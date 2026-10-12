"""离线跑通示例小说的第一集，并把成片路径打出来。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    data = root / "data" / "demo"
    data.mkdir(parents=True, exist_ok=True)
    database = data / "studio.db"
    if database.exists():
        database.unlink()
    settings = Settings(
        app_mode="mock",
        jobs_inline=True,
        llm_provider="mock",
        tts_provider="mock",
        image_provider="mock",
        data_dir=str(data),
        database_url=f"sqlite:///{database}",
    )
    application = create_app(settings)
    sample = (root / "samples" / "rain-night.txt").read_text(encoding="utf-8")
    with TestClient(application) as client:
        created = client.post("/api/novels", json={"title": "雨夜来信", "text": sample})
        created.raise_for_status()
        novel = created.json()
        episode_id = novel["episodes"][0]["id"]
        job = client.post(f"/api/episodes/{episode_id}/pipeline")
        job.raise_for_status()
        body = job.json()
        if body["status"] != "succeeded":
            raise SystemExit(body.get("error") or body)
        episode = client.get(f"/api/episodes/{episode_id}").json()
        print(f"作品：{novel['title']}")
        print(f"分集：{episode['title']}")
        print(f"台词：{episode['line_count']}  场景：{episode['scene_count']}")
        print(f"播放：{episode['video_url']}")
    videos = sorted(data.rglob("episode.mp4"))
    if not videos:
        raise SystemExit("没有生成视频")
    print(f"文件：{videos[-1]}")


if __name__ == "__main__":
    main()
