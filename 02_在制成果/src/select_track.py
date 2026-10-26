#!/usr/bin/env python3
"""按当前 F1 分站选赛道图文件（供 update_live.sh 调用）。

规则与 build_demo_data.py 的 next_race 选择一致：取 Jolpica 赛程快照中，
正赛北京时间日期当天 12:00 + 1 天 >= now 的第一场，作为当前分站；
按其 Circuit.circuitName 映射到 circuit_data 下的 GeoJSON。
任何异常（快照缺失、解析失败、无匹配分站、文件不存在）一律回退 COTA，
绝不让选图失败拖垮更新主流程。

用法：
  select_track.py --snapshot <jolpica_f1_2026_schedule.json> --circuit-dir <circuit_data>
  [--now <ISO 时间，默认当前北京时间，仅供离线测试>]
输出：选中文件的绝对路径（单行，stdout）。
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

# 分站赛道名 -> circuit_data 文件名；未列出的分站回退 COTA。
TRACK_FILES = {
    "Circuit of the Americas": "cota_us_2012.geojson",
    "Autódromo Hermanos Rodríguez": "mx-1962.geojson",
}
FALLBACK = "cota_us_2012.geojson"


def race_start_of(race: dict) -> datetime:
    raw = f"{race['date']}T{race.get('time', '00:00:00Z').replace('Z', '+00:00')}"
    return datetime.fromisoformat(raw).astimezone(TZ)


def select(snapshot: Path, circuit_dir: Path, now: datetime) -> Path:
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        races = data["MRData"]["RaceTable"]["Races"]
        next_race = next(
            race
            for race in races
            if race_start_of(race).replace(hour=12, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
            >= now
        )
        name = TRACK_FILES.get(next_race["Circuit"]["circuitName"], FALLBACK)
    except Exception as error:  # 快照缺失/格式异常/无匹配分站 -> 回退
        print(f"select_track: fallback to COTA ({error})", file=sys.stderr)
        name = FALLBACK
    candidate = circuit_dir / name
    if not candidate.exists():
        print(f"select_track: {candidate} missing, fallback to COTA", file=sys.stderr)
        candidate = circuit_dir / FALLBACK
    return candidate


def main(argv: list[str]) -> int:
    snapshot = None
    circuit_dir = None
    now = datetime.now(TZ)
    i = 0
    while i < len(argv):
        if argv[i] == "--snapshot":
            i += 1
            snapshot = Path(argv[i])
        elif argv[i] == "--circuit-dir":
            i += 1
            circuit_dir = Path(argv[i])
        elif argv[i] == "--now":
            i += 1
            now = datetime.fromisoformat(argv[i].replace("Z", "+00:00"))
            if now.tzinfo is None:
                now = now.replace(tzinfo=TZ)
            else:
                now = now.astimezone(TZ)
        i += 1
    if snapshot is None or circuit_dir is None:
        print("用法：select_track.py --snapshot <快照> --circuit-dir <目录> [--now <ISO>]", file=sys.stderr)
        return 2
    print(select(snapshot, circuit_dir, now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
