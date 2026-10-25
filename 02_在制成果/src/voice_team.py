#!/usr/bin/env python3
"""P2：解析改选队意图。

用法：voice_team.py <识别文字>
输出（stdout 单行）：
  空：无改队意图，宿主继续走问答/Hermes。
  OK\t<新选队文件 JSON>\t<确认文案>：解析成功，宿主显示确认页；
    确认后宿主把该 JSON 原样写入选队文件（脚本已按原文件结构生成）。
  UNPARSED：有改队意图但队名没解析出来，宿主显示“没听清要改哪支队”。
  NO_SELECTION：选队文件不存在，宿主提示先去选队网页选一次。

支持的说法（队名用选队表全名或简称）：
  把 A 换成 B / A 换 B：去掉 A，加上 B。
  关注 A / 加上 A / 添加 A：加上 A。
  去掉 A / 取消 A / 不看 A / 删除 A：去掉 A。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ALIASES = {
    "巴萨": "巴塞罗那", "皇马": "皇家马德里", "国米": "国际米兰",
    "米兰": "AC米兰", "尤文": "尤文图斯", "拜仁": "拜仁慕尼黑",
    "巴黎": "巴黎圣日耳曼", "曼联": "曼联",
}
ADD_VERBS = ("关注", "加上", "添加")
REMOVE_VERBS = ("去掉", "取消", "不看", "删除")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    if len(sys.argv) < 2:
        return 1
    text = sys.argv[1]
    here = os.path.dirname(os.path.abspath(__file__))
    names_path = os.path.normpath(os.path.join(
        here, "..", "..", "03_项目固定资源", "team_names_zh.json"))
    sel_path = os.path.normpath(os.path.join(here, "..", "runtime", "team_selection.json"))
    try:
        raw_names = load_json(names_path)
    except OSError:
        return 1
    id_to_name = {tid: name for tid, name in raw_names.items()}
    name_to_id = {name: tid for tid, name in raw_names.items()}
    for alias, full in ALIASES.items():
        if full in name_to_id:
            name_to_id.setdefault(alias, name_to_id[full])
    # 队名匹配：按长度降序，贪心取不重叠的出现（“国际米兰”先于“米兰”）。
    spans = []
    for name in sorted(name_to_id, key=len, reverse=True):
        start = 0
        while True:
            pos = text.find(name, start)
            if pos < 0:
                break
            end = pos + len(name)
            if all(end <= s or pos >= e for s, e, _, _ in spans):
                spans.append((pos, end, name_to_id[name], name))
            start = end
    spans.sort()
    teams = []
    for _, _, tid, _ in spans:
        if tid not in teams:
            teams.append(tid)

    removed = None
    added = None
    if ("换成" in text or "换" in text) and len(teams) >= 2:
        removed, added = teams[0], teams[1]
    elif any(v in text for v in ADD_VERBS) and teams:
        added = teams[0]
    elif any(v in text for v in REMOVE_VERBS) and teams:
        removed = teams[0]
    elif ("换成" in text or "换" in text
          or any(v in text for v in ADD_VERBS + REMOVE_VERBS)):
        print("UNPARSED")
        return 0
    else:
        return 0

    try:
        sel = load_json(sel_path)
    except OSError:
        print("NO_SELECTION")
        return 0
    if not isinstance(sel, dict) or not isinstance(sel.get("teamIds"), list):
        print("UNPARSED")
        return 0
    current = [str(t) for t in sel["teamIds"]]
    proposed = [tid for tid in current]
    if removed is not None and removed in proposed:
        proposed.remove(removed)
    if added is not None and added not in proposed:
        proposed.append(added)
    old_names = "、".join(id_to_name.get(t, str(t)) for t in current)
    new_names = "、".join(id_to_name.get(t, str(t)) for t in proposed)
    desc = f"改成：{new_names}（原：{old_names}）\n按确认键保存，其他键取消"
    out = dict(sel)
    out["teamIds"] = proposed
    out["updatedAt"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    print("OK\t" + json.dumps(out, ensure_ascii=False) + "\t" + desc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
