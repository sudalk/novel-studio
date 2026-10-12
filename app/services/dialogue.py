"""把台词收成最多两位说话人的对话段，方便一次交给 moss-ttsd。"""

NARRATOR = "旁白"


def group_dialogue(lines: list[dict]) -> list[list[dict]]:
    """连续台词合成一段。一段最多两个说话人。

    旁白如果是在给下一位新角色起头，就从这句旁白另起一段，让旁白和新角色待在同一次合成里。
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    speakers: list[str] = []
    for index, line in enumerate(lines):
        if current and _starts_new_pair(speakers, lines[index:]):
            groups.append(current)
            current = []
            speakers = []
        speaker = str(line.get("speaker") or NARRATOR)
        if speaker not in speakers:
            speakers.append(speaker)
        current.append(line)
    if current:
        groups.append(current)
    return groups


def _starts_new_pair(speakers: list[str], upcoming: list[dict]) -> bool:
    nxt = str(upcoming[0].get("speaker") or NARRATOR)
    if nxt in speakers:
        if nxt == NARRATOR and len(upcoming) > 1:
            after = str(upcoming[1].get("speaker") or NARRATOR)
            if after not in speakers and after != NARRATOR:
                return True
        return False
    return len(speakers) >= 2


def estimate_timings(weights: list[float], total: float) -> list[tuple[float, float]]:
    """没有上游时间戳时，按字数（或已有时长）把整段音频分给每一句。"""
    clean = [max(1.0, float(weight)) for weight in weights] or [1.0]
    total = max(0.05 * len(clean), float(total))
    whole = sum(clean)
    cursor = 0.0
    spans: list[tuple[float, float]] = []
    for index, weight in enumerate(clean):
        if index == len(clean) - 1:
            end = total
        else:
            end = min(total, cursor + total * weight / whole)
            if end < cursor + 0.05:
                end = min(total, cursor + 0.05)
        spans.append((cursor, end))
        cursor = end
    return spans
