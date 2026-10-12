import re
from dataclasses import dataclass

from app.database import new_id, utcnow
from app.models import Episode, Novel

CHAPTER_RE = re.compile(
    r"^第\s*[0-9０-９零〇一二三四五六七八九十百千两]+\s*[章节集回节话]"
    r"(?:[ \t]*[:：.\-—、][ \t]*|[ \t]+)?"
    r".*$"
)
EN_RE = re.compile(r"^(?:chapter|episode)\s+\d+\b.*$", re.IGNORECASE)


@dataclass
class ParsedEpisode:
    title: str
    text: str


@dataclass
class SplitResult:
    episodes: list[ParsedEpisode]
    suggested_title: str | None = None


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def normalize_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    return text.strip()


def heading_title(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    plain = re.sub(r"^#{1,3}[ \t]+", "", stripped).strip()
    if CHAPTER_RE.match(plain) or EN_RE.match(plain):
        return plain[:120]
    if re.match(r"^#{1,3}[ \t]+\S", stripped):
        return plain[:120]
    return None


def split_episodes(raw: str) -> SplitResult:
    text = normalize_text(raw)
    if not text:
        raise ValueError("正文是空的")
    lines = text.split("\n")
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        title = heading_title(line)
        if title:
            headings.append((index, title))
    if not headings:
        return SplitResult([ParsedEpisode("第1集", text)])

    episodes: list[ParsedEpisode] = []
    suggested: str | None = None
    preamble = "\n".join(lines[: headings[0][0]]).strip()
    if preamble:
        if len(preamble) <= 40 and "\n" not in preamble:
            suggested = preamble
        else:
            episodes.append(ParsedEpisode("序章", preamble))

    for index, (start, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if not body:
            continue
        episodes.append(ParsedEpisode(title, body))
    if not episodes:
        raise ValueError("没有解析到正文")
    return SplitResult(episodes, suggested)


def create_novel(db, title: str | None, text: str, filename: str | None = None) -> Novel:
    try:
        split = split_episodes(text)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    novel = Novel(id=new_id(), title=_choose_title(title, filename, split), created_at=utcnow(), updated_at=utcnow())
    db.add(novel)
    for position, episode in enumerate(split.episodes):
        db.add(
            Episode(
                id=new_id(),
                novel_id=novel.id,
                position=position,
                title=episode.title,
                source_text=episode.text,
                status="imported",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    db.flush()
    return novel


def _choose_title(title: str | None, filename: str | None, split: SplitResult) -> str:
    if title and title.strip():
        return title.strip()[:80]
    if filename:
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
        if stem and stem.lower() not in {"upload", "novel", "text"}:
            return stem[:80]
    if split.suggested_title:
        return split.suggested_title[:80]
    if split.episodes:
        return split.episodes[0].title[:80]
    return "未命名作品"
