import logging
import time

from app.config import Settings
from app.database import new_id, session_scope, utcnow
from app.ffmpeg_util import MediaError, probe_duration
from app.models import Character, Episode, Scene, ScriptLine
from app.paths import episode_dir, from_rel, remove_stored, to_rel
from app.providers.factory import build_image, build_llm, build_tts, close_provider
from app.providers.tts import SynthRequest
from app.providers.voices import PRESET_VOICES
from app.services.audio import SEGMENT_PAUSE, mix_segments, normalize_wav, slice_wav
from app.services.casting import apply_cast
from app.services.dialogue import estimate_timings, group_dialogue
from app.services.heuristics import stable_appearance
from app.services.image_prompt import inter_image_pause
from app.services.scenes import missing_cache_keys, plan_scenes, stamp_cache_keys
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
    plan, work = _tts_plan(session_factory, settings, episode_id)
    _render_dialogue(settings, plan, work, progress, only_group=None)
    _store_dialogue(session_factory, settings, episode_id, plan, work)


def run_line_tts(session_factory, settings: Settings, episode_id: str, line_id: str, progress) -> None:
    plan, work = _tts_plan(session_factory, settings, episode_id)
    groups = group_dialogue(plan)
    only = next(index for index, group in enumerate(groups) if any(item["line_id"] == line_id for item in group))
    progress(20, "正在重配这一段对话")
    _render_dialogue(settings, plan, work, progress, only_group=only)
    _store_dialogue(session_factory, settings, episode_id, plan, work)


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
        style = episode.novel.style or ""
        cap = _image_cap(settings, episode.novel)
        work = episode_dir(settings, novel_id, episode.id)
        cached: dict[str, tuple[object, str | None]] = {}
        for scene in episode.scenes:
            if not scene.cache_key or not scene.image_path or scene.cache_key in cached:
                continue
            path = from_rel(settings, scene.image_path)
            if path is not None and path.is_file():
                cached[scene.cache_key] = (path, scene.source_url)
    progress(20, "正在挑选关键画面")
    llm = build_llm(settings)
    try:
        scenes = plan_scenes(
            line_payload,
            character_payload,
            llm,
            style=style,
            aspect=settings.resolved_image_aspect(),
            max_images=cap,
        )
    finally:
        close_provider(llm)
    try:
        size = settings.resolved_image_size()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    stamp_cache_keys(scenes, size)
    needed = missing_cache_keys(scenes, set(cached))
    image_provider = build_image(settings)
    fresh: dict[str, tuple[object, str | None]] = {}
    scene_dir = work / "scenes"
    scene_dir.mkdir(parents=True, exist_ok=True)
    try:
        prompts = {scene["cache_key"]: scene["prompt"] for scene in scenes if scene["key"] and scene.get("prompt")}
        for index, cache_key in enumerate(needed):
            pause = inter_image_pause(index, image_provider.name, settings.image_gap_sec)
            if pause:
                progress(30 + int(40 * index / max(len(needed), 1)), f"等待 {pause:.0f} 秒后绘制下一张")
                time.sleep(pause)
            progress(30 + int(40 * index / max(len(needed), 1)), f"正在绘制关键画面 {index + 1}/{len(needed)}")
            dest = scene_dir / f"{cache_key[:24]}.png"
            source_url = image_provider.generate(
                prompts[cache_key], dest, settings.video_width, settings.video_height
            )
            fresh[cache_key] = (dest, source_url)
    finally:
        close_provider(image_provider)
    if not needed:
        progress(70, "画面没有变化，沿用已有图片")
    rendered: list[tuple[dict, object, str | None, str]] = []
    used: set[object] = set()
    for scene in scenes:
        cache_key = scene["cache_key"]
        if cache_key in fresh:
            dest, source_url = fresh[cache_key]
        elif cache_key in cached:
            dest, source_url = cached[cache_key]
        else:
            raise RuntimeError("关键画面没有生成")
        used.add(dest.resolve())
        rendered.append((scene, dest, source_url, cache_key))
    for stale in scene_dir.glob("*.png"):
        if stale.resolve() not in used:
            stale.unlink(missing_ok=True)
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        for scene in list(episode.scenes):
            db.delete(scene)
        db.flush()
        for index, (scene, dest, source_url, cache_key) in enumerate(rendered):
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
                    source_url=source_url,
                    key_visual=bool(scene["key"]),
                    cache_key=cache_key,
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
        timed = all(line.start_sec is not None for line in lines)
        if timed:
            cues = []
            for line in lines:
                label = line.text if line.kind == "narrator" else f"{line.speaker}：{line.text}"
                start = float(line.start_sec or 0)
                cues.append((start, start + float(line.duration_sec or 0), label))
            scene_lengths = None
        else:
            spans = line_spans(durations)
            cues = []
            for line, span in zip(lines, spans):
                label = line.text if line.kind == "narrator" else f"{line.speaker}：{line.text}"
                cues.append((span.start, span.end, label))
            scene_lengths = None
        images = []
        for scene in scenes:
            resolved = from_rel(settings, scene.image_path)
            if resolved is None or not resolved.is_file():
                raise RuntimeError("场景图片丢失，请重新生成画面")
            images.append((scene.start_line, scene.end_line, resolved))
        audio = from_rel(settings, episode.audio_path)
        if audio is None or not audio.is_file():
            raise RuntimeError("整集配音丢失，请重新合成配音")
        if timed:
            scene_lengths = _scene_lengths_from_starts(scenes, lines, probe_duration(audio))
        work = episode_dir(settings, episode.novel_id, episode.id)
        output = work / "episode.mp4"
    progress(40, "正在铺画面、字幕和声音")
    try:
        srt = compose_episode(
            settings, work, images, durations, cues, audio, output, scene_lengths=scene_lengths
        )
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


def _image_cap(settings: Settings, novel) -> int | None:
    raw = getattr(novel, "image_max", None)
    if raw is not None and int(raw) > 0:
        return int(raw)
    return settings.resolved_image_cap()


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
                voice_locked=False,
                gender="",
                age="",
                personality="",
                is_narrator=name == "旁白",
                created_at=utcnow(),
            )
            db.add(character)
            existing[name] = character
        if name != "旁白" and not character.description:
            character.description = (payload.get("description") or "").strip() or stable_appearance(name)
    db.flush()
    voices = _voice_catalog(settings)
    apply_cast(list(existing.values()), info, voices)


def _clear_generated(db, settings: Settings, episode: Episode, drop_lines: bool, drop_scenes: bool) -> None:
    for line in list(episode.lines):
        remove_stored(settings, line.audio_path)
        line.audio_path = None
        line.duration_sec = None
        line.start_sec = None
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


def _tts_plan(session_factory, settings: Settings, episode_id: str):
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        lines = list(episode.lines)
        if not lines:
            raise RuntimeError("还没有台词，请先拆解剧本")
        characters = {item.name: item for item in episode.novel.characters}
        missing = sorted(
            {line.speaker for line in lines if not characters.get(line.speaker) or not characters[line.speaker].voice_id}
        )
        if missing:
            raise RuntimeError("这些角色还没有音色：" + "、".join(missing))
        plan = [
            {
                "line_id": line.id,
                "speaker": line.speaker,
                "character_id": characters[line.speaker].id,
                "voice_id": characters[line.speaker].voice_id,
                "text": line.text,
                "duration_sec": line.duration_sec,
            }
            for line in lines
        ]
        work = episode_dir(settings, episode.novel_id, episode.id)
        for stale in (episode.audio_path, episode.video_path, episode.subtitle_path):
            remove_stored(settings, stale)
    return plan, work


def _render_dialogue(settings: Settings, plan: list[dict], work, progress, only_group: int | None) -> None:
    groups = group_dialogue(plan)
    seg_dir = work / "segments"
    line_dir = work / "lines"
    seg_dir.mkdir(parents=True, exist_ok=True)
    line_dir.mkdir(parents=True, exist_ok=True)
    seg_paths = [seg_dir / f"{index:02d}.wav" for index in range(len(groups))]
    tts = build_tts(settings)
    calls = 0
    try:
        for index, group in enumerate(groups):
            dest = seg_paths[index]
            if only_group is not None and index != only_group and dest.is_file():
                _fill_local_from_weights(group, probe_duration(dest))
                continue
            if calls and tts.name != "mock" and settings.moss_gap_sec > 0:
                progress(30, f"等待 {settings.moss_gap_sec:.0f} 秒后合成下一段")
                time.sleep(settings.moss_gap_sec)
            calls += 1
            progress(15 + int(60 * index / max(len(groups), 1)), f"正在合成第 {index + 1}/{len(groups)} 段")
            raw = seg_dir / f"{index:02d}.raw"
            requests = [
                SynthRequest(item["character_id"], item["speaker"], item["voice_id"], item["text"]) for item in group
            ]
            timings = tts.synthesize_dialogue(requests, raw)
            normalize_wav(raw, dest)
            raw.unlink(missing_ok=True)
            duration = probe_duration(dest)
            if not timings or len(timings) != len(group):
                timings = estimate_timings([len(item["text"]) for item in group], duration)
            else:
                end = max(span[1] for span in timings) or duration
                scale = duration / end if end else 1.0
                timings = [(start * scale, min(duration, stop * scale)) for start, stop in timings]
            for item, (start, stop) in zip(group, timings):
                stop = max(start + 0.05, min(duration, stop))
                slice_wav(dest, line_dir / f"{item['line_id']}.wav", start, stop)
                item["local_start"] = start
                item["local_end"] = stop
    finally:
        close_provider(tts)
    progress(88, "正在衔接整集配音")
    mix = work / "episode.wav"
    mix_segments(seg_paths, mix, pause=SEGMENT_PAUSE)
    cursor = 0.0
    expected = 0.0
    for index, group in enumerate(groups):
        seg_duration = probe_duration(seg_paths[index])
        if any("local_start" not in item for item in group):
            _fill_local_from_weights(group, seg_duration)
        for item in group:
            item["abs_start"] = cursor + float(item["local_start"])
            item["duration"] = max(0.05, float(item["local_end"]) - float(item["local_start"]))
        cursor += seg_duration
        expected += seg_duration
        if index != len(groups) - 1:
            cursor += SEGMENT_PAUSE
            expected += SEGMENT_PAUSE
    final = probe_duration(mix)
    if expected > 0 and abs(final - expected) > 0.08:
        scale = final / expected
        for group in groups:
            for item in group:
                item["abs_start"] = float(item["abs_start"]) * scale
                item["duration"] = float(item["duration"]) * scale


def _fill_local_from_weights(group: list[dict], duration: float) -> None:
    weights = [float(item.get("duration_sec") or 0) or len(item["text"]) for item in group]
    for item, (start, stop) in zip(group, estimate_timings(weights, duration)):
        item["local_start"] = start
        item["local_end"] = stop


def _store_dialogue(session_factory, settings: Settings, episode_id: str, plan: list[dict], work) -> None:
    mix = work / "episode.wav"
    with session_scope(session_factory) as db:
        episode = _episode(db, episode_id)
        by_id = {line.id: line for line in episode.lines}
        for item in plan:
            line = by_id[item["line_id"]]
            wav = work / "lines" / f"{item['line_id']}.wav"
            line.audio_path = to_rel(settings, wav)
            line.duration_sec = float(item["duration"])
            line.start_sec = float(item["abs_start"])
        remove_stored(settings, episode.video_path)
        remove_stored(settings, episode.subtitle_path)
        episode.audio_path = to_rel(settings, mix)
        episode.video_path = None
        episode.subtitle_path = None
        episode.status = _status(episode)
        episode.error = None
        episode.updated_at = utcnow()


def _voice_catalog(settings: Settings):
    provider = build_tts(settings)
    try:
        return provider.list_voices()
    except Exception:
        logger.warning("音色列表拉取失败，改用内置预设")
        return list(PRESET_VOICES)
    finally:
        close_provider(provider)


def _scene_lengths_from_starts(scenes, lines, audio_total: float) -> list[float]:
    lengths = []
    ordered = sorted(scenes, key=lambda scene: scene.position)
    for index, scene in enumerate(ordered):
        start = float(lines[scene.start_line].start_sec or 0)
        if index + 1 < len(ordered):
            end = float(lines[ordered[index + 1].start_line].start_sec or start)
        else:
            end = audio_total
        lengths.append(max(0.05, end - start))
    return lengths


def invalidate_after_line_edit(settings: Settings, episode: Episode, line: ScriptLine | None = None) -> None:
    if line is not None:
        remove_stored(settings, line.audio_path)
        line.audio_path = None
        line.duration_sec = None
        line.start_sec = None
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
