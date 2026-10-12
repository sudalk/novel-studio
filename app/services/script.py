from app.providers.errors import ProviderError
from app.services.prompts import SCRIPT_SYSTEM

NARRATOR_ALIASES = {"旁白", "叙述", "叙述者", "画外音", "narrator"}


def breakdown_text(text: str, llm) -> dict:
    data = llm.complete_json(SCRIPT_SYSTEM, text)
    return normalize_script(data)


def normalize_script(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ProviderError("剧本结果不是 JSON 对象")
    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ProviderError("剧本结果缺少台词")
    lines: list[dict] = []
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or "旁白").strip() or "旁白"
        if speaker.lower() in NARRATOR_ALIASES or speaker in NARRATOR_ALIASES:
            speaker = "旁白"
        content = str(item.get("text") or "").strip()
        if not content:
            continue
        lines.append({"speaker": speaker, "text": content})
    if not lines:
        raise ProviderError("剧本结果没有有效台词")

    descriptions: dict[str, str] = {}
    raw_characters = data.get("characters") if isinstance(data.get("characters"), list) else []
    for item in raw_characters:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in NARRATOR_ALIASES or name in NARRATOR_ALIASES:
            name = "旁白"
        descriptions[name] = "" if name == "旁白" else str(item.get("description") or "").strip()
    for line in lines:
        descriptions.setdefault(line["speaker"], "")
    characters = [
        {
            "name": name,
            "is_narrator": name == "旁白",
            "description": "" if name == "旁白" else description,
        }
        for name, description in descriptions.items()
    ]
    characters.sort(key=lambda item: (not item["is_narrator"], item["name"]))
    if not any(item["is_narrator"] for item in characters):
        characters.insert(0, {"name": "旁白", "is_narrator": True, "description": ""})
    return {"characters": characters, "lines": lines}


def line_kind(speaker: str) -> str:
    return "narrator" if speaker == "旁白" else "dialogue"
