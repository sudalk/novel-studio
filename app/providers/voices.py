from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    description: str = ""
    language: str = ""


# 来自 MOSI 文档音色列表示例，便于离线演示和手动填写。
# 真实账号是否可用，以 GET /v1/audio/voices 的结果为准。
PRESET_VOICES: tuple[Voice, ...] = (
    Voice("133bd03b-d717-4a55-8974-7ffc9afc1b51", "故宫纪录片", "纪录片、旁白", "中文"),
    Voice("fe85a513-9bf3-4ef7-aa0b-8b2d11e4db93", "少年感人声（男）", "有声书、旁白", "中文"),
    Voice("806c9695-6160-404e-8722-4f788d935af3", "轻快灵动女声", "轻快、活泼", "中文"),
    Voice("f80b6698-0066-430b-88a0-f0fb8796db34", "明太祖", "沉稳、角色", "中文"),
    Voice("2fdf194e-c16e-4587-9027-0d3464e09b4e", "诗词朗读", "沉稳旁白", "中文"),
    Voice("944eb93b-3820-49f3-b2c0-4e37a31d1161", "三农农业旁白", "轻快旁白", "中文"),
    Voice("faf7f550-0627-4fc6-8db0-d3bfdad49358", "经验女教师", "自然、女声", "中文"),
    Voice("19411508-8731-4b68-901d-7e4b8a98e23f", "忧伤的秋", "温柔、女声", "中文"),
)


def assign_default_voice(is_narrator: bool, nth_role: int) -> Voice:
    if is_narrator or nth_role < 0:
        return PRESET_VOICES[0]
    return PRESET_VOICES[1 + (nth_role % (len(PRESET_VOICES) - 1))]
