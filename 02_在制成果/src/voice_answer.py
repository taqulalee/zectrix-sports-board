#!/mnt/lexar512/apps/zectrix-challenge/.venv/bin/python
"""P1: 语音问答（本地 sports.json，无网络）。
用法：voice_answer.py <识别文本>，打印答案；无匹配则不输出。"""
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SPORTS = ROOT / "runtime" / "sports.json"

ALIASES = {
    "巴萨": "巴塞罗那",
    "皇马": "皇家马德里",
    "国米": "国际米兰",
    "米兰": "AC米兰",
    "尤文": "尤文图斯",
    "拜仁": "拜仁慕尼黑",
    "巴黎": "巴黎圣日耳曼",
    "曼联": "曼联",
}
WEEK = "一二三四五六日"


def fmt_time(iso):
    dt = datetime.fromisoformat(iso)
    return f"{dt.month}月{dt.day}日周{WEEK[dt.weekday()]} {dt.hour:02d}:{dt.minute:02d}"


def load():
    try:
        return json.loads(SPORTS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_team(text, data):
    names = []
    for m in data.get("upcoming", []) + data.get("results", []):
        for k in ("home", "away"):
            if m.get(k) and m[k] not in names:
                names.append(m[k])
    for n in names:  # 全名优先（国际米兰含米兰，须先于简称）
        if n in text:
            return n
    for short, full in ALIASES.items():
        if short in text:
            return full
    return None


def team_match(m, team):
    return team is None or m.get("home") == team or m.get("away") == team


def answer_next(data, team):
    for m in data.get("upcoming", []):
        if team_match(m, team):
            head = f"{team}下一场：" if team else "下一场："
            return f"{head}\n{m.get('leagueName', '')} {fmt_time(m['startTime'])}\n{m.get('home', '')} vs {m.get('away', '')}"
    return f"近期暂无{team}的比赛" if team else "近期暂无赛程"


def answer_score(data, team):
    for m in data.get("results", []):
        if team_match(m, team):
            head = f"{team}最新比分：" if team else "最新比分："
            return (
                f"{head}\n{m.get('leagueName', '')} {fmt_time(m['startTime'])}\n"
                f"{m.get('home', '')} {m.get('homeScore')}:{m.get('awayScore')} {m.get('away', '')}"
            )
    return f"暂无{team}的最新比分" if team else "暂无最新比分"


def answer_derby(data):
    f = data.get("featured") or {}
    title = f.get("title") or ""
    if "德比" not in title or not f.get("startTime"):
        return "近期暂无国家德比"
    lines = [title, fmt_time(f["startTime"]), f"{f.get('home', '')} vs {f.get('away', '')}"]
    if f.get("venue"):
        lines.append(f["venue"])
    return "\n".join(lines)


def answer_f1(data, quali):
    f = data.get("f1Featured") or {}
    sessions = f.get("sessions") or []
    race = f.get("startTime")
    if quali:
        for s in sessions:
            if s.get("name") == "排位赛":
                if s.get("past"):
                    lines = ["排位赛已结束", fmt_time(s["startTime"])]
                    if race:
                        lines.append(f"正赛 {fmt_time(race)}")
                    return "\n".join(lines)
                return f"排位赛\n{fmt_time(s['startTime'])}"
    if not race:
        return "近期一周内无 F1 正赛"
    lines = [f"{f.get('title', 'F1')}正赛", fmt_time(race)]
    if f.get("venue"):
        lines.append(f["venue"])
    return "\n".join(lines)


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    if not text:
        return 0
    data = load()
    team = find_team(text, data)
    f1_ctx = any(w in text for w in ("F1", "f1", "大奖赛", "正赛", "排位赛"))
    derby_ctx = "德比" in text
    no_score = any(w in text for w in ("比分", "排名", "名次", "第几", "结果", "赢", "输"))
    if (f1_ctx or derby_ctx) and no_score:
        return 0  # 问的是比分/排名，不归时间分支猜
    if f1_ctx:
        print(answer_f1(data, "排位赛" in text))
    elif derby_ctx:
        print(answer_derby(data))
    elif "下一场" in text or "下场比赛" in text or ("下场" in text and team):
        print(answer_next(data, team))
    elif "比分" in text:
        print(answer_score(data, team))
    # 其余：无匹配，不输出
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
