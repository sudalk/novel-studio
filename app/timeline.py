from dataclasses import dataclass


GAP_SEC = 0.12


@dataclass(frozen=True)
class Span:
    start: float
    end: float


def line_spans(durations: list[float], gap: float = GAP_SEC) -> list[Span]:
    spans: list[Span] = []
    cursor = 0.0
    last = len(durations) - 1
    for index, duration in enumerate(durations):
        start = cursor
        end = cursor + max(0.05, duration)
        spans.append(Span(start, end))
        cursor = end
        if index != last:
            cursor += gap
    return spans


def total_duration(durations: list[float], gap: float = GAP_SEC) -> float:
    if not durations:
        return 0.0
    return sum(max(0.05, item) for item in durations) + gap * (len(durations) - 1)


def scene_durations(ranges: list[tuple[int, int]], durations: list[float], gap: float = GAP_SEC) -> list[float]:
    """ranges 是含端点的台词下标。场景之间的静音算在前一个场景里，保证视频总长等于音频。"""
    if not ranges:
        return []
    spans = line_spans(durations, gap)
    output: list[float] = []
    last_line = len(durations) - 1
    for start_line, end_line in ranges:
        start = spans[start_line].start
        end = spans[end_line].end
        if end_line < last_line:
            end += gap
        output.append(max(0.05, end - start))
    return output
