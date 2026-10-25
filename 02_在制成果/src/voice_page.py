#!/mnt/lexar512/apps/zectrix-challenge/.venv/bin/python
"""P0: 语音临时页渲染（400x300 灰度 PNG）。"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 400, 300
FONT_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "zfull_GB.ttf"


def wrap(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if draw.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("voice_page.png")
    title = sys.argv[3] if len(sys.argv) > 3 else "语音识别"
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(str(FONT_PATH), 14)
    body = ImageFont.truetype(str(FONT_PATH), 20)
    foot = ImageFont.truetype(str(FONT_PATH), 12)
    draw.text((16, 12), title, font=title_font, fill=0)
    draw.line([(16, 34), (384, 34)], fill=0, width=1)
    lines = wrap(draw, text, body, 368)[:8]
    y = 48
    for ln in lines:
        draw.text((16, y), ln, font=body, fill=0)
        y += 28
    draw.text((16, 278), "按任意键返回", font=foot, fill=0)
    img.save(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
