"""渲染赛事看板Pro三页400×300连续灰度预览。"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


WIDTH, HEIGHT = 400, 300


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def fit_text(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> str:
    if draw.textbbox((0, 0), text, font=fnt)[2] <= width:
        return text
    value = text
    while value and draw.textbbox((0, 0), value + "…", font=fnt)[2] > width:
        value = value[:-1]
    return value + "…"


def canvas(title: str, page: str, font_path: Path) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (WIDTH, HEIGHT), 246)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 43), fill=22)
    draw.text((11, 7), title, fill=255, font=font(font_path, 24))
    page_w = draw.textbbox((0, 0), page, font=font(font_path, 14))[2]
    draw.text((389 - page_w, 14), page, fill=210, font=font(font_path, 14))
    draw.rectangle((0, 272, WIDTH, HEIGHT), fill=32)
    return image, draw


def footer(draw: ImageDraw.ImageDraw, text: str, font_path: Path) -> None:
    draw.text((10, 278), text, fill=230, font=font(font_path, 12))


def draw_countdown(
    draw: ImageDraw.ImageDraw, label: str, x: int, y: int, font_path: Path
) -> None:
    """以固定组内、组间距绘制“2天9小时”，不依赖字体空格。"""
    match = re.fullmatch(r"(\d+)天\s*(\d+)小时", label)
    if not match:
        draw.text((x, y), label, fill=0, font=font(font_path, 42))
        return
    number_font = font(font_path, 44)
    unit_font = font(font_path, 20)
    cursor = x
    groups = ((match.group(1), "天"), (match.group(2), "小时"))
    for index, (number, unit) in enumerate(groups):
        draw.text((cursor, y), number, fill=0, font=number_font)
        number_width = draw.textbbox((0, 0), number, font=number_font)[2]
        cursor += number_width + 4
        draw.text((cursor, y + 19), unit, fill=20, font=unit_font)
        unit_width = draw.textbbox((0, 0), unit, font=unit_font)[2]
        cursor += unit_width
        if index == 0:
            cursor += 18


def paste_white_logo(image: Image.Image, logo_path: Path, box: tuple[int, int, int, int]) -> None:
    """把透明Logo等比缩放后以白色贴入灰度页面。"""
    source = Image.open(logo_path).convert("RGBA")
    left, top, right, bottom = box
    fitted = ImageOps.contain(source, (right - left, bottom - top), Image.Resampling.LANCZOS)
    alpha = fitted.getchannel("A")
    white = Image.new("L", fitted.size, 252)
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    image.paste(white, (x, y), alpha)


def paste_portrait(image: Image.Image, portrait_path: Path | None, box: tuple[int, int, int, int]) -> bool:
    """把车手头像圆形裁剪后贴到指定方框；无图返回 False（调用方回退名次圆圈）。"""
    if portrait_path is None or not portrait_path.exists():
        return False
    left, top, right, bottom = box
    size = right - left
    source = Image.open(portrait_path).convert("L")
    width, height = source.size
    side = min(width, height)
    source = source.crop(((width - side) // 2, (height - side) // 2,
                          (width + side) // 2, (height + side) // 2))
    source = ImageOps.autocontrast(source, cutoff=1)
    source = source.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, size, size), fill=255)
    image.paste(source, (left, top), mask)
    return True


def paste_logo_alpha(image: Image.Image, logo_path: Path | None, box: tuple[int, int, int, int]) -> bool:
    """把带透明通道的 Logo 灰度贴到指定框（渐变底上不留方块底色）；无图返回 False。"""
    if logo_path is None or not logo_path.exists():
        return False
    left, top, right, bottom = box
    source = Image.open(logo_path).convert("RGBA")
    fitted = ImageOps.contain(source, (right - left, bottom - top), Image.Resampling.LANCZOS)
    gray = fitted.convert("L")
    alpha = fitted.getchannel("A")
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    image.paste(gray, (x, y), alpha)
    return True


def paste_logo_on(image: Image.Image, logo_path: Path | None, box: tuple[int, int, int, int], bg_fill: int) -> None:
    """把带透明通道的 Logo 等比贴到指定底色的方块里（灰度页）。"""
    if logo_path is None or not logo_path.exists():
        return
    source = Image.open(logo_path).convert("RGBA")
    left, top, right, bottom = box
    fitted = ImageOps.contain(source, (right - left, bottom - top), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", fitted.size, (bg_fill, bg_fill, bg_fill, 255))
    tile.alpha_composite(fitted)
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    image.paste(tile.convert("L"), (x, y))


def render_schedule(data: dict[str, Any], output: Path, font_path: Path, logos_dir: Path | None = None, page_label: str = "1 / 4") -> None:
    image, draw = canvas("未来赛程", page_label, font_path)
    label_font = font(font_path, 14)
    body_font = font(font_path, 17)
    items = data["upcoming"][:6]
    if not items:
        draw.text((14, 130), "所选球队近期暂无赛程", fill=60, font=font(font_path, 16))
    y = 50
    current_date = None
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for index, item in enumerate(items):
        start = datetime.fromisoformat(item["startTime"])
        date_label = start.strftime("%m/%d")
        if date_label != current_date:
            draw.rectangle((7, y, 55, y + 29), fill=75)
            draw.text((13, y + 1), date_label, fill=255, font=label_font)
            draw.text((13, y + 16), weekdays[start.weekday()], fill=255, font=font(font_path, 12))
            current_date = date_label
        row_fill = 235 if index % 2 == 0 else 218
        draw.rounded_rectangle((61, y, 393, y + 29), radius=4, fill=row_fill)
        if logos_dir is not None and item.get("league"):
            paste_logo_on(image, logos_dir / "联赛" / f"{item['league']}.png", (64, y + 6, 80, y + 22), row_fill)
        draw.text((84, y + 5), start.strftime("%H:%M"), fill=55, font=label_font)
        name_font = font(font_path, 15)
        if logos_dir is not None and item.get("homeLogo"):
            paste_logo_on(image, logos_dir / item["homeLogo"], (123, y + 3, 145, y + 25), row_fill)
        draw.text((149, y + 4), fit_text(draw, item["home"], name_font, 92), fill=12, font=name_font)
        draw.text((245, y + 5), "vs", fill=55, font=label_font)
        if logos_dir is not None and item.get("awayLogo"):
            paste_logo_on(image, logos_dir / item["awayLogo"], (265, y + 3, 287, y + 25), row_fill)
        draw.text((291, y + 4), fit_text(draw, item["away"], name_font, 98), fill=12, font=name_font)
        y += 34
    gen_label = datetime.fromisoformat(data['meta']['generatedAt']).strftime('%m/%d %H:%M')
    footer(draw, f"更新 {gen_label}" if data["meta"].get("mode") == "live" else f"演示快照 · 更新 {gen_label}", font_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)


def render_results(data: dict[str, Any], output: Path, font_path: Path, f1_logo: Path, logos_dir: Path | None = None, page_label: str = "2 / 4") -> None:
    image, draw = canvas("最近赛果", page_label, font_path)
    small = font(font_path, 12)
    team = font(font_path, 14)
    score = font(font_path, 24)
    draw.text((10, 50), "足球", fill=30, font=font(font_path, 15))
    if not data["results"]:
        draw.text((14, 130), "所选球队暂无赛果", fill=60, font=font(font_path, 15))
    for index, item in enumerate(data["results"][:8]):
        y = 73 + index * 24
        row_fill = 232 if index % 2 == 0 else 214
        draw.rounded_rectangle((7, y, 254, y + 21), radius=4, fill=row_fill)
        if logos_dir is not None and item.get("league"):
            paste_logo_on(image, logos_dir / "联赛" / f"{item['league']}.png", (10, y + 2, 28, y + 20), row_fill)
        draw.text((32, y + 5), datetime.fromisoformat(item["startTime"]).strftime("%m/%d"), fill=80, font=font(font_path, 11))
        name_font = font(font_path, 12)
        draw.text((64, y + 4), fit_text(draw, item["home"], name_font, 73), fill=20, font=name_font)
        draw.text((141, y + 3), f"{item['homeScore']}:{item['awayScore']}", fill=5, font=font(font_path, 15))
        draw.text((174, y + 4), fit_text(draw, item["away"], name_font, 76), fill=20, font=name_font)
    draw.rounded_rectangle((265, 50, 394, 257), radius=7, fill=55)
    paste_white_logo(image, f1_logo, (276, 56, 382, 79))
    location = str(data.get("f1LastRace", {}).get("location", "--"))
    draw.text((278, 82), fit_text(draw, location, font(font_path, 20), 103), fill=255, font=font(font_path, 20))
    draw.line((276, 108, 383, 108), fill=140, width=1)
    podium_y = [119, 161, 203]
    medals = [225, 170, 115]
    for index, item in enumerate(data["f1Podium"][:3]):
        y = podium_y[index]
        portrait = item.get("portrait")
        shown = False
        if logos_dir is not None and portrait:
            shown = paste_portrait(image, logos_dir / portrait, (270, y - 4, 310, y + 36))
        if shown:
            draw.ellipse((270, y - 4, 310, y + 36), outline=medals[index], width=2)
        else:
            draw.ellipse((276, y, 304, y + 28), fill=medals[index])
            draw.text((285, y + 4), str(item["position"]), fill=10, font=font(font_path, 14))
        draw.text((316, y + 1), fit_text(draw, item["name"], font(font_path, 14), 72), fill=255, font=font(font_path, 14))
        draw.text((316, y + 20), fit_text(draw, item["team"], small, 72), fill=195, font=small)
    footer(draw, "ESPN · Jolpica · 缓存可用", font_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)


def load_track_points(geojson_path: Path, box: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """把赛道GeoJSON坐标等比缩放到指定框内。"""
    payload = json.loads(geojson_path.read_text())
    coordinates = payload["features"][0]["geometry"]["coordinates"]
    mean_lat = sum(point[1] for point in coordinates) / len(coordinates)
    projected = [(point[0] * math.cos(math.radians(mean_lat)), point[1]) for point in coordinates]
    min_x = min(point[0] for point in projected)
    max_x = max(point[0] for point in projected)
    min_y = min(point[1] for point in projected)
    max_y = max(point[1] for point in projected)
    left, top, right, bottom = box
    scale = min((right - left) / (max_x - min_x), (bottom - top) / (max_y - min_y))
    width = (max_x - min_x) * scale
    height = (max_y - min_y) * scale
    offset_x = left + ((right - left) - width) / 2
    offset_y = top + ((bottom - top) - height) / 2
    return [
        (
            round(offset_x + (x - min_x) * scale),
            round(offset_y + (max_y - y) * scale)
        )
        for x, y in projected
    ]


def render_focus(
    data: dict[str, Any], output: Path, font_path: Path, track_geojson: Path, f1_logo: Path,
    logos_dir: Path | None = None, title: str = "焦点赛事", page_label: str = "3 / 4"
) -> None:
    image, draw = canvas(title, page_label, font_path)
    featured = data["featured"]
    # 16灰阶背景带，展示设备层次，同时避免使用受版权保护的海报素材。
    for x in range(WIDTH):
        shade = round(225 - 65 * x / (WIDTH - 1))
        draw.line((x, 44, x, 271), fill=shade)
    if featured.get("sport") == "NONE":
        # 近期无大赛：焦点页只显示空态，不放 F1
        msg = "近期暂无焦点大赛"
        msg_font = font(font_path, 20)
        msg_w = draw.textbbox((0, 0), msg, font=msg_font)[2]
        draw.text(((WIDTH - msg_w) // 2, 140), msg, fill=25, font=msg_font)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, optimize=True)
        return
    if featured.get("sport") == "FOOTBALL":
        # 足球焦点：联赛徽标＋大赛标题＋主客队徽对阵＋开球时间与倒计时
        league = featured.get("league")
        logo_shown = False
        if logos_dir is not None and league:
            league_logo = logos_dir / "联赛" / f"{league}.png"
            if league_logo.exists():
                logo_shown = bool(paste_logo_alpha(image, league_logo, (232, 50, 392, 114)))
        if not logo_shown and featured.get("leagueName"):
            league_w = draw.textbbox((0, 0), featured["leagueName"], font=font(font_path, 16))[2]
            draw.text((392 - league_w, 58), featured["leagueName"], fill=25, font=font(font_path, 16))
        draw.text((14, 84), fit_text(draw, featured["title"], font(font_path, 26), 212), fill=5, font=font(font_path, 26))

        def crest_or_char(team_id, logo_rel, box, name):
            if logos_dir is not None:
                path = logos_dir / "球队" / f"{team_id}.png"
                if not path.exists() and logo_rel:
                    path = logos_dir / logo_rel
                if paste_logo_alpha(image, path, box):
                    return
            ch = name[:1] if name else "?"
            ch_font = font(font_path, 40)
            ch_w = draw.textbbox((0, 0), ch, font=ch_font)[2]
            cx = (box[0] + box[2]) // 2
            draw.text((cx - ch_w // 2, box[1] + 12), ch, fill=25, font=ch_font)

        crest_or_char(featured.get("homeId"), featured.get("homeLogo"), (44, 128, 108, 192), featured.get("home", ""))
        crest_or_char(featured.get("awayId"), featured.get("awayLogo"), (292, 128, 356, 192), featured.get("away", ""))
        home_w = draw.textbbox((0, 0), featured.get("home", ""), font=font(font_path, 16))[2]
        draw.text((76 - home_w // 2, 196), featured.get("home", ""), fill=25, font=font(font_path, 16))
        away_w = draw.textbbox((0, 0), featured.get("away", ""), font=font(font_path, 16))[2]
        draw.text((324 - away_w // 2, 196), featured.get("away", ""), fill=25, font=font(font_path, 16))
        if featured.get("finished"):
            draw.text((186, 146), f"{featured['homeScore']}:{featured['awayScore']}", fill=25, font=font(font_path, 28))
        else:
            draw.text((186, 146), "VS", fill=25, font=font(font_path, 28))
        start = datetime.fromisoformat(featured["startTime"])
        weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        kickoff = f"{start.strftime('%m月%d日')} {weekdays[start.weekday()]} {start.strftime('%H:%M')} 开球"
        kickoff_w = draw.textbbox((0, 0), kickoff, font=font(font_path, 17))[2]
        draw.text(((WIDTH - kickoff_w) // 2, 222), kickoff, fill=25, font=font(font_path, 17))
        if featured.get("finished"):
            countdown = "已结束"
        else:
            countdown = f"还有 {featured['countdownLabel']}"
        countdown_w = draw.textbbox((0, 0), countdown, font=font(font_path, 17))[2]
        draw.text(((WIDTH - countdown_w) // 2, 244), countdown, fill=25, font=font(font_path, 17))
        footer_text = featured.get("leagueName", "")
        if featured.get("venue"):
            footer_text = f"{footer_text} · {featured['venue']}" if footer_text else featured["venue"]
        footer(draw, fit_text(draw, footer_text, font(font_path, 12), 380), font_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, optimize=True)
        return
    draw.rounded_rectangle((12, 54, 88, 82), radius=5, fill=28)
    paste_white_logo(image, f1_logo, (18, 59, 82, 77))
    draw.text((14, 84), fit_text(draw, featured["title"], font(font_path, 26), 365), fill=5, font=font(font_path, 26))
    if featured.get("finished"):
        draw.text((14, 139), "已结束", fill=0, font=font(font_path, 42))
    else:
        draw.text((14, 122), "距离开赛", fill=55, font=font(font_path, 15))
        draw_countdown(draw, featured["countdownLabel"], 14, 139, font_path)
    # 最新已完赛节次前三：标题行右侧、赛道图上方的空位（头像＋代号＋车队标，无标则队名）。
    top3 = featured.get("sessionTop3")
    if top3:
        draw.text((238, 48), f"{top3['sessionName']}前三", fill=25, font=font(font_path, 13))
        for row, item in enumerate(top3["top3"][:3]):
            y = 66 + row * 22
            if logos_dir is not None and item.get("portrait"):
                paste_portrait(image, logos_dir / item["portrait"], (238, y, 258, y + 20))
            draw.text((262, y + 3), item["code"], fill=25, font=font(font_path, 13))
            draw.text((296, y + 3), fit_text(draw, item["team"], font(font_path, 11), 92), fill=25, font=font(font_path, 11))
    # 周末赛程（各节次北京时间）：两列三行，已结束的节次用浅灰。
    weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    session_font = font(font_path, 13)
    for index, session in enumerate(featured.get("sessions", [])[:6]):
        start = datetime.fromisoformat(session["startTime"])
        label = f"{session['name']} {start.strftime('%m/%d')} {weekdays[start.weekday()]} {start.strftime('%H:%M')}"
        x = 14 + (index // 3) * 196
        y = 212 + (index % 3) * 21
        draw.text((x, y), label, fill=140 if session.get("past") else 25, font=session_font)
    # 使用MIT许可的真实赛道GeoJSON坐标，不再手工近似轮廓。
    track = load_track_points(track_geojson, (238, 132, 392, 196))
    draw.line(track, fill=245, width=9, joint="curve")
    draw.line(track, fill=45, width=3, joint="curve")
    footer(draw, f"{featured['venue']} · 赛道示意", font_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--track-geojson", type=Path, required=True)
    parser.add_argument("--f1-logo", type=Path, required=True)
    parser.add_argument("--logos-dir", type=Path)
    args = parser.parse_args()
    data = json.loads(args.data.read_text())
    render_schedule(data, args.output_dir / "01_赛程页_v2.png", args.font, args.logos_dir)
    render_results(data, args.output_dir / "02_赛果页_v2.png", args.font, args.f1_logo, args.logos_dir)
    render_focus(
        data,
        args.output_dir / "03_焦点页_v2.png",
        args.font,
        args.track_geojson,
        args.f1_logo,
    )


if __name__ == "__main__":
    main()
