"""NUC每10分钟抓取赛事数据；仅在页面语义变化时原子更新三张PNG。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from build_demo_data import build
from render_sports_v2 import render_focus, render_marathon, render_results, render_schedule


TZ = ZoneInfo("Asia/Shanghai")

WEATHER_GLASS_DIR = Path(
    os.environ.get("WEATHER_GLASS_DIR", "/mnt/lexar512/apps/zectrix-weather-glass")
)
WEATHER_PAGE_SCRIPT = Path(__file__).with_name("weather_page.py")


def ensure_weather(root: Path) -> Path | None:
    """刷新天气图（weather-glass 渲染）；失败沿用上一张，绝不抛错拖垮体育更新。

    返回可用的天气 PNG 路径；从未成功渲染过则返回 None（本轮不带天气页）。
    天气渲染走 weather-glass 仓库自己的 .venv（依赖与缓存都在它那边，
    15 分钟缓存内不会真调 WeatherAPI）。单次尝试含其内部重试，
    子进程超时 180 秒封顶。
    """
    target = root / "02_在制成果" / "runtime" / "weather" / "weather.png"
    python = WEATHER_GLASS_DIR / ".venv" / "bin" / "python"
    try:
        if python.exists() and WEATHER_PAGE_SCRIPT.exists():
            result = subprocess.run(
                [str(python), str(WEATHER_PAGE_SCRIPT), str(target)],
                capture_output=True,
                timeout=180,
                check=False,
            )
            if result.returncode != 0:
                print("WEATHER_REFRESH_FAILED")
    except Exception:
        print("WEATHER_REFRESH_FAILED")
    return target if target.exists() else None


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def fetch_json(url: str, cache_path: Path) -> tuple[dict, bool]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".download")
    command = [
        "/usr/bin/curl", "--retry", "3", "--retry-all-errors",
        "--connect-timeout", "10", "--max-time", "30", "-fsSL",
        url, "-o", str(temporary)
    ]
    result = subprocess.run(command, check=False)
    if result.returncode == 0:
        try:
            payload = json.loads(temporary.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            # 远端偶尔返回带非法控制字符的残缺 JSON：丢弃本次结果，走缓存。
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, cache_path)
            return payload, True
    elif temporary.exists():
        temporary.unlink()
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), False
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    raise RuntimeError(f"抓取失败且没有缓存：{url}")


def fetch_featured_session_top3(sessions: list, snapshot_dir: Path, now) -> dict | None:
    """在 OpenF1 节次表中定位下一场正赛的 meeting，取其内最近完赛且有成绩的节次前三。"""
    now_utc = now.astimezone(timezone.utc)

    def parse(ts: str):
        return datetime.fromisoformat(ts)

    races = [s for s in sessions
             if s.get("session_name") == "Race" and parse(s["date_start"]) > now_utc]
    if not races:
        return None
    meeting_key = min(races, key=lambda s: parse(s["date_start"]))["meeting_key"]
    done = [s for s in sessions
            if s.get("meeting_key") == meeting_key and parse(s["date_end"]) < now_utc]
    done.sort(key=lambda s: parse(s["date_end"]), reverse=True)
    for session in done:
        key = session["session_key"]
        try:
            results, _ = fetch_json(
                f"https://api.openf1.org/v1/session_result?session_key={key}&position<=3",
                snapshot_dir / f"openf1_result_{key}.json")
            drivers, _ = fetch_json(
                f"https://api.openf1.org/v1/drivers?session_key={key}",
                snapshot_dir / f"openf1_drivers_{key}.json")
        except Exception:
            continue
        by_number = {d["driver_number"]: d for d in drivers}
        top3 = []
        for row in sorted(results, key=lambda r: r["position"]):
            driver = by_number.get(row["driver_number"])
            if not driver:
                continue
            top3.append({"position": int(row["position"]),
                         "code": driver["name_acronym"],
                         "team": driver["team_name"]})
        if top3:
            return {"sessionName": session["session_name"],
                    "sessionKey": key,
                    "top3": top3}
    return None


def semantic(data: dict) -> dict:
    return {key: data.get(key) for key in ("upcoming", "results", "featured", "f1LastRace", "f1Podium", "f1Featured")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--track-geojson", type=Path, required=True)
    parser.add_argument("--f1-logo", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    now = datetime.now(TZ)
    snapshot_dir = root / "01_当前有效资料" / "runtime_snapshots"
    failures = []
    logo_targets = {}
    league_codes = ["eng.1", "esp.1", "ita.1", "ger.1", "uefa.champions"]
    for code in league_codes:
        daily_dir = snapshot_dir / f"espn_daily_{code}"
        events = []
        seen = set()
        for offset in range(-7, 11):
            day = (now.date() + timedelta(days=offset)).strftime("%Y%m%d")
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={day}"
            payload, fresh = fetch_json(url, daily_dir / f"{day}.json")
            if not fresh:
                failures.append(f"{code}:{day}")
            for event in payload.get("events", []):
                if event.get("id") not in seen:
                    seen.add(event.get("id"))
                    events.append(event)
        events.sort(key=lambda item: item.get("date", ""))
        merged = {
            "source": "ESPN unofficial scoreboard",
            "league": code,
            "fetchedAt": now.isoformat(),
            "failures": [f for f in failures if f.startswith(code + ":")],
            "events": events,
        }
        atomic_json(snapshot_dir / f"espn_{code}_merged.json", merged)
        if code == "eng.1":
            atomic_json(snapshot_dir / "espn_epl_merged.json", merged)
        for event in events:
            for competitor in event["competitions"][0]["competitors"]:
                team = competitor["team"]
                if team.get("logo"):
                    logo_targets[str(team["id"])] = team["logo"]

    fetch_json(
        f"https://api.jolpi.ca/ergast/f1/{now.year}.json",
        snapshot_dir / "jolpica_f1_2026_schedule.json",
    )
    fetch_json(
        f"https://api.jolpi.ca/ergast/f1/{now.year}/last/results.json",
        snapshot_dir / "jolpica_f1_2026_last_results.json",
    )

    # 队徽：缓存所有在抓取窗口内出现过的球队（比赛入选只要任一队被选中，
    # 对手队徽也必须有），缺失才下载；失败不影响主流程（渲染时跳过该队徽）。
    team_dir = root / "03_项目固定资源" / "赛事标识" / "球队"
    team_dir.mkdir(parents=True, exist_ok=True)
    for team_id, url in sorted(logo_targets.items()):
        dest = team_dir / f"{team_id}.png"
        if dest.exists():
            continue
        result = subprocess.run(["curl", "-fsSL", "--max-time", "20", url, "-o", str(dest)],
                                capture_output=True)
        if result.returncode != 0:
            dest.unlink(missing_ok=True)
            print(f"TEAMLOGO_FAILED\t{team_id}")

    # F1 车手头像：OpenF1 免 Key；失败只跳过本步，渲染回退名次圆圈。
    try:
        drivers, _fresh = fetch_json(
            "https://api.openf1.org/v1/drivers?session_key=latest",
            snapshot_dir / "openf1_drivers.json",
        )
    except Exception as error:
        print(f"OPENF1_FAILED\t{error}")
        drivers = []
    if drivers:
        portrait_dir = root / "03_项目固定资源" / "赛事标识" / "车手"
        portrait_dir.mkdir(parents=True, exist_ok=True)
        for driver in drivers:
            code = driver.get("name_acronym")
            url = driver.get("headshot_url")
            if not code or not url:
                continue
            dest = portrait_dir / f"{code}.png"
            if dest.exists():
                continue
            result = subprocess.run(["curl", "-fsSL", "--max-time", "20", url, "-o", str(dest)],
                                    capture_output=True)
            if result.returncode != 0:
                dest.unlink(missing_ok=True)
                print(f"PORTRAIT_FAILED\t{code}")

    # 焦点站最新已完赛节次前三（OpenF1）；失败则写空快照，焦点页不画该区块。
    session_top3 = None
    try:
        sessions, _ = fetch_json(
            f"https://api.openf1.org/v1/sessions?year={now.year}",
            snapshot_dir / f"openf1_sessions_{now.year}.json")
        session_top3 = fetch_featured_session_top3(sessions, snapshot_dir, now)
    except Exception as error:
        print(f"OPENF1_SESSIONS_FAILED\t{error}")
    atomic_json(snapshot_dir / "openf1_featured_session_top3.json",
                session_top3 or {"sessionName": None, "top3": []})

    next_data = root / "02_在制成果" / "runtime" / "sports.next.json"
    current_data = root / "02_在制成果" / "runtime" / "sports.json"
    build(snapshot_dir, next_data, now, mode="live", root=root)
    new_payload = json.loads(next_data.read_text())
    # 天气页：先刷新天气图并确定本轮是否带天气页，后面的总页数计算要用。
    weather_png = ensure_weather(root)
    has_weather = weather_png is not None
    # 马拉松页：保留到 2026-11-02 12:00；倒计时只到小时，体育数据不变时也要单独重渲，避免冻结。
    marathon_cutoff = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
    marathon_active = now < marathon_cutoff
    # 总页数变化时（如新增/下线马拉松页、F1 页进出），必须全量重渲，否则 01-04 的页码分母仍是旧总数。
    # 上次全量渲染的总页数存在 runtime/last_total_pages.txt；不存在时视为已变，强制全量一次。
    _total_file = root / "02_在制成果" / "runtime" / "last_total_pages.txt"
    try:
        _last_total = int(_total_file.read_text().strip())
    except Exception:
        _last_total = None
    _expected_total_pre = 3 + (1 if new_payload.get("f1Featured") is not None else 0) + (1 if marathon_active else 0) + (1 if has_weather else 0)
    _total_changed = (_last_total != _expected_total_pre)
    marathon_map = root / "03_项目固定资源" / "hangzhou_marathon_2026_map_gray16.png"
    page_dir_pre = root / "02_在制成果" / "runtime" / "pages"
    marathon_page_pre = page_dir_pre / "05_marathon.png"
    if current_data.exists():
        current_payload = json.loads(current_data.read_text())
        if semantic(new_payload) == semantic(current_payload) and not _total_changed:
            if marathon_active:
                # 先算本轮总页数（F1 页存在与否由新 payload 定），只重渲马拉松页。
                # 马拉松角标写自己的序号：天气页在它之后，它不再是末页。
                _has_f1 = new_payload.get("f1Featured") is not None
                _total = 3 + (1 if _has_f1 else 0) + 1 + (1 if has_weather else 0)
                _marathon_no = 3 + (1 if _has_f1 else 0) + 1
                _tmp = root / "02_在制成果" / "runtime" / "next_pages"
                _tmp.mkdir(parents=True, exist_ok=True)
                render_marathon(_tmp / "05_marathon.tmp.png", args.font, marathon_map, page_label=f"{_marathon_no} / {_total}")
                os.replace(_tmp / "05_marathon.tmp.png", marathon_page_pre)
            else:
                marathon_page_pre.unlink(missing_ok=True)
            if has_weather:
                # 体育数据没变也要让天气图保持新鲜：内容变了才原子替换。
                _weather_page = page_dir_pre / "06_weather.png"
                _weather_bytes = weather_png.read_bytes()
                if not _weather_page.exists() or _weather_page.read_bytes() != _weather_bytes:
                    _tmp = root / "02_在制成果" / "runtime" / "next_pages"
                    _tmp.mkdir(parents=True, exist_ok=True)
                    (_tmp / "06_weather.tmp.png").write_bytes(_weather_bytes)
                    os.replace(_tmp / "06_weather.tmp.png", _weather_page)
            next_data.unlink()
            print("UNCHANGED")
            return 3

    page_dir = root / "02_在制成果" / "runtime" / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir = root / "02_在制成果" / "runtime" / "next_pages"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    logos_dir = root / "03_项目固定资源" / "赛事标识"
    has_f1_page = new_payload.get("f1Featured") is not None
    total_pages = 3 + (1 if has_f1_page else 0) + (1 if marathon_active else 0) + (1 if has_weather else 0)
    render_schedule(new_payload, temporary_dir / "01_schedule.tmp.png", args.font, logos_dir, page_label=f"1 / {total_pages}")
    render_results(new_payload, temporary_dir / "02_results.tmp.png", args.font, args.f1_logo, logos_dir, page_label=f"2 / {total_pages}")
    render_focus(
        new_payload,
        temporary_dir / "03_focus.tmp.png",
        args.font,
        args.track_geojson,
        args.f1_logo,
        logos_dir,
        page_label=f"3 / {total_pages}",
    )
    replace_pairs = [
        (temporary_dir / "01_schedule.tmp.png", page_dir / "01_schedule.png"),
        (temporary_dir / "02_results.tmp.png", page_dir / "02_results.png"),
        (temporary_dir / "03_focus.tmp.png", page_dir / "03_focus.png"),
    ]
    if has_f1_page:
        f1_payload = {**new_payload, "featured": new_payload["f1Featured"]}
        render_focus(
            f1_payload,
            temporary_dir / "04_f1_focus.tmp.png",
            args.font,
            args.track_geojson,
            args.f1_logo,
            logos_dir,
            title="F1赛事",
            page_label=f"4 / {total_pages}",
        )
        replace_pairs.append((temporary_dir / "04_f1_focus.tmp.png", page_dir / "04_f1_focus.png"))
    if marathon_active:
        # 天气页排在马拉松之后，马拉松角标写自己的序号而非总数。
        _marathon_no = 3 + (1 if has_f1_page else 0) + 1
        render_marathon(
            temporary_dir / "05_marathon.tmp.png",
            args.font,
            marathon_map,
            page_label=f"{_marathon_no} / {total_pages}",
        )
        replace_pairs.append((temporary_dir / "05_marathon.tmp.png", page_dir / "05_marathon.png"))
    if has_weather:
        # 天气图用 weather-glass 的原灰阶 PNG，不在本侧二值化或抖色。
        (temporary_dir / "06_weather.tmp.png").write_bytes(weather_png.read_bytes())
        replace_pairs.append((temporary_dir / "06_weather.tmp.png", page_dir / "06_weather.png"))
    for source, destination in replace_pairs:
        os.replace(source, destination)
    if not has_f1_page:
        (page_dir / "04_f1_focus.png").unlink(missing_ok=True)
    if not marathon_active:
        (page_dir / "05_marathon.png").unlink(missing_ok=True)
    if not has_weather:
        (page_dir / "06_weather.png").unlink(missing_ok=True)
    os.replace(next_data, current_data)
    _total_file.write_text(str(total_pages) + "\n")
    print(f"UPDATED {now.isoformat()} failures={len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
