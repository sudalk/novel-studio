import json

from app.providers.errors import ProviderError
from app.services.heuristics import stable_appearance
from app.services.image_prompt import compose_image_prompt
from app.services.prompts import SCENE_SYSTEM


def plan_scenes(lines: list[dict], characters: list[dict], llm, style: str = "", aspect: str = "16:9") -> list[dict]:
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
    data = llm.complete_json(SCENE_SYSTEM, json.dumps(payload, ensure_ascii=False))
    scenes = normalize_scenes(data, len(lines))
    looks = {item["name"]: (item.get("description") or "").strip() or stable_appearance(item["name"]) for item in characters}
    for scene in scenes:
        scene["prompt"] = compose_image_prompt(scene["prompt"], style, looks, aspect)
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


