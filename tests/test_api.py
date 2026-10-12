import json
from pathlib import Path

from app.ffmpeg_util import probe_streams

SAMPLE = Path(__file__).resolve().parents[1].joinpath("samples", "rain-night.txt").read_text(encoding="utf-8")


def test_health_and_mock_config(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ffmpeg"] is True
    config = client.get("/api/config").json()
    assert config["app_mode"] == "mock"
    assert config["tts_provider"] == "mock"
    voices = client.get("/api/voices").json()
    assert any(voice["name"] == "故宫纪录片" for voice in voices["voices"])


def test_upload_and_edit_line(client):
    response = client.post(
        "/api/novels/upload",
        files={"file": ("story.md", "# 序\n\n他走在雨里。\n".encode("utf-8"), "text/markdown")},
        data={"title": "上传测试"},
    )
    assert response.status_code == 200
    novel = response.json()
    assert novel["title"] == "上传测试"
    assert novel["episode_count"] == 1


def test_mock_pipeline_renders_video(client, app):
    created = client.post("/api/novels", json={"title": "雨夜来信", "text": SAMPLE})
    assert created.status_code == 200
    novel = created.json()
    assert novel["episode_count"] == 2
    episode_id = novel["episodes"][0]["id"]

    broken = client.post(f"/api/episodes/{episode_id}/breakdown")
    assert broken.status_code == 200
    assert broken.json()["status"] == "succeeded"
    episode = client.get(f"/api/episodes/{episode_id}").json()
    speakers = {line["speaker"] for line in episode["lines"]}
    assert {"旁白", "林晚", "沈衡"} <= speakers
    lin = next(character for character in episode["characters"] if character["name"] == "林晚")
    patched = client.patch(f"/api/characters/{lin['id']}", json={"voice_id": "voice-lin-wan", "voice_name": "林晚的音色"})
    assert patched.status_code == 200
    assert patched.json()["voice_id"] == "voice-lin-wan"

    first_line = episode["lines"][0]
    edited = client.patch(f"/api/lines/{first_line['id']}", json={"text": first_line["text"] + "风更紧了。"})
    assert edited.status_code == 200

    job = client.post(f"/api/episodes/{episode_id}/pipeline")
    assert job.status_code == 200
    body = job.json()
    assert body["status"] == "succeeded", body.get("error")

    episode = client.get(f"/api/episodes/{episode_id}").json()
    assert episode["has_video"] is True
    assert episode["voiced_count"] == episode["line_count"]
    assert episode["illustrated_count"] == episode["scene_count"] > 0
    assert any("风更紧了" in line["text"] for line in episode["lines"])
    assert "角色外观必须保持一致" in episode["scenes"][0]["prompt"]

    preview = client.post("/api/voices/preview", json={"voice_id": "voice-lin-wan"})
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("audio/wav")
    assert preview.content[:4] == b"RIFF"

    first = episode["lines"][0]
    regen = client.post(f"/api/lines/{first['id']}/tts")
    assert regen.status_code == 200
    assert regen.json()["status"] == "succeeded", regen.json().get("error")
    episode = client.get(f"/api/episodes/{episode_id}").json()
    assert episode["lines"][0]["audio_url"]
    assert episode["audio_url"]
    assert episode["has_video"] is False

    again = client.post(f"/api/episodes/{episode_id}/pipeline")
    assert again.status_code == 200
    assert again.json()["status"] == "succeeded", again.json().get("error")
    episode = client.get(f"/api/episodes/{episode_id}").json()
    assert episode["has_video"] is True

    video = client.get(episode["video_url"])
    assert video.status_code == 200
    assert video.headers["content-type"].startswith("video/mp4")
    assert video.content[4:8] == b"ftyp"
    files = list(Path(app.state.settings.data_dir).rglob("episode.mp4"))
    probe = probe_streams(files[0])
    kinds = {stream["codec_type"] for stream in probe["streams"]}
    assert {"video", "audio"} <= kinds
    assert float(probe["format"]["duration"]) > 0.5
    subtitles = Path(app.state.settings.data_dir).rglob("episode.srt")
    text = next(subtitles).read_text(encoding="utf-8")
    assert "不要再往北走" in text


def test_gbk_upload(client):
    raw = "第1集 旧档\n\n林晚说道：“这是旧编码。”\n".encode("gb18030")
    response = client.post("/api/novels/upload", files={"file": ("old.txt", raw, "text/plain")})
    assert response.status_code == 200
    episode_id = response.json()["episodes"][0]["id"]
    detail = client.get(f"/api/episodes/{episode_id}").json()
    assert "旧编码" in detail["source_text"]
