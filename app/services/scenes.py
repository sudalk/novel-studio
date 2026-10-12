import hashlib
import json

from app.providers.errors import ProviderError
from app.services.heuristics import stable_appearance
from app.services.image_prompt import compose_image_prompt
from app.services.prompts import SCENE_SYSTEM


def plan_scenes(
    lines: list[dict],
    characters: list[dict],
    llm,
    style: str = "",
    aspect: str = "9:16",
    max_images: int | None = None,
) -> list[dict]:
    payload = {
        "characters": [
            {"name": item["name"], "description": item.get("description") or ""}
            for item in characters
            if item.get("name")
        ],
        "lines": [
            {"index": index, "speaker": line["speaker"], "text": line["text"]}
            for index, line in enumerate(lines)
        ],
    }
    if max_images is not None and max_images > 0:
        payload["max_images"] = max_images
    data = llm.complete_json(SCENE_SYSTEM, json.dumps(payload, ensure_ascii=False))
    scenes = bind_visuals(normalize_scenes(data, len(lines)), max_images)
    looks = {item["name"]: (item.get("description") or "").strip() or stable_appearance(item["name"]) for item in characters}
    for scene in scenes:
        if scene["key"]:
            scene["prompt"] = compose_image_prompt(scene["prompt"] or scene["summary"], style, looks, aspect)
        else:
            scene["prompt"] = ""
    return scenes


def normalize_scenes(data: dict, line_count: int) -> list[dict]:
    if line_count <= 0:
        raise ProviderError("没有台词可以分镜")
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list) or not data["scenes"]:
        raise ProviderError("分镜结果缺少 scenes")
    scenes: list[dict] = []
    for item in data["scenes"]:
        if not isinstance(item, dict):
            raise ProviderError("分镜条目格式不正确")
        try:
            start = int(item["start_line"])
            end = int(item["end_line"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("分镜缺少行号") from exc
        summary = str(item.get("summary") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if start < 0 or end < start or end >= line_count:
            raise ProviderError("分镜行号超出台词范围")
        if not prompt and not summary:
            raise ProviderError("分镜缺少画面提示词")
        scenes.append(
            {
                "start_line": start,
                "end_line": end,
                "summary": summary or prompt[:40],
                "prompt": prompt or summary,
                "key": _as_bool(item.get("key"), default=False),
                "location": str(item.get("location") or "").strip(),
            }
        )
    scenes.sort(key=lambda scene: (scene["start_line"], scene["end_line"]))
    expected = 0
    for scene in scenes:
        if scene["start_line"] != expected:
            raise ProviderError("分镜没有连续覆盖全部台词")
        expected = scene["end_line"] + 1
    if expected != line_count:
        raise ProviderError("分镜没有覆盖全部台词")
    return scenes


def bind_visuals(scenes: list[dict], max_images: int | None) -> list[dict]:
    """普通场景沿用最近一张关键画面。开头还没有画面时，先借用后面第一张。"""
    bound = [dict(scene) for scene in scenes]
    for scene in bound:
        scene["key"] = bool(scene.get("key"))
    if max_images is not None and max_images > 0:
        keys = [index for index, scene in enumerate(bound) if scene["key"]]
        keep = _spread(keys, max_images)
        for index, scene in enumerate(bound):
            if scene["key"] and index not in keep:
                scene["key"] = False
    if bound and not any(scene["key"] for scene in bound):
        bound[0]["key"] = True
        if not (bound[0].get("prompt") or "").strip():
            bound[0]["prompt"] = bound[0].get("summary") or "开场"
    first_key = next(index for index, scene in enumerate(bound) if scene["key"])
    owner = None
    for index, scene in enumerate(bound):
        if scene["key"]:
            owner = index
        scene["visual_of"] = owner if owner is not None else first_key
    return bound


def visual_cache_key(prompt: str, size: str) -> str:
    return hashlib.sha256(f"{size}\n{prompt}".encode("utf-8")).hexdigest()


def stamp_cache_keys(scenes: list[dict], size: str) -> None:
    for scene in scenes:
        if scene.get("key"):
            scene["cache_key"] = visual_cache_key(scene.get("prompt") or scene.get("summary") or "", size)
    for scene in scenes:
        scene["cache_key"] = scenes[scene["visual_of"]]["cache_key"]


def missing_cache_keys(scenes: list[dict], cached: set[str]) -> list[str]:
    """按出现顺序返回还没有文件的关键画面。同一提示词只生成一次。"""
    missing: list[str] = []
    seen: set[str] = set()
    for scene in scenes:
        if not scene.get("key"):
            continue
        key = scene.get("cache_key") or ""
        if not key or key in cached or key in seen:
            continue
        seen.add(key)
        missing.append(key)
    return missing


def _spread(keys: list[int], cap: int) -> set[int]:
    if cap <= 0 or not keys:
        return set()
    if len(keys) <= cap:
        return set(keys)
    if cap == 1:
        return {keys[0]}
    chosen: list[int] = []
    last = cap - 1
    for step in range(cap):
        pick = keys[round(step * (len(keys) - 1) / last)]
        if pick not in chosen:
            chosen.append(pick)
    for key in keys:
        if len(chosen) >= cap:
            break
        if key not in chosen:
            chosen.append(key)
    return set(chosen)


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "关键"}:
        return True
    if text in {"0", "false", "no", "n", "否", ""}:
        return False
    return default


