from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "desktop" / "src-tauri" / "icons"


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    size = 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, 492, 492), radius=112, fill="#102a43")
    draw.rounded_rectangle((74, 82, 438, 430), radius=52, fill="#f5fbfc")
    bars = ((120, 310, 172, 382), (206, 250, 258, 382), (292, 176, 344, 382))
    for bar in bars:
        draw.rounded_rectangle(bar, radius=18, fill="#087e8b")
    draw.line((118, 230, 218, 164, 298, 206, 388, 116), fill="#e29b35", width=30, joint="curve")
    draw.polygon(((388, 116), (335, 123), (381, 169)), fill="#e29b35")
    image.save(ICONS / "icon.png")
    image.save(ICONS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
