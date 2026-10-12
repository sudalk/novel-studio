import math
from pathlib import Path

from app.config import Settings
from app.ffmpeg_util import ffmpeg_run
from app.fonts import find_cjk_font
from app.timeline import scene_durations


def write_srt(path: Path, cues: list[tuple[float, float, str]], width: int = 18) -> None:
    blocks: list[str] = []
    for index, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{index}\n{_ts(start)} --> {_ts(end)}\n{_wrap_subtitle(text, width)}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")


def compose_episode(
    settings: Settings,
    work_dir: Path,
    images: list[tuple[int, int, Path]],
    durations: list[float],
    cues: list[tuple[float, float, str]],
    audio_path: Path,
    output: Path,
    scene_lengths: list[float] | None = None,
) -> Path:
    """images 是 (start_line, end_line, image_path)。"""
    if len(images) == 0:
        raise RuntimeError("没有画面可以合成")
    if scene_lengths is None:
        ranges = [(start, end) for start, end, _image in images]
        scene_lengths = scene_durations(ranges, durations)
    clip_dir = work_dir / "_clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for index, ((_start, _end, image), length) in enumerate(zip(images, scene_lengths)):
        clip = clip_dir / f"{index:02d}.mp4"
        _ken_burns(settings, image, clip, length, index)
        clips.append(clip)
    silent = work_dir / "_silent.mp4"
    _concat_videos(clips, silent, settings)
    srt = work_dir / "episode.srt"
    wrap = 12 if settings.video_height > settings.video_width else 18
    write_srt(srt, cues, width=wrap)
    output.parent.mkdir(parents=True, exist_ok=True)
    _mux(settings, silent, audio_path, srt, output)
    return srt


def _ken_burns(settings: Settings, image: Path, dest: Path, duration: float, scene_index: int) -> None:
    width = _even(settings.video_width)
    height = _even(settings.video_height)
    fps = max(1, settings.video_fps)
    frames = max(2, math.ceil(duration * fps) + fps)
    video_filter = _zoompan(scene_index, frames, width, height, fps)
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(image),
            "-vf",
            video_filter,
            "-t",
            f"{duration:.3f}",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            settings.video_preset,
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(dest),
        ]
    )


def _zoompan(scene_index: int, frames: int, width: int, height: int, fps: int) -> str:
    denom = max(frames - 1, 1)
    mode = scene_index % 4
    if mode == 0:
        zoom = "min(1.0+0.0018*on,1.18)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif mode == 1:
        zoom = "1.12"
        x_expr = f"(iw-iw/zoom)*on/{denom}"
        y_expr = "ih/2-(ih/zoom/2)"
    elif mode == 2:
        zoom = "max(1.18-0.0018*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        zoom = "1.12"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*on/{denom}"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps}"
    )


def _concat_videos(clips: list[Path], dest: Path, settings: Settings) -> None:
    list_file = dest.parent / "_video_concat.txt"
    lines = [f"file '{str(path.resolve()).replace(chr(39), chr(39) + '\\' + chr(39) + chr(39))}'" for path in clips]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-preset",
            settings.video_preset,
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(dest),
        ]
    )


def _mux(settings: Settings, video: Path, audio: Path, srt: Path, dest: Path) -> None:
    font = find_cjk_font()
    subtitle_filter = _subtitle_filter(srt, font, settings)
    ffmpeg_run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            settings.video_preset,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )


def _subtitle_filter(srt: Path, font, settings: Settings) -> str:
    escaped = _filter_path(srt)
    vertical = settings.video_height > settings.video_width
    size = max(36, settings.video_height // 28) if vertical else 22
    margin = max(72, settings.video_height // 12) if vertical else 36
    style = (
        f"FontSize={size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,"
        f"BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={margin}"
    )
    if font is not None:
        style = f"FontName={font.family}," + style
        fontsdir = _filter_path(font.file.parent)
        return f"subtitles={escaped}:fontsdir={fontsdir}:charenc=UTF-8:force_style='{style}'"
    return f"subtitles={escaped}:charenc=UTF-8:force_style='{style}'"


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _wrap_subtitle(text: str, width: int = 20) -> str:
    clean = text.replace("{", "｛").replace("}", "｝").replace("\n", "")
    if len(clean) <= width:
        return clean or " "
    lines: list[str] = []
    buf = ""
    for char in clean:
        buf += char
        if len(buf) >= width and char in "，。！？、；：,.!?":
            lines.append(buf)
            buf = ""
        elif len(buf) >= width + 8:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    return "\n".join(lines) or " "


def _ts(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _even(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1
