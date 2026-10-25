"""把ESPN与Jolpica快照规范化为赛事看板统一JSON。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")
TEAM_NAMES = {
    "ARS": "阿森纳", "AVL": "维拉", "BOU": "伯恩茅斯", "BRE": "布伦特福德",
    "BHA": "布莱顿", "CHE": "切尔西", "COV": "考文垂", "CRY": "水晶宫",
    "EVE": "埃弗顿", "FUL": "富勒姆", "HUL": "赫尔城", "IPS": "伊普斯维奇",
    "LEE": "利兹联", "LIV": "利物浦", "MAN": "曼联", "MNC": "曼城",
    "NEW": "纽卡斯尔", "NFO": "诺丁汉森林", "SUN": "桑德兰", "TOT": "热刺"
}
F1_RACE_NAMES = {
    "United States Grand Prix": "美国大奖赛",
    "Mexico City Grand Prix": "墨西哥城大奖赛",
    "Brazilian Grand Prix": "巴西大奖赛",
    "Las Vegas Grand Prix": "拉斯维加斯大奖赛",
    "Qatar Grand Prix": "卡塔尔大奖赛",
    "Abu Dhabi Grand Prix": "阿布扎比大奖赛"
}
F1_CIRCUIT_NAMES = {
    "Circuit of the Americas": "美洲赛道"
}
SESSION_FIELDS = (
    ("FirstPractice", "一练"),
    ("SecondPractice", "二练"),
    ("ThirdPractice", "三练"),
    ("SprintQualifying", "冲刺排位"),
    ("Sprint", "冲刺赛"),
    ("Qualifying", "排位赛"),
)


def session_start(race: dict, key: str) -> datetime | None:
    part = race.get(key)
    if not part or not part.get("date"):
        return None
    raw = f"{part['date']}T{part.get('time', '00:00:00Z').replace('Z', '+00:00')}"
    return datetime.fromisoformat(raw).astimezone(TZ)


F1_LOCATION_NAMES = {
    "Singapore": "新加坡",
    "Austin": "奥斯汀",
    "Mexico City": "墨西哥城",
    "São Paulo": "圣保罗"
}


def local_iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ).isoformat()


def competitor_pair(event: dict) -> tuple[dict, dict]:
    competitors = event["competitions"][0]["competitors"]
    home = next(item for item in competitors if item["homeAway"] == "home")
    away = next(item for item in competitors if item["homeAway"] == "away")
    return home, away


LEAGUE_NAMES = {
    "eng.1": "英超",
    "esp.1": "西甲",
    "ita.1": "意甲",
    "ger.1": "德甲",
    "uefa.champions": "欧冠",
}

# 德比对阵（ESPN 球队 ID 无序对）→ 焦点标题
DERBY_TITLES = {
    frozenset({"83", "86"}): "西班牙国家德比",
    frozenset({"110", "111"}): "意大利国家德比",
    frozenset({"103", "110"}): "米兰德比",
}


def ucl_round_label(slug: str | None) -> str:
    """欧冠赛季阶段 slug → 中文轮次名；未知 slug 一律按淘汰赛处理。"""
    s = (slug or "").lower()
    if s in ("semifinal", "semifinals", "semi-final", "semi-finals"):
        return "欧冠半决赛"
    if s in ("quarterfinal", "quarterfinals", "quarter-final", "quarter-finals"):
        return "欧冠四分之一决赛"
    if s in ("round-of-16", "roundof16"):
        return "欧冠十六强战"
    if "playoff" in s or "play-off" in s:
        return "欧冠淘汰赛附加赛"
    if s in ("final", "finals") or s.endswith("final"):
        return "欧冠决赛"
    return "欧冠淘汰赛"


def team_name(item: dict, zh_by_id: dict | None = None) -> str:
    team = item["team"]
    zh = (zh_by_id or {}).get(str(team.get("id", "")))
    if zh:
        return zh
    abbreviation = team.get("abbreviation", "")
    return TEAM_NAMES.get(abbreviation, abbreviation or team.get("shortDisplayName", "--"))


def load_zh_by_id(root: Path | None) -> dict:
    if root is None:
        return {}
    path = root / "03_项目固定资源" / "team_names_zh.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def team_logo_name(team_id: str, root: Path | None) -> str | None:
    if root is None:
        return None
    path = root / "03_项目固定资源" / "赛事标识" / "球队" / f"{team_id}.png"
    return f"球队/{team_id}.png" if path.exists() else None


SESSION_NAMES_ZH = {
    "Practice 1": "一练",
    "Practice 2": "二练",
    "Practice 3": "三练",
    "Qualifying": "排位赛",
    "Race": "正赛",
    "Sprint Qualifying": "冲刺排位",
    "Sprint": "冲刺赛",
}


def f1_team_logo_name(team: str, root: Path | None) -> str | None:
    if root is None:
        return None
    path = root / "03_项目固定资源" / "赛事标识" / "车队" / f"{team}.png"
    return f"车队/{team}.png" if path.exists() else None


def driver_portrait_name(code: str, root: Path | None) -> str | None:
    if root is None:
        return None
    path = root / "03_项目固定资源" / "赛事标识" / "车手" / f"{code}.png"
    return f"车手/{code}.png" if path.exists() else None


def load_selection(root: Path | None) -> set | None:
    """选队文件存在才过滤；不存在返回 None（不过滤，兼容演示模式）。"""
    if root is None:
        return None
    path = root / "02_在制成果" / "runtime" / "team_selection.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return {str(x) for x in payload.get("teamIds", [])}


def build(snapshot_dir: Path, output: Path, now: datetime, mode: str = "demo", root: Path | None = None) -> None:
    zh_by_id = load_zh_by_id(root)
    selected = load_selection(root)
    league_events: list[tuple[str, dict]] = []
    for code in LEAGUE_NAMES:
        merged_path = snapshot_dir / f"espn_{code}_merged.json"
        if not merged_path.exists() and code == "eng.1":
            merged_path = snapshot_dir / "espn_epl_merged.json"
            if not merged_path.exists():
                merged_path = snapshot_dir / "espn_epl_merged_20261013_20261102.json"
        if merged_path.exists():
            payload = json.loads(merged_path.read_text())
            for event in payload.get("events", []):
                league_events.append((code, event))
    schedule = json.loads((snapshot_dir / "jolpica_f1_2026_schedule.json").read_text())
    last_results = json.loads((snapshot_dir / "jolpica_f1_2026_last_results.json").read_text())

    upcoming = []
    results = []
    for code, event in league_events:
        home, away = competitor_pair(event)
        home_id = str(home["team"].get("id", ""))
        away_id = str(away["team"].get("id", ""))
        if selected is not None and home_id not in selected and away_id not in selected:
            continue
        completed = bool(event.get("status", {}).get("type", {}).get("completed"))
        base = {
            "id": str(event["id"]),
            "sport": "SOCCER",
            "league": code,
            "leagueName": LEAGUE_NAMES[code],
            "startTime": local_iso(event["date"]),
            "homeId": home_id,
            "awayId": away_id,
            "homeLogo": team_logo_name(home_id, root),
            "awayLogo": team_logo_name(away_id, root),
            "home": team_name(home, zh_by_id),
            "away": team_name(away, zh_by_id),
            "status": "finished" if completed else "scheduled",
            "venue": event["competitions"][0].get("venue", {}).get("fullName"),
            "roundSlug": event.get("season", {}).get("slug")
        }
        if completed:
            results.append({
                **base,
                "homeScore": int(home.get("score") or 0),
                "awayScore": int(away.get("score") or 0)
            })
        elif datetime.fromisoformat(base["startTime"]) >= now:
            upcoming.append(base)

    upcoming.sort(key=lambda item: item["startTime"])
    results.sort(key=lambda item: item["startTime"], reverse=True)

    races = schedule["MRData"]["RaceTable"]["Races"]
    next_race = next(race for race in races if race["date"] >= now.date().isoformat())
    race_start = datetime.fromisoformat(f"{next_race['date']}T{next_race.get('time', '00:00:00Z').replace('Z', '+00:00')}").astimezone(TZ)
    delta = race_start - now
    days = max(0, delta.days)
    hours = max(0, delta.seconds // 3600)

    result_races = last_results["MRData"]["RaceTable"]["Races"]
    podium = []
    last_race_info = {"name": "--", "location": "--", "date": "1970-01-01"}
    if result_races:
        last_race = result_races[0]
        location_data = last_race.get("Circuit", {}).get("Location", {})
        locality = location_data.get("locality", "--")
        country = location_data.get("country", "")
        display_location = F1_LOCATION_NAMES.get(country) or F1_LOCATION_NAMES.get(locality, locality)
        last_race_info = {
            "name": last_race.get("raceName", "--"),
            "location": display_location,
            "date": last_race.get("date", "1970-01-01")
        }
        for item in last_race.get("Results", [])[:3]:
            code = item["Driver"].get("code") or item["Driver"]["familyName"]
            podium.append({
                "position": int(item["position"]),
                "name": code,
                "team": item["Constructor"]["name"],
                "portrait": driver_portrait_name(code, root)
            })

    sessions = []
    for key, label in SESSION_FIELDS:
        start_at = session_start(next_race, key)
        if start_at is not None:
            sessions.append({
                "name": label,
                "startTime": start_at.isoformat(),
                "past": start_at < now
            })
    sessions.append({
        "name": "正赛",
        "startTime": race_start.isoformat(),
        "past": race_start < now
    })

    session_top3 = None
    top3_path = snapshot_dir / "openf1_featured_session_top3.json"
    if top3_path.exists():
        raw = json.loads(top3_path.read_text())
        if raw.get("sessionName") and raw.get("top3"):
            session_top3 = {
                "sessionName": SESSION_NAMES_ZH.get(raw["sessionName"], raw["sessionName"]),
                "top3": [{
                    "position": item["position"],
                    "code": item["code"],
                    "team": item["team"],
                    "portrait": driver_portrait_name(item["code"], root),
                    "teamLogo": f1_team_logo_name(item["team"], root),
                } for item in raw["top3"]],
            }

    # 焦点赛事只放大赛（欧冠淘汰赛、德比），不放 F1；
    # upcoming 已按开球时间升序，第一个命中的就是最近的一场大赛，不限时间窗口
    football_featured = None
    for item in upcoming:
        if item["status"] != "scheduled":
            continue
        pair = frozenset({item["homeId"], item["awayId"]})
        title = DERBY_TITLES.get(pair)
        if title is None and item["league"] == "uefa.champions" and (item.get("roundSlug") or "") != "league-phase":
            title = ucl_round_label(item.get("roundSlug"))
        if title is None:
            continue
        kickoff = datetime.fromisoformat(item["startTime"])
        kickoff_delta = kickoff - now
        football_featured = {
            "sport": "FOOTBALL",
            "title": title,
            "league": item["league"],
            "leagueName": item["leagueName"],
            "startTime": item["startTime"],
            "venue": item.get("venue"),
            "countdownLabel": f"{max(0, kickoff_delta.days)}天{max(0, kickoff_delta.seconds // 3600):02d}小时",
            "homeId": item["homeId"],
            "awayId": item["awayId"],
            "home": item["home"],
            "away": item["away"],
            "homeLogo": item.get("homeLogo"),
            "awayLogo": item.get("awayLogo"),
        }
        break

    # F1赛事页：仅当下一场正赛在 7 天内才出现，否则本轮不展示该页
    f1_featured = {
        "sport": "F1",
        "title": F1_RACE_NAMES.get(next_race["raceName"], next_race["raceName"].replace("Grand Prix", "GP")),
        "startTime": race_start.isoformat(),
        "venue": F1_CIRCUIT_NAMES.get(next_race["Circuit"]["circuitName"], next_race["Circuit"]["circuitName"]),
        "countdownLabel": f"{days}天{hours:02d}小时",
        "sessions": sessions,
        "sessionTop3": session_top3
    } if race_start - now <= timedelta(days=7) else None

    data = {
        "meta": {
            "generatedAt": now.isoformat(),
            "timezone": "Asia/Shanghai",
            "mode": mode,
            "sources": [
                {"name": "ESPN Soccer", "status": "partial", "detail": "非官方接口；五联赛逐日快照，含重试与缓存"},
                {"name": "Jolpica F1", "status": "ok", "detail": "2026赛程及最近完赛结果"},
                {"name": "TheSportsDB", "status": "partial", "detail": "测试Key仅返回10队，不作为完整备源"}
            ]
        },
        "upcoming": upcoming[:10],
        "results": results[:8],
        "featured": football_featured if football_featured is not None else {"sport": "NONE", "title": "焦点赛事"},
        "f1Featured": f1_featured,
        "f1LastRace": last_race_info,
        "f1Podium": podium
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--now", help="模拟生成时间；省略时使用Asia/Shanghai当前时间")
    parser.add_argument("--mode", choices=("demo", "live", "cached"), default="demo")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(TZ)
    build(args.snapshot_dir, args.output, now, mode=args.mode)


if __name__ == "__main__":
    main()
