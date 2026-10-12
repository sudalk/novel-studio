import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import new_id, utcnow
from app.models import Character, Episode, Job, Novel, ScriptLine
from app.paths import from_rel
from app.providers.errors import ProviderError
from app.providers.factory import build_tts, close_provider
from app.providers.voices import PRESET_VOICES, assign_default_voice
from app.services.heuristics import stable_appearance
from app.services.image_prompt import DEFAULT_STYLE
from app.services.importer import create_novel, decode_bytes
from app.services.jobs import execute_job
from app.services.pipeline import drop_scenes, invalidate_after_line_edit
from app.services.script import line_kind

router = APIRouter()
SAMPLE_PATH = Path(__file__).resolve().parents[2] / "samples" / "rain-night.txt"
MAX_UPLOAD = 5_000_000


def get_db(request: Request):
    db = request.app.state.SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NovelIn(BaseModel):
    title: str | None = None
    text: str = Field(min_length=1, max_length=2_000_000)


class NovelPatch(BaseModel):
    style: str | None = Field(default=None, max_length=4000)
    image_max: int | None = Field(default=None, ge=1, le=24)


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    voice_id: str = ""
    voice_name: str = ""


class CharacterPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    voice_id: str | None = None
    voice_name: str | None = None


class LineIn(BaseModel):
    speaker: str = "旁白"
    text: str = Field(min_length=1, max_length=2000)


class LinePatch(BaseModel):
    speaker: str | None = None
    text: str | None = None


class MoveIn(BaseModel):
    direction: str


class PreviewIn(BaseModel):
    voice_id: str = Field(min_length=1, max_length=80)
    text: str = ""


def _settings(request: Request):
    return request.app.state.settings


def _novel(db: Session, novel_id: str) -> Novel:
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "作品不存在")
    return novel


def _episode(db: Session, episode_id: str) -> Episode:
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(404, "分集不存在")
    return episode


def _character(db: Session, character_id: str) -> Character:
    character = db.get(Character, character_id)
    if character is None:
        raise HTTPException(404, "角色不存在")
    return character


def _line(db: Session, line_id: str) -> ScriptLine:
    line = db.get(ScriptLine, line_id)
    if line is None:
        raise HTTPException(404, "台词不存在")
    return line


def _job_out(job: Job) -> dict:
    return {
        "id": job.id,
        "novel_id": job.novel_id,
        "episode_id": job.episode_id,
        "kind": job.kind,
        "line_id": job.line_id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _character_out(character: Character) -> dict:
    return {
        "id": character.id,
        "name": character.name,
        "description": character.description,
        "voice_id": character.voice_id,
        "voice_name": character.voice_name,
        "voice_locked": bool(character.voice_locked),
        "gender": character.gender,
        "age": character.age,
        "personality": character.personality,
        "is_narrator": character.is_narrator,
    }


def _line_out(line: ScriptLine) -> dict:
    return {
        "id": line.id,
        "position": line.position,
        "speaker": line.speaker,
        "kind": line.kind,
        "text": line.text,
        "duration_sec": line.duration_sec,
        "audio_url": f"/api/media/lines/{line.id}/audio" if line.audio_path else None,
    }


def _scene_out(scene) -> dict:
    return {
        "id": scene.id,
        "position": scene.position,
        "start_line": scene.start_line,
        "end_line": scene.end_line,
        "summary": scene.summary,
        "prompt": scene.prompt,
        "key_visual": bool(scene.key_visual),
        "image_path": scene.image_path,
        "source_url": scene.source_url,
        "image_url": f"/api/media/scenes/{scene.id}/image" if scene.image_path else None,
    }


def _episode_summary(episode: Episode) -> dict:
    lines = list(episode.lines)
    scenes = list(episode.scenes)
    return {
        "id": episode.id,
        "position": episode.position,
        "title": episode.title,
        "status": episode.status,
        "line_count": len(lines),
        "voiced_count": sum(1 for line in lines if line.audio_path),
        "scene_count": len(scenes),
        "illustrated_count": sum(1 for scene in scenes if scene.image_path),
        "image_count": len({scene.image_path for scene in scenes if scene.image_path}),
        "has_video": bool(episode.video_path),
        "updated_at": episode.updated_at,
    }


def _episode_detail(db: Session, episode: Episode) -> dict:
    latest = db.query(Job).filter(Job.episode_id == episode.id).order_by(Job.created_at.desc()).first()
    payload = _episode_summary(episode)
    payload.update(
        {
            "novel_id": episode.novel_id,
            "novel_title": episode.novel.title,
            "source_text": episode.source_text,
            "error": episode.error,
            "audio_url": f"/api/media/episodes/{episode.id}/audio" if episode.audio_path else None,
            "video_url": f"/api/media/episodes/{episode.id}/video" if episode.video_path else None,
            "lines": [_line_out(line) for line in episode.lines],
            "scenes": [_scene_out(scene) for scene in episode.scenes],
            "characters": [_character_out(character) for character in _ordered_characters(episode.novel)],
            "latest_job": _job_out(latest) if latest else None,
        }
    )
    return payload


def _novel_out(novel: Novel) -> dict:
    return {
        "id": novel.id,
        "title": novel.title,
        "created_at": novel.created_at,
        "updated_at": novel.updated_at,
        "episode_count": len(novel.episodes),
        "character_count": len(novel.characters),
        "style": novel.style or "",
        "style_default": DEFAULT_STYLE,
        "image_max": novel.image_max if novel.image_max else None,
        "episodes": [_episode_summary(episode) for episode in novel.episodes],
        "characters": [_character_out(character) for character in _ordered_characters(novel)],
    }


def _ordered_characters(novel: Novel) -> list[Character]:
    return sorted(novel.characters, key=lambda item: (not item.is_narrator, item.created_at))


def _launch(request: Request, db: Session, episode: Episode, kind: str, line_id: str | None = None) -> dict:
    settings = _settings(request)
    active = (
        db.query(Job)
        .filter(Job.episode_id == episode.id, Job.status.in_(("queued", "running")))
        .first()
    )
    if active and not settings.jobs_inline:
        raise HTTPException(409, "这一集还有任务在执行")
    job = Job(
        id=new_id(),
        novel_id=episode.novel_id,
        episode_id=episode.id,
        kind=kind,
        line_id=line_id,
        status="queued",
        progress=0,
        message="排队中",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(job)
    db.commit()
    if settings.jobs_inline:
        execute_job(job.id, request.app.state.SessionLocal, settings)
    db.refresh(job)
    return _job_out(job)


def _ensure_speaker(db: Session, request: Request, novel: Novel, name: str) -> Character:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(400, "说话人不能为空")
    found = next((item for item in novel.characters if item.name == cleaned), None)
    if found:
        return found
    settings = _settings(request)
    is_narrator = cleaned == "旁白"
    voice_id = ""
    voice_name = ""
    if settings.resolved_tts() == "mock":
        nth = len([item for item in novel.characters if not item.is_narrator])
        voice = assign_default_voice(is_narrator, -1 if is_narrator else nth)
        voice_id = voice.id
        voice_name = voice.name
    character = Character(
        id=new_id(),
        novel_id=novel.id,
        name=cleaned,
        description="" if is_narrator else stable_appearance(cleaned),
        voice_id=voice_id,
        voice_name=voice_name,
        is_narrator=is_narrator,
        created_at=utcnow(),
    )
    db.add(character)
    db.flush()
    return character


def _import_text(db: Session, title: str | None, text: str, filename: str | None) -> dict:
    try:
        novel = create_novel(db, title, text, filename)
        db.commit()
        db.refresh(novel)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return _novel_out(novel)


@router.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": shutil.which("ffmpeg") is not None}


@router.get("/api/config")
def config(request: Request):
    settings = _settings(request)
    return {
        "app_mode": settings.app_mode,
        "llm_provider": settings.resolved_llm(),
        "tts_provider": settings.resolved_tts(),
        "image_provider": settings.resolved_image(),
        "llm_model": settings.resolved_llm_model(),
        "llm_configured": bool(settings.resolved_llm_key()),
        "image_configured": bool(settings.image_api_key.strip()),
        "image_model": settings.resolved_image_model(),
        "image_models": settings.resolved_image_models(),
        "image_max_per_episode": settings.resolved_image_cap(),
        "image_aspect": settings.resolved_image_aspect(),
        "moss_configured": bool(settings.resolved_mosi_key()),
        "moss_model": settings.moss_model,
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }


@router.get("/api/sample")
def sample():
    text = SAMPLE_PATH.read_text(encoding="utf-8")
    return {"title": "雨夜来信", "filename": SAMPLE_PATH.name, "text": text}


@router.get("/api/voices")
def voices(request: Request):
    settings = _settings(request)
    try:
        provider = build_tts(settings)
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    try:
        listed = provider.list_voices()
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        close_provider(provider)
    examples = []
    if provider.name != "mock":
        examples = [
            {
                "id": voice.id,
                "name": voice.name,
                "description": voice.description,
                "language": voice.language,
                "preview_url": voice.preview_url,
            }
            for voice in PRESET_VOICES
        ]
    return {
        "provider": provider.name,
        "model": settings.moss_model if provider.name == "mosi" else "",
        "voices": [
            {
                "id": voice.id,
                "name": voice.name,
                "description": voice.description,
                "language": voice.language,
                "preview_url": voice.preview_url,
            }
            for voice in listed
        ],
        "examples": examples,
    }


@router.get("/api/novels")
def list_novels(db: Session = Depends(get_db)):
    novels = db.query(Novel).order_by(Novel.updated_at.desc()).all()
    return [
        {
            "id": novel.id,
            "title": novel.title,
            "created_at": novel.created_at,
            "updated_at": novel.updated_at,
            "episode_count": len(novel.episodes),
            "character_count": len(novel.characters),
        }
        for novel in novels
    ]


@router.post("/api/novels")
def import_novel(payload: NovelIn, db: Session = Depends(get_db)):
    return _import_text(db, payload.title, payload.text, None)


@router.post("/api/novels/upload")
async def upload_novel(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    db: Session = Depends(get_db),
):
    filename = file.filename or "upload.txt"
    if not filename.lower().endswith((".txt", ".md")):
        raise HTTPException(400, "只支持 .txt 或 .md")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件是空的")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "文件超过 5MB")
    return _import_text(db, title, decode_bytes(raw), filename)


@router.get("/api/novels/{novel_id}")
def get_novel(novel_id: str, db: Session = Depends(get_db)):
    return _novel_out(_novel(db, novel_id))


@router.patch("/api/novels/{novel_id}")
def patch_novel(novel_id: str, payload: NovelPatch, db: Session = Depends(get_db)):
    novel = _novel(db, novel_id)
    if payload.style is not None:
        novel.style = payload.style.strip()
    if "image_max" in payload.model_fields_set:
        novel.image_max = payload.image_max
    novel.updated_at = utcnow()
    db.commit()
    db.refresh(novel)
    return _novel_out(novel)


@router.delete("/api/novels/{novel_id}")
def delete_novel(novel_id: str, request: Request, db: Session = Depends(get_db)):
    novel = _novel(db, novel_id)
    folder = _settings(request).data_path / "novels" / novel.id
    db.delete(novel)
    db.commit()
    shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


@router.post("/api/novels/{novel_id}/characters")
def add_character(novel_id: str, payload: CharacterIn, request: Request, db: Session = Depends(get_db)):
    novel = _novel(db, novel_id)
    name = payload.name.strip()
    if any(character.name == name for character in novel.characters):
        raise HTTPException(409, "已有同名角色")
    character = _ensure_speaker(db, request, novel, name)
    if payload.description.strip():
        character.description = payload.description.strip()
    if payload.voice_id.strip():
        character.voice_id = payload.voice_id.strip()
        character.voice_name = payload.voice_name.strip()
    novel.updated_at = utcnow()
    db.commit()
    db.refresh(character)
    return _character_out(character)


@router.patch("/api/characters/{character_id}")
def patch_character(character_id: str, payload: CharacterPatch, db: Session = Depends(get_db)):
    character = _character(db, character_id)
    if payload.name is not None and payload.name.strip() and payload.name.strip() != character.name:
        if character.is_narrator:
            raise HTTPException(400, "旁白的名字不能修改")
        new_name = payload.name.strip()
        clash = (
            db.query(Character)
            .filter(Character.novel_id == character.novel_id, Character.name == new_name, Character.id != character.id)
            .first()
        )
        if clash:
            raise HTTPException(409, "已有同名角色")
        old_name = character.name
        character.name = new_name
        episode_ids = [episode.id for episode in character.novel.episodes]
        if episode_ids:
            db.query(ScriptLine).filter(ScriptLine.episode_id.in_(episode_ids), ScriptLine.speaker == old_name).update(
                {ScriptLine.speaker: new_name, ScriptLine.kind: line_kind(new_name)},
                synchronize_session=False,
            )
    if payload.description is not None:
        character.description = payload.description.strip()
    if payload.voice_id is not None:
        new_voice = payload.voice_id.strip()
        if new_voice != (character.voice_id or ""):
            character.voice_locked = True
        character.voice_id = new_voice
    if payload.voice_name is not None:
        character.voice_name = payload.voice_name.strip()
    character.novel.updated_at = utcnow()
    db.commit()
    db.refresh(character)
    return _character_out(character)


@router.delete("/api/characters/{character_id}")
def delete_character(character_id: str, db: Session = Depends(get_db)):
    character = _character(db, character_id)
    if character.is_narrator:
        raise HTTPException(400, "不能删除旁白")
    used = (
        db.query(ScriptLine)
        .join(Episode)
        .filter(Episode.novel_id == character.novel_id, ScriptLine.speaker == character.name)
        .count()
    )
    if used:
        raise HTTPException(409, "仍有台词使用这个角色，请先改说话人")
    db.delete(character)
    db.commit()
    return {"ok": True}


@router.get("/api/episodes/{episode_id}")
def get_episode(episode_id: str, db: Session = Depends(get_db)):
    return _episode_detail(db, _episode(db, episode_id))


@router.post("/api/episodes/{episode_id}/lines")
def add_line(episode_id: str, payload: LineIn, request: Request, db: Session = Depends(get_db)):
    episode = _episode(db, episode_id)
    speaker = _ensure_speaker(db, request, episode.novel, payload.speaker)
    position = max((line.position for line in episode.lines), default=-1) + 1
    line = ScriptLine(
        id=new_id(),
        episode_id=episode.id,
        position=position,
        speaker=speaker.name,
        kind=line_kind(speaker.name),
        text=payload.text.strip(),
    )
    db.add(line)
    drop_scenes(_settings(request), episode)
    invalidate_after_line_edit(_settings(request), episode, None)
    episode.novel.updated_at = utcnow()
    db.commit()
    db.refresh(episode)
    return _episode_detail(db, episode)


@router.patch("/api/lines/{line_id}")
def patch_line(line_id: str, payload: LinePatch, request: Request, db: Session = Depends(get_db)):
    line = _line(db, line_id)
    episode = line.episode
    changed_speaker = False
    if payload.speaker is not None and payload.speaker.strip() and payload.speaker.strip() != line.speaker:
        speaker = _ensure_speaker(db, request, episode.novel, payload.speaker)
        line.speaker = speaker.name
        line.kind = line_kind(speaker.name)
        changed_speaker = True
    if payload.text is not None:
        text = payload.text.strip()
        if not text:
            raise HTTPException(400, "台词不能为空")
        if text != line.text:
            line.text = text
    invalidate_after_line_edit(_settings(request), episode, line)
    if changed_speaker:
        drop_scenes(_settings(request), episode)
    episode.novel.updated_at = utcnow()
    db.commit()
    db.refresh(episode)
    return _episode_detail(db, episode)


@router.delete("/api/lines/{line_id}")
def delete_line(line_id: str, request: Request, db: Session = Depends(get_db)):
    line = _line(db, line_id)
    episode = line.episode
    invalidate_after_line_edit(_settings(request), episode, line)
    db.delete(line)
    db.flush()
    remaining = [item for item in sorted(episode.lines, key=lambda item: item.position) if item.id != line.id]
    for index, item in enumerate(remaining):
        item.position = index
    drop_scenes(_settings(request), episode)
    episode.novel.updated_at = utcnow()
    db.commit()
    db.refresh(episode)
    return _episode_detail(db, episode)


@router.post("/api/lines/{line_id}/move")
def move_line(line_id: str, payload: MoveIn, request: Request, db: Session = Depends(get_db)):
    if payload.direction not in {"up", "down"}:
        raise HTTPException(400, "direction 只能是 up 或 down")
    line = _line(db, line_id)
    episode = line.episode
    ordered = sorted(episode.lines, key=lambda item: item.position)
    index = next(i for i, item in enumerate(ordered) if item.id == line.id)
    swap_index = index - 1 if payload.direction == "up" else index + 1
    if 0 <= swap_index < len(ordered):
        ordered[index].position, ordered[swap_index].position = ordered[swap_index].position, ordered[index].position
        from app.paths import remove_stored

        settings = _settings(request)
        remove_stored(settings, episode.audio_path)
        remove_stored(settings, episode.video_path)
        remove_stored(settings, episode.subtitle_path)
        episode.audio_path = None
        episode.video_path = None
        episode.subtitle_path = None
        drop_scenes(settings, episode)
        episode.updated_at = utcnow()
    db.commit()
    db.refresh(episode)
    return _episode_detail(db, episode)


def _job_route(kind: str):
    def endpoint(episode_id: str, request: Request, db: Session = Depends(get_db)):
        episode = _episode(db, episode_id)
        return _launch(request, db, episode, kind)

    endpoint.__name__ = f"start_{kind}"
    return endpoint


router.add_api_route("/api/episodes/{episode_id}/breakdown", _job_route("breakdown"), methods=["POST"])
router.add_api_route("/api/episodes/{episode_id}/tts", _job_route("tts"), methods=["POST"])
router.add_api_route("/api/episodes/{episode_id}/illustrate", _job_route("illustrate"), methods=["POST"])
router.add_api_route("/api/episodes/{episode_id}/video", _job_route("video"), methods=["POST"])
router.add_api_route("/api/episodes/{episode_id}/pipeline", _job_route("pipeline"), methods=["POST"])


@router.post("/api/lines/{line_id}/tts")
def resynth_line(line_id: str, request: Request, db: Session = Depends(get_db)):
    line = _line(db, line_id)
    return _launch(request, db, line.episode, "line-tts", line_id=line.id)


@router.post("/api/voices/preview")
def preview_voice(payload: PreviewIn, request: Request):
    voice_id = payload.voice_id.strip()
    text = payload.text.strip() or "你好，这是一段音色试听。"
    if len(text) > 80:
        text = text[:80]
    settings = _settings(request)
    dest = settings.data_path / "previews" / f"{new_id()}.wav"
    provider = build_tts(settings)
    try:
        from app.providers.tts import SynthRequest
        from app.services.audio import normalize_wav

        provider.synthesize_line(SynthRequest("preview", "试听", voice_id, text), dest)
        normalized = dest.with_suffix(".norm.wav")
        normalize_wav(dest, normalized)
        data = normalized.read_bytes()
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        close_provider(provider)
        dest.unlink(missing_ok=True)
        dest.with_suffix(".norm.wav").unlink(missing_ok=True)
    return Response(content=data, media_type="audio/wav")


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return _job_out(job)


def _file_response(request: Request, relpath: str | None, media_type: str, filename: str, missing: str):
    if not relpath:
        raise HTTPException(404, missing)
    path = from_rel(_settings(request), relpath)
    if path is None or not path.is_file():
        raise HTTPException(404, missing)
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/api/media/lines/{line_id}/audio")
def line_audio(line_id: str, request: Request, db: Session = Depends(get_db)):
    line = _line(db, line_id)
    return _file_response(request, line.audio_path, "audio/wav", f"{line.id}.wav", "这句还没有配音")


@router.get("/api/media/episodes/{episode_id}/audio")
def episode_audio(episode_id: str, request: Request, db: Session = Depends(get_db)):
    episode = _episode(db, episode_id)
    return _file_response(request, episode.audio_path, "audio/wav", f"{episode.id}.wav", "这一集还没有配音")


@router.get("/api/media/scenes/{scene_id}/image")
def scene_image(scene_id: str, request: Request, db: Session = Depends(get_db)):
    from app.models import Scene

    scene = db.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(404, "场景不存在")
    return _file_response(request, scene.image_path, "image/png", f"{scene.id}.png", "场景图片不在了")


@router.get("/api/media/episodes/{episode_id}/video")
def episode_video(episode_id: str, request: Request, db: Session = Depends(get_db)):
    episode = _episode(db, episode_id)
    return _file_response(request, episode.video_path, "video/mp4", f"{episode.title}.mp4", "这一集还没有成片")
