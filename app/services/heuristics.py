import hashlib
import json
import re

from app.providers.errors import ProviderError

PRONOUNS = {"他", "她", "我", "你", "它", "他们", "她们", "我们", "大家", "众人"}

DIALOGUE_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z·]{1,6}?)"
    r"(?:轻声|低声|冷声|沉声|微笑着|笑着|淡淡)?"
    r"(?:说道|问道|喊道|笑道|答道|回道|喝道|冷哼道|说|问|喊|道)"
    r"[：:]?\s*"
    r"[「“\"](?P<text>[^」”\"]+)[」”\"]"
)

QUOTED_RE = re.compile(r"^[「“\"](.+)[」”\"]$")

_HAIR = ("乌黑长发", "束起的黑发", "微卷短发", "墨色高马尾")
_CLOTHES = ("月白长衫", "深青布袍", "烟灰短褐", "暗红罩衣")
_FEATURES = ("眉眼清淡", "眉骨分明", "眼尾微垂", "鼻梁挺直")


def stable_appearance(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return (
        f"{name}，约二十出头，{_HAIR[digest[0] % len(_HAIR)]}，"
        f"{_CLOTHES[digest[1] % len(_CLOTHES)]}，{_FEATURES[digest[2] % len(_FEATURES)]}，面容固定"
    )


def heuristic_script(text: str) -> dict:
    last_speaker = ""
    lines: list[dict] = []
    for paragraph in _paragraphs(text):
        events, last_speaker = _parse_paragraph(paragraph, last_speaker)
        lines.extend({"speaker": speaker, "text": content} for speaker, content in events)
    if not lines:
        raise ProviderError("没有从正文里拆出台词")
    names: list[str] = ["旁白"]
    for line in lines:
        if line["speaker"] not in names:
            names.append(line["speaker"])
    characters = []
    for name in names:
        characters.append(
            {
                "name": name,
                "is_narrator": name == "旁白",
                "description": "" if name == "旁白" else stable_appearance(name),
            }
        )
    return {"characters": characters, "lines": lines}


def heuristic_scenes(user: str) -> dict:
    try:
        data = json.loads(user)
    except json.JSONDecodeError as exc:
        raise ProviderError("分镜输入不是 JSON") from exc
    lines = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(lines, list) or not lines:
        raise ProviderError("分镜输入缺少台词")
    looks = {}
    for item in data.get("characters") or []:
        if isinstance(item, dict) and item.get("name"):
            looks[str(item["name"])] = str(item.get("description") or "")
    size = 2 if len(lines) <= 4 else 3 if len(lines) <= 12 else 5
    scenes: list[dict] = []
    index = 0
    while index < len(lines):
        end = min(len(lines) - 1, index + size - 1)
        scenes.append(_scene_from_lines(lines, looks, index, end))
        index = end + 1
    if len(scenes) >= 2 and scenes[-1]["end_line"] == scenes[-1]["start_line"]:
        previous = scenes[-2]
        merged_end = scenes[-1]["end_line"]
        scenes[-2] = _scene_from_lines(lines, looks, previous["start_line"], merged_end)
        scenes.pop()
    return {"scenes": scenes}


def _scene_from_lines(lines: list[dict], looks: dict, start: int, end: int) -> dict:
    excerpt = "".join(str(lines[i].get("text") or "") for i in range(start, end + 1))
    speakers: list[str] = []
    for i in range(start, end + 1):
        name = str(lines[i].get("speaker") or "旁白")
        if name != "旁白" and name not in speakers:
            speakers.append(name)
    who = "、".join(speakers) if speakers else "环境"
    summary = f"{who}。{excerpt[:42]}"
    del looks
    return {"start_line": start, "end_line": end, "summary": summary, "prompt": summary}


def _paragraphs(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]


def _parse_paragraph(paragraph: str, last_speaker: str) -> tuple[list[tuple[str, str]], str]:
    events: list[tuple[str, str]] = []
    cursor = 0
    matched = False
    for match in DIALOGUE_RE.finditer(paragraph):
        matched = True
        before = _clean(paragraph[cursor : match.start()])
        if before:
            events.extend(("旁白", part) for part in _split_long(before))
        name = match.group("name")
        if name in PRONOUNS:
            name = last_speaker or "旁白"
        text = match.group("text").strip()
        if text:
            if name != "旁白":
                last_speaker = name
            events.append((name, text))
        cursor = match.end()
    if matched:
        rest = _clean(paragraph[cursor:])
        if rest:
            events.extend(("旁白", part) for part in _split_long(rest))
        return events, last_speaker
    quoted = QUOTED_RE.match(paragraph.strip())
    if quoted:
        speaker = last_speaker or "旁白"
        return [(speaker, quoted.group(1).strip())], last_speaker
    narration = _clean(paragraph)
    if narration:
        events.extend(("旁白", part) for part in _split_long(narration))
    return events, last_speaker


def _clean(text: str) -> str:
    text = text.strip().strip("「」“”\"")
    text = text.strip()
    if not re.sub(r"[，,。！？!?；;：:、\s]", "", text):
        return ""
    return text


def _split_long(text: str, limit: int = 80) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if buf and len(buf) + len(part) > limit:
            chunks.append(buf)
            buf = part
        else:
            buf = f"{buf}{part}" if buf else part
        while len(buf) > limit:
            chunks.append(buf[:limit])
            buf = buf[limit:]
    if buf:
        chunks.append(buf)
    return [chunk for chunk in chunks if chunk.strip()]
