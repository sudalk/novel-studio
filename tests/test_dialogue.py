from app.config import Settings
from app.providers.voices import PRESET_VOICES, Voice
from app.services.casting import apply_cast, profile_for, resolve_voice
from app.services.dialogue import estimate_timings, group_dialogue


def test_sample_groups_narrator_with_each_speaker():
    lines = [
        {"speaker": "旁白", "text": "雨下了一夜。"},
        {"speaker": "林晚", "text": "别往北走。"},
        {"speaker": "旁白", "text": "沈衡站在门外。"},
        {"speaker": "沈衡", "text": "城门关了。"},
    ]
    groups = group_dialogue(lines)
    assert [[line["speaker"] for line in group] for group in groups] == [
        ["旁白", "林晚"],
        ["旁白", "沈衡"],
    ]


def test_timings_cover_the_segment():
    spans = estimate_timings([6, 4], 2.0)
    assert spans[0][0] == 0
    assert abs(spans[-1][1] - 2.0) < 1e-6
    assert spans[0][1] <= spans[1][0] + 1e-6


def test_cast_keeps_locked_voice_and_reuses_narrator():
    class Speaker:
        def __init__(self, name, narrator=False):
            self.name = name
            self.is_narrator = narrator
            self.voice_id = ""
            self.voice_name = ""
            self.voice_locked = False
            self.gender = ""
            self.age = ""
            self.personality = ""

    narrator = Speaker("旁白", True)
    lin = Speaker("林晚")
    shen = Speaker("沈衡")
    profiles = {
        "旁白": profile_for("旁白", {"is_narrator": True}),
        "林晚": profile_for("林晚", {"gender": "female", "age": "youth", "personality": "gentle"}),
        "沈衡": profile_for("沈衡", {"gender": "male", "age": "youth", "personality": "calm"}),
    }
    apply_cast([narrator, lin, shen], profiles, list(PRESET_VOICES))
    assert narrator.voice_name == "故宫纪录片"
    assert lin.voice_name == "忧伤的秋"
    assert shen.voice_name == "少年感人声（男）"
    assert len({narrator.voice_id, lin.voice_id, shen.voice_id}) == 3
    locked = lin.voice_id
    lin.voice_locked = True
    lin.voice_id = "user-voice"
    apply_cast([narrator, lin, shen], profiles, list(PRESET_VOICES))
    assert lin.voice_id == "user-voice"
    assert narrator.voice_id != ""
    assert locked != "user-voice"


def test_account_voices_match_when_presets_are_missing():
    voices = [
        Voice("narr", "沉稳", "", ""),
        Voice("girl", "女主", "", ""),
        Voice("boy", "青年音", "", ""),
    ]
    used: set[str] = set()
    narrator = resolve_voice(profile_for("旁白", {"is_narrator": True}), voices, used)
    used.add(narrator.id)
    lin = resolve_voice(profile_for("林晚", {"gender": "female", "age": "youth", "personality": "gentle"}), voices, used)
    used.add(lin.id)
    shen = resolve_voice(profile_for("沈衡", {"gender": "male", "age": "youth", "personality": "calm"}), voices, used)
    assert (narrator.name, lin.name, shen.name) == ("沉稳", "女主", "青年音")


def test_vertical_defaults(tmp_path):
    settings = Settings(
        app_mode="mock",
        llm_provider="mock",
        tts_provider="mock",
        image_provider="mock",
        mosi_api_key="",
        image_api_key="",
        openai_api_key="",
        deepseek_api_key="",
        data_dir=str(tmp_path),
    )
    assert settings.resolved_image_aspect() == "9:16"
    assert settings.resolved_image_size() == "1024x1792"
    assert (settings.video_width, settings.video_height) == (1080, 1920)
