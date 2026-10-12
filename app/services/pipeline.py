import logging
import shutil

from app.config import Settings
from app.database import new_id, session_scope, utcnow
from app.ffmpeg_util import MediaError, probe_duration
from app.models import Character, Episode, Scene, ScriptLine
from app.paths import episode_dir, from_rel, remove_stored, to_rel
from app.providers.factory import build_image, build_llm, build_tts, close_provider
from app.providers.tts import SynthRequest
from app.providers.voices import assign_default_voice
from app.services.audio import normalize_wav, stitch_wavs
from app.services.heuristics import stable_appearance
from app.services.scenes import plan_scenes
from app.services.script import breakdown_text, line_kind
from app.services.video import compose_episode
from app.timeline import line_spans

logger = logging.getLogger(__name__)


def run_pipeline(session_factory, settings: Settings, episode_id: str, progress) -> None:
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        has_lines = bool(episode.lines)
    if not has_lines:
        progress(5, "正在拆解剧本")
        run_breakdown(session_factory, settings, episode_id, progress)
    progress(30, "正在合成配音")
    run_tts(session_factory, settings, episode_id, progress)
    progress(60, "正在生成画面")
    run_illustrate(session_factory, settings, episode_id, progress)
    progress(85, "正在合成视频")
    run_video(session_factory, settings, episode_id, progress)


def run_breakdown(session_factory, settings: Settings, episode_id: str, progress) -> None:
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        source = episode.source_text or ""
        if not source.strip():
            raise RuntimeError("这一集没有正文")
    progress(15, "正在阅读正文")
    llm = build_llm(settings)
    try:
        draft = breakdown_text(source, llm)
    finally:
        close_provider(llm)
    progress(35, "正在写入台词")
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        _clear_generated(db, settings, episode, drop_lines=True, drop_scenes=True)
        _ensure_characters(db, settings, episode.novel, draft["characters"])
        for position, line in enumerate(draft["lines"]):
            db.add(
                ScriptLine(
                    id=new_id(),
                    episode_id=episode.id,
                    position=position,
                    speaker=line["speaker"],
                    kind=line_kind(line["speaker"]),
                    text=line["text"],
                )
            )
        episode.status = "scripted"
        episode.error = None
        episode.updated_at = utcnow()
        episode.novel.updated_at = utcnow()


def run_tts(session_factory, settings: Settings, episode_id: str, progress) -> None:
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        lines = list(episode.lines)
        if not lines:
            raise RuntimeError("还没有台词，请先拆解剧本")
        characters = {item.name: item for item in episode.novel.characters}
        missing = sorted({line.speaker for line in lines if not characters.get(line.speaker) or not characters[line.speaker].voice_id})
        if missing:
            raise RuntimeError("这些角色还没有音色：" + "、".join(missing))
        plan = [
            {
                "line_id": line.id,
                "speaker": line.speaker,
                "character_id": characters[line.speaker].id,
                "voice_id": characters[line.speaker].voice_id,
                "text": line.text,
            }
            for line in lines
        ]
        novel_id = episode.novel_id
        work = episode_dir(settings, novel_id, episode.id)
        old_mix = episode.audio_path
        old_video = episode.video_path
        old_srt = episode.subtitle_path
    for stale in (old_mix, old_video, old_srt):
        remove_stored(settings, stale)
    tts = build_tts(settings)
    wav_paths = []
    durations = []
    try:
        line_dir = work / "lines"
        line_dir.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(plan):
            progress(10 + int(70 * index / max(len(plan), 1)), f"正在合成第 {index + 1}/{len(plan)} 句")
            raw = line_dir / f"{item['line_id']}.raw"
            wav = line_dir / f"{item['line_id']}.wav"
            tts.synthesize_line(
                SynthRequest(item["character_id"], item["speaker"], item["voice_id"], item["text"]),
                raw,
            )
            normalize_wav(raw, wav)
            if raw.exists() and raw.resolve() != wav.resolve():
                raw.unlink()
            durations.append(probe_duration(wav))
            wav_paths.append(wav)
    finally:
        close_provider(tts)
    progress(88, "正在拼接整集配音")
    mix = work / "episode.wav"
    stitch_wavs(wav_paths, mix)
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        by_id = {line.id: line for line in episode.lines}
        for item, wav, duration in zip(plan, wav_paths, durations):
            line = by_id[item["line_id"]]
            line.audio_path = to_rel(settings, wav)
            line.duration_sec = duration
        remove_stored(settings, episode.video_path)
        remove_stored(settings, episode.subtitle_path)
        episode.audio_path = to_rel(settings, mix)
        episode.video_path = None
        episode.subtitle_path = None
        episode.status = _status(episode)
        episode.error = None
        episode.updated_at = utcnow()


def run_illustrate(session_factory, settings: Settings, episode_id: str, progress) -> None:
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        lines = list(episode.lines)
        if not lines:
            raise RuntimeError("还没有台词，请先拆解剧本")
        for character in episode.novel.characters:
            if character.name != "旁白" and not character.description:
                character.description = stable_appearance(character.name)
        line_payload = [{"speaker": line.speaker, "text": line.text} for line in lines]
        character_payload = [
            {"name": character.name, "description": character.description} for character in episode.novel.characters
        ]
        novel_id = episode.novel_id
        work = episode_dir(settings, novel_id, episode.id)
    progress(20, "正在划分场景")
    llm = build_llm(settings)
    try:
        scenes = plan_scenes(line_payload, character_payload, llm)
    finally:
        close_provider(llm)
    image_provider = build_image(settings)
    rendered: list[tuple[dict, object]] = []
    try:
        scene_dir = work / "scenes"
        if scene_dir.exists():
            shutil.rmtree(scene_dir)
        scene_dir.mkdir(parents=True, exist_ok=True)
        for index, scene in enumerate(scenes):
            progress(30 + int(60 * index / max(len(scenes), 1)), f"正在绘制场景 {index + 1}/{len(scenes)}")
            dest = scene_dir / f"{index:02d}.png"
            image_provider.generate(scene["prompt"], dest, settings.video_width, settings.video_height)
            rendered.append((scene, dest))
    finally:
        close_provider(image_provider)
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        for scene in list(episode.scenes):
            remove_stored(settings, scene.image_path)
            db.delete(scene)
        db.flush()
        for index, (scene, dest) in enumerate(rendered):
            db.add(
                Scene(
                    id=new_id(),
                    episode_id=episode.id,
                    position=index,
                    start_line=scene["start_line"],
                    end_line=scene["end_line"],
                    summary=scene["summary"],
                    prompt=scene["prompt"],
                    image_path=to_rel(settings, dest),
                )
            )
        remove_stored(settings, episode.video_path)
        remove_stored(settings, episode.subtitle_path)
        episode.video_path = None
        episode.subtitle_path = None
        episode.status = "illustrated"
        episode.error = None
        episode.updated_at = utcnow()


def run_video(session_factory, settings: Settings, episode_id: str, progress) -> None:
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        lines = list(episode.lines)
        scenes = list(episode.scenes)
        if not lines:
            raise RuntimeError("还没有台词，请先拆解剧本")
        if any(line.audio_path is None or line.duration_sec is None for line in lines):
            raise RuntimeError("还有台词没有配音，请先合成配音")
        if not scenes or any(scene.image_path is None for scene in scenes):
            raise RuntimeError("还有场景没有画面，请先生成画面")
        if not _scenes_cover(scenes, len(lines)):
            raise RuntimeError("分镜和当前台词对不上，请重新生成画面")
        durations = [float(line.duration_sec or 0) for line in lines]
        spans = line_spans(durations)
        cues = []
        for line, span in zip(lines, spans):
            label = line.text if line.kind == "narrator" else f"{line.speaker}：{line.text}"
            cues.append((span.start, span.end, label))
        images = []
        for scene in scenes:
            resolved = from_rel(settings, scene.image_path)
            if resolved is None or not resolved.is_file():
                raise RuntimeError("场景图片丢失，请重新生成画面")
            images.append((scene.start_line, scene.end_line, resolved))
        audio = from_rel(settings, episode.audio_path)
        if audio is None or not audio.is_file():
            raise RuntimeError("整集配音丢失，请重新合成配音")
        work = episode_dir(settings, episode.novel_id, episode.id)
        output = work / "episode.mp4"
    progress(40, "正在铺画面、字幕和声音")
    try:
        srt = compose_episode(settings, work, images, durations, cues, audio, output)
    except MediaError as exc:
        raise RuntimeError(str(exc)) from exc
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        episode.video_path = to_rel(settings, output)
        episode.subtitle_path = to_rel(settings, srt)
        episode.status = "rendered"
        episode.error = None
        episode.updated_at = utcnow()
    progress(96, "成片已写好")


def _episode(db, episode_id: str) -> Episode:
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise RuntimeError("分集不存在")
    return episode


def _ensure_characters(db, settings: Settings, novel, characters: list[dict]) -> None:
    existing = {character.name: character for character in novel.characters}
    ordered: list[str] = []
    for name in ["旁白", *[item["name"] for item in characters]]:
        if name and name not in ordered:
            ordered.append(name)
    info = {item["name"]: item for item in characters}
    role_index = 0
    mock_voices = settings.resolved_tts() == "mock"
    for name in ordered:
        payload = info.get(name, {})
        character = existing.get(name)
        if character is None:
            character = Character(
                id=new_id(),
                novel_id=novel.id,
                name=name,
                description="",
                voice_id="",
                voice_name="",
                is_narrator=name == "旁白",
                created_at=utcnow(),
            )
            db.add(character)
            existing[name] = character
        if name != "旁白" and not character.description:
            character.description = (payload.get("description") or "").strip() or stable_appearance(name)
        if mock_voices and not character.voice_id:
            voice = assign_default_voice(character.is_narrator or name == "旁白", -1 if name == "旁白" else role_index)
            character.voice_id = voice.id
            character.voice_name = voice.name
        if name != "旁白":
            role_index += 1
    db.flush()


def _clear_generated(db, settings: Settings, episode: Episode, drop_lines: bool, drop_scenes: bool) -> None:
    for line in list(episode.lines):
        remove_stored(settings, line.audio_path)
        line.audio_path = None
        line.duration_sec = None
        if drop_lines:
            db.delete(line)
    for scene in list(episode.scenes):
        remove_stored(settings, scene.image_path)
        if drop_scenes:
            db.delete(scene)
    remove_stored(settings, episode.audio_path)
    remove_stored(settings, episode.video_path)
    remove_stored(settings, episode.subtitle_path)
    episode.audio_path = None
    episode.video_path = None
    episode.subtitle_path = None
    if drop_lines or drop_scenes:
        db.flush()


def _scenes_cover(scenes: list[Scene], line_count: int) -> bool:
    expected = 0
    for scene in sorted(scenes, key=lambda item: item.position):
        if scene.start_line != expected:
            return False
        if scene.end_line < scene.start_line:
            return False
        expected = scene.end_line + 1
    return expected == line_count


def _status(episode: Episode) -> str:
    lines = list(episode.lines)
    if episode.video_path:
        return "rendered"
    if episode.scenes and all(scene.image_path for scene in episode.scenes):
        return "illustrated"
    if lines and all(line.audio_path for line in lines):
        return "voiced"
    if lines:
        return "scripted"
    return "imported"


def invalidate_after_line_edit(settings: Settings, episode: Episode, line: ScriptLine | None = None) -> None:
    if line is not None:
        remove_stored(settings, line.audio_path)
        line.audio_path = None
        line.duration_sec = None
    remove_stored(settings, episode.audio_path)
    remove_stored(settings, episode.video_path)
    remove_stored(settings, episode.subtitle_path)
    episode.audio_path = None
    episode.video_path = None
    episode.subtitle_path = None
    episode.status = _status(episode)
    episode.updated_at = utcnow()


def drop_scenes(settings: Settings, episode: Episode) -> None:
    for scene in list(episode.scenes):
        remove_stored(settings, scene.image_path)
        episode.scenes.remove(scene)
    remove_stored(settings, episode.video_path)
    remove_stored(settings, episode.subtitle_path)
    episode.video_path = None
    episode.subtitle_path = None
    episode.status = _status(episode)
