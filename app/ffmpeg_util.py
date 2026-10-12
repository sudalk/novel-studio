import shutil
import subprocess
import wave
from pathlib import Path


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise MediaError("未找到 ffmpeg 或 ffprobe，请先安装 ffmpeg")


def ffmpeg_run(args: list[str]) -> None:
    require_ffmpeg()
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1800:]
        raise MediaError(f"ffmpeg 失败：{tail}")


def probe_duration(path: Path) -> float:
    if shutil.which("ffprobe"):
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return max(0.05, float(proc.stdout.strip()))
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate() or 1
        return max(0.05, frames / float(rate))


def probe_streams(path: Path) -> dict:
    import json

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise MediaError(proc.stderr[-500:])
    return json.loads(proc.stdout)
