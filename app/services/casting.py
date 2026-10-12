"""按角色的性别、年龄和性格，选一把稳定的 MOSI 音色。"""

from app.providers.voices import PRESET_VOICES, Voice

_FEMALE = set("晚娘娥娟婷萱柔燕霞凤莲妹妮妍姬嫣")
_MALE = set("衡刚强伟杰峰宇轩浩磊鹏郎君")

_GENDER = {
    "female": "female",
    "male": "male",
    "女": "female",
    "男": "male",
    "女性": "female",
    "男性": "male",
    "unknown": "unknown",
    "未知": "unknown",
}
_AGE = {
    "child": "youth",
    "youth": "youth",
    "young": "youth",
    "adult": "adult",
    "mature": "mature",
    "少年": "youth",
    "青年": "youth",
    "成年": "adult",
    "中年": "mature",
    "老年": "mature",
}
_PERSONALITY = {
    "gentle": "gentle",
    "calm": "calm",
    "lively": "lively",
    "stern": "stern",
    "sad": "gentle",
    "温柔": "gentle",
    "沉稳": "calm",
    "灵动": "lively",
    "活泼": "lively",
    "严厉": "stern",
    "忧伤": "gentle",
}


def profile_for(name: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    if name == "旁白" or payload.get("is_narrator"):
        return {"gender": "unknown", "age": "adult", "personality": "calm", "narrator": True}
    gender = _GENDER.get(str(payload.get("gender") or "").strip().lower(), "")
    if not gender:
        gender = _GENDER.get(str(payload.get("gender") or "").strip(), "")
    if not gender:
        gender = _guess_gender(name)
    age = _AGE.get(str(payload.get("age") or "").strip().lower(), "")
    if not age:
        age = _AGE.get(str(payload.get("age") or "").strip(), "") or "youth"
    personality = _PERSONALITY.get(str(payload.get("personality") or "").strip().lower(), "")
    if not personality:
        personality = _PERSONALITY.get(str(payload.get("personality") or "").strip(), "")
    if not personality:
        personality = "gentle" if gender == "female" else "calm"
    return {"gender": gender, "age": age, "personality": personality, "narrator": False}


def _guess_gender(name: str) -> str:
    if any(char in _FEMALE for char in name):
        return "female"
    if any(char in _MALE for char in name):
        return "male"
    return "unknown"


def _preset(name: str) -> Voice:
    for voice in PRESET_VOICES:
        if voice.name == name:
            return voice
    return PRESET_VOICES[0]


def target_preset(profile: dict) -> Voice:
    if profile.get("narrator"):
        return _preset("故宫纪录片")
    gender = profile.get("gender")
    age = profile.get("age")
    personality = profile.get("personality")
    if gender == "female":
        if personality == "lively":
            return _preset("轻快灵动女声")
        if age == "mature":
            return _preset("经验女教师")
        return _preset("忧伤的秋")
    if gender == "male":
        if age == "mature" or personality == "stern":
            return _preset("明太祖")
        return _preset("少年感人声（男）")
    return _preset("故宫纪录片")


_KEYWORDS = {
    "故宫纪录片": ("沉稳", "旁白", "纪录", "解说", "纪录片"),
    "轻快灵动女声": ("女主", "女声", "少女", "灵动", "清冷"),
    "忧伤的秋": ("女主", "女声", "女", "温柔", "忧伤", "清冷"),
    "经验女教师": ("女", "教师", "中年"),
    "少年感人声（男）": ("青年", "男主", "男", "少年"),
    "明太祖": ("男", "沉稳", "中年", "老"),
}


def resolve_voice(profile: dict, voices: list[Voice], used: set[str]) -> Voice:
    """优先用预设音色。账号里没有这把时，按名字挑最接近的一把，并且不和已选角色重复。"""
    target = target_preset(profile)
    by_id = {voice.id: voice for voice in voices}
    if target.id in by_id and target.id not in used:
        return by_id[target.id]
    ranked: list[tuple[int, Voice]] = []
    for voice in voices:
        if not voice.id or voice.id in used:
            continue
        ranked.append((_score(voice, target.name, profile), voice))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]
    for voice in voices:
        if voice.id and voice.id not in used:
            return voice
    return target


def _score(voice: Voice, preset_name: str, profile: dict) -> int:
    blob = f"{voice.name} {voice.description}"
    score = 0
    for word in _KEYWORDS.get(preset_name, ()):
        if word and word in blob:
            score += 2
    if profile.get("narrator") and any(word in blob for word in ("沉稳", "旁白", "纪录", "解说")):
        score += 2
    if profile.get("gender") == "female" and "女" in blob:
        score += 3
    if profile.get("gender") == "male" and any(word in blob for word in ("男", "青年")):
        score += 3
    return score


def apply_cast(characters, profiles: dict[str, dict], voices: list[Voice]) -> None:
    """已经有音色，或用户锁定过的角色，保持原音色。同一把音色在所有对话段里复用。"""
    used: set[str] = set()
    ordered = sorted(characters, key=lambda item: (not _is_narrator(item), item.name))
    for character in ordered:
        if character.voice_id:
            used.add(character.voice_id)
    for character in ordered:
        payload = profiles.get(character.name) or {}
        profile = profile_for(character.name, {**payload, "is_narrator": _is_narrator(character)})
        character.gender = profile["gender"]
        character.age = profile["age"]
        character.personality = profile["personality"]
        if character.voice_locked or (character.voice_id or "").strip():
            continue
        voice = resolve_voice(profile, voices, used)
        character.voice_id = voice.id
        character.voice_name = voice.name
        used.add(voice.id)


def _is_narrator(character) -> bool:
    return bool(getattr(character, "is_narrator", False) or character.name == "旁白")
