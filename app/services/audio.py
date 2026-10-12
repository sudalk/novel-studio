from pathlib import Path

from app.ffmpeg_util import ffmpeg_run, probe_duration
from app.timeline import GAP_SEC


def normalize_wav(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dest.resolve():
        temporary = dest.with_suffix(".tmp.wav")
        _to_wav(src, temporary)
        temporary.replace(dest)
        return
    _to_wav(src, dest)


def _to_wav(src: Path, dest: Path) -> None:
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


SEGMENT_PAUSE = 0.30
SEGMENT_CROSSFADE = 0.04


def slice_wav(src: Path, dest: Path, start: float, end: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end - start)
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{duration:.3f}",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def mix_segments(paths: list[Path], dest: Path, pause: float = SEGMENT_PAUSE) -> float:
    """段与段之间留短停顿，边缘淡入淡出，再统一响度，避免拼接处的爆音。"""
    if not paths:
        raise RuntimeError("没有可以拼接的音频")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        _loudnorm(paths[0], dest)
        return probe_duration(dest)
    cross = SEGMENT_CROSSFADE
    shortest = min(probe_duration(path) for path in paths)
    cross = min(cross, max(0.01, shortest / 4))
    silence = dest.parent / "_pause.wav"
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{pause + cross:.3f}",
            "-c:a",
            "pcm_s16le",
            str(silence),
        ]
    )
    args = ["ffmpeg", "-y"]
    for path in paths[:-1]:
        args.extend(["-i", str(path), "-i", str(silence)])
    args.extend(["-i", str(paths[-1])])
    filters: list[str] = []
    previous = "[0:a]"
    for index in range(1, len(paths) * 2 - 1):
        output = f"[x{index}]"
        filters.append(f"{previous}[{index}:a]acrossfade=d={cross:.3f}:c1=tri:c2=tri{output}")
        previous = output
    filters.append(f"{previous}loudnorm=I=-16:TP=-1.5:LRA=11[out]")
    mixed = dest.with_name("_mix.wav")
    args.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(mixed)])
    ffmpeg_run(args)
    mixed.replace(dest)
    return probe_duration(dest)


def _loudnorm(src: Path, dest: Path) -> None:
    if src.resolve() == dest.resolve():
        temporary = dest.with_suffix(".norm.wav")
        _loudnorm(src, temporary)
        temporary.replace(dest)
        return
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-af",
            "afade=t=in:st=0:d=0.012,areverse,afade=t=in:st=0:d=0.012,areverse,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def stitch_wavs(paths: list[Path], dest: Path, gap: float = GAP_SEC) -> float:
    if not paths:
        raise RuntimeError("没有可以拼接的音频")
    dest.parent.mkdir(parents=True, exist_ok=True)
    silence = dest.parent / "_gap.wav"
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{gap:.3f}",
            "-c:a",
            "pcm_s16le",
            str(silence),
        ]
    )
    list_file = dest.parent / "_audio_concat.txt"
    lines: list[str] = []
    for index, path in enumerate(paths):
        lines.append(f"file '{_escape(path)}'")
        if index != len(paths) - 1:
            lines.append(f"file '{_escape(silence)}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ffmpeg_run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(dest)])
    return probe_duration(dest)


def _escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")
