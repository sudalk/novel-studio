"""把单镜画面、整部作品的 STYLE，以及共用的 COMMON 约束拼成最终提示词。"""

ASPECT_SIZES = {
    "1:1": "1024x1024",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
    "4:3": "1792x1344",
    "3:4": "1344x1792",
}

DEFAULT_STYLE = (
    "系列统一画风：电影感插画，暖色写实，柔和侧光，淡彩质感。"
    "同一角色的年龄感、发型、服装颜色与形制在全系列保持不变。"
)

COMMON_CONSTRAINTS = (
    "构图完整，主体清晰，不要裁切人物头部和关键道具。\n"
    "不要水印，不要标志，不要签名。\n"
    "不要无关文字，不要乱码，不要错乱字符。\n"
    "不要元数据，不要页脚文字，不要标题栏或参数说明。\n"
    "不要分镜格，不要漫画边框。"
)


def size_for_aspect(aspect: str) -> str:
    key = (aspect or "").strip().replace("：", ":")
    try:
        return ASPECT_SIZES[key]
    except KeyError as exc:
        known = "、".join(ASPECT_SIZES)
        raise ValueError(f"不支持的画幅 {aspect or '空'}。可选：{known}") from exc


def compose_image_prompt(body: str, style: str, looks: dict[str, str], aspect: str = "16:9") -> str:
    picture = (body or "").strip() or "按台词呈现这一镜的环境与人物动作"
    series = (style or "").strip() or DEFAULT_STYLE
    bits = []
    for name, description in looks.items():
        if not name or name == "旁白":
            continue
        bits.append(f"{name}：{(description or '').strip() or '外观保持与本系列其他镜头一致'}")
    appearance = "\n".join(bits) if bits else "这一镜以环境和光影为主，不引入需要固定外形的新角色。"
    ratio = (aspect or "16:9").strip().replace("：", ":") or "16:9"
    return (
        f"画面：\n画幅 {ratio}。{picture}\n\n"
        f"STYLE\n{series}\n\n"
        f"角色外观必须保持一致\n{appearance}\n\n"
        f"COMMON\n{COMMON_CONSTRAINTS}"
    )


def inter_image_pause(index: int, provider_name: str, gap_sec: float) -> float:
    """真实出图在两张之间停一下。mock 不等待。"""
    if index <= 0 or provider_name == "mock" or gap_sec <= 0:
        return 0.0
    return float(gap_sec)
