from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FontInfo:
    file: Path
    family: str


_CANDIDATES = (
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), "Noto Sans CJK SC"),
    (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"), "Noto Sans CJK SC"),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"), "Noto Sans CJK SC"),
    (Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"), "WenQuanYi Micro Hei"),
    (Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"), "Droid Sans Fallback"),
)


def find_cjk_font() -> FontInfo | None:
    for path, family in _CANDIDATES:
        if path.is_file():
            return FontInfo(path, family)
    return None
