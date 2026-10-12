from pathlib import Path

from app.config import Settings


def novel_dir(settings: Settings, novel_id: str) -> Path:
    path = settings.data_path / "novels" / novel_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def episode_dir(settings: Settings, novel_id: str, episode_id: str) -> Path:
    path = novel_dir(settings, novel_id) / "episodes" / episode_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def to_rel(settings: Settings, path: Path) -> str:
    return str(path.resolve().relative_to(settings.data_path.resolve()))


def from_rel(settings: Settings, relpath: str | None) -> Path | None:
    if not relpath:
        return None
    root = settings.data_path.resolve()
    path = (root / relpath).resolve()
    if path != root and root not in path.parents:
        raise ValueError("路径越界")
    return path


def remove_stored(settings: Settings, relpath: str | None) -> None:
    path = from_rel(settings, relpath)
    if path and path.is_file():
        path.unlink()


def sqlite_file(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if not raw or raw.startswith(":memory:"):
        return None
    return Path(raw)
