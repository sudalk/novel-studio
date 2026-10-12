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
