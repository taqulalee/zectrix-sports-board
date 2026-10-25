#!/usr/bin/env python3
"""赛事看板 Pro · 选队页（内网小服务，Python 标准库实现，无第三方依赖）。

- 球队目录：从 ESPN 各联赛 teams 接口抓取，按球队 ID 跨联赛合并，缓存为 JSON。
- 选择结果：保存为 runtime/team_selection.json，供数据管线（update_live）读取。
- 页面：按联赛分组、带队徽与搜索，勾选后保存。

用法：
    team_picker.py build-catalog    # 抓取/刷新球队目录
    team_picker.py serve [--port 8790]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path("/mnt/lexar512/apps/zectrix-challenge")
RUNTIME = ROOT / "02_在制成果" / "runtime"
CATALOG_PATH = RUNTIME / "team_catalog.json"
SELECTION_PATH = RUNTIME / "team_selection.json"

LEAGUES = [
    ("eng.1", "英超", "Premier League"),
    ("esp.1", "西甲", "La Liga"),
    ("ita.1", "意甲", "Serie A"),
    ("ger.1", "德甲", "Bundesliga"),
    ("uefa.champions", "欧冠", "UEFA Champions League"),
]

# 与 build_demo_data.py 的 TEAM_NAMES 保持一致（英超中文名，按缩写）
TEAM_NAMES_ZH = {
    "ARS": "阿森纳", "AVL": "维拉", "BOU": "伯恩茅斯", "BRE": "布伦特福德",
    "BHA": "布莱顿", "CHE": "切尔西", "COV": "考文垂", "CRY": "水晶宫",
    "EVE": "埃弗顿", "FUL": "富勒姆", "HUL": "赫尔城", "IPS": "伊普斯维奇",
    "LEE": "利兹联", "LIV": "利物浦", "MAN": "曼联", "MNC": "曼城",
    "NEW": "纽卡斯尔", "NFO": "诺丁汉森林", "SUN": "桑德兰", "TOT": "热刺",
}

def fetch_json(url: str) -> dict:
    # 与 update_live.py 保持一致：NUC 上用 curl 抓 ESPN（urllib 直连曾出现 TLS 握手超时）
    import subprocess
    result = subprocess.run(
        ["/usr/bin/curl", "--retry", "3", "--retry-all-errors",
         "--connect-timeout", "10", "--max-time", "40", "-fsSL",
         "-A", "Mozilla/5.0 (zectrix-team-picker)", url],
        capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"curl 抓取失败：{url} rc={result.returncode}")
    return json.loads(result.stdout.decode("utf-8"))


def atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


ZH_BY_ID_PATH = ROOT / "03_项目固定资源" / "team_names_zh.json"


def load_zh_by_id() -> dict:
    if ZH_BY_ID_PATH.exists():
        return json.loads(ZH_BY_ID_PATH.read_text())
    return {}


def build_catalog() -> dict:
    zh_by_id = load_zh_by_id()
    teams: dict[str, dict] = {}
    league_meta = []
    for code, zh, en in LEAGUES:
        payload = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/teams")
        entries = payload["sports"][0]["leagues"][0]["teams"]
        league_meta.append({"code": code, "name": zh, "nameEn": en, "count": len(entries)})
        for entry in entries:
            team = entry["team"]
            tid = str(team["id"])
            logos = team.get("logos") or []
            logo = logos[0]["href"] if logos else ""
            record = teams.setdefault(tid, {
                "id": tid,
                "abbr": team.get("abbreviation", ""),
                "name": team.get("displayName", ""),
                "shortName": team.get("shortDisplayName", ""),
                "zh": "",
                "logo": logo,
                "leagues": [],
            })
            if tid in zh_by_id:
                record["zh"] = zh_by_id[tid]
            elif team.get("abbreviation") in TEAM_NAMES_ZH:
                record["zh"] = TEAM_NAMES_ZH[team["abbreviation"]]
            if code not in record["leagues"]:
                record["leagues"].append(code)
    catalog = {
        "generatedAt": datetime.now(TZ).isoformat(),
        "leagues": league_meta,
        "teams": sorted(teams.values(), key=lambda t: (t["leagues"][0], t["name"])),
    }
    atomic_write(CATALOG_PATH, catalog)
    return catalog


def load_catalog() -> dict:
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text())
    return build_catalog()


def load_selection() -> dict:
    if SELECTION_PATH.exists():
        return json.loads(SELECTION_PATH.read_text())
    return {"teamIds": [], "updatedAt": None}


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>赛事看板 Pro · 选队</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; background: #f4f4f4; color: #111; }
  header { position: sticky; top: 0; z-index: 10; background: #111; color: #fff; padding: 14px 16px; }
  header h1 { margin: 0; font-size: 19px; letter-spacing: 1px; }
  header p { margin: 4px 0 0; font-size: 12px; color: #bbb; }
  .bar { position: sticky; top: 76px; z-index: 10; background: #fff; border-bottom: 2px solid #111; padding: 10px 16px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .bar input[type=search] { flex: 1 1 180px; padding: 8px 10px; font-size: 14px; border: 1px solid #999; border-radius: 4px; }
  .count { font-size: 13px; color: #333; white-space: nowrap; }
  button { cursor: pointer; border: 1px solid #111; background: #fff; color: #111; padding: 7px 14px; font-size: 13px; border-radius: 4px; }
  button.primary { background: #111; color: #fff; font-weight: 600; }
  button:disabled { opacity: .45; cursor: default; }
  main { max-width: 860px; margin: 0 auto; padding: 8px 16px 90px; }
  section { margin-top: 22px; }
  section h2 { font-size: 16px; margin: 0 0 4px; padding-bottom: 6px; border-bottom: 1px solid #111; display: flex; justify-content: space-between; align-items: baseline; }
  section h2 .mini { font-size: 12px; font-weight: normal; }
  section h2 .mini button { padding: 3px 9px; font-size: 12px; margin-left: 6px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 6px; margin-top: 10px; }
  label.team { display: flex; align-items: center; gap: 9px; background: #fff; border: 1px solid #ccc; border-radius: 6px; padding: 7px 10px; cursor: pointer; }
  label.team:hover { border-color: #111; }
  label.team input { width: 17px; height: 17px; accent-color: #111; flex: none; }
  label.team img { width: 26px; height: 26px; object-fit: contain; flex: none; }
  label.team .nm { font-size: 14px; line-height: 1.25; }
  label.team .sub { font-size: 11px; color: #777; }
  label.team.checked { border-color: #111; box-shadow: inset 0 0 0 1px #111; }
  .foot { position: fixed; left: 0; right: 0; bottom: 0; background: #111; color: #fff; padding: 10px 16px; display: flex; gap: 12px; align-items: center; justify-content: center; }
  .foot .msg { font-size: 13px; color: #ddd; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<header>
  <h1>赛事看板 Pro · 选队</h1>
  <p>勾选你关注的球队即可，选择会自动保存。看板之后只发布所选球队的比赛（赛程与赛果）；同一支球队在国内联赛和欧冠的比赛都会包含。</p>
</header>
<div class="bar">
  <input type="search" id="q" placeholder="搜索球队（中文 / 英文 / 缩写）">
  <span class="count" id="count">已选 0 支</span>
  <button id="refresh" title="从 ESPN 重新抓取球队列表">刷新球队列表</button>
</div>
<main id="main">正在加载…</main>
<div class="foot">
  <button class="primary" id="save">保存选择</button>
  <span class="msg" id="msg"></span>
</div>
<script>
let state = null;
const selected = new Set();

function teamLabel(t) { return t.zh || t.name; }

function render() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const main = document.getElementById('main');
  main.innerHTML = '';
  for (const lg of state.leagues) {
    const teams = state.teams.filter(t => t.leagues.includes(lg.code));
    const sec = document.createElement('section');
    const picked = teams.filter(t => selected.has(t.id)).length;
    sec.innerHTML = `<h2>${lg.name} <span class="mini">已选 ${picked}/${teams.length}
      <button data-act="all" data-lg="${lg.code}">全选</button><button data-act="none" data-lg="${lg.code}">清空</button></span></h2>`;
    const grid = document.createElement('div'); grid.className = 'grid';
    let visible = 0;
    for (const t of teams) {
      const hay = (teamLabel(t) + ' ' + t.name + ' ' + t.abbr).toLowerCase();
      if (q && !hay.includes(q)) continue;
      visible++;
      const lab = document.createElement('label');
      lab.className = 'team' + (selected.has(t.id) ? ' checked' : '');
      lab.innerHTML = `<input type="checkbox" ${selected.has(t.id) ? 'checked' : ''}>
        ${t.logo ? `<img loading="lazy" src="${t.logo}" alt="">` : ''}
        <span><span class="nm">${teamLabel(t)}</span><br><span class="sub">${t.zh ? t.name + ' · ' : ''}${t.abbr}</span></span>`;
      lab.querySelector('input').addEventListener('change', ev => {
        if (ev.target.checked) selected.add(t.id); else selected.delete(t.id);
        render(); queueSave();
      });
      grid.appendChild(lab);
    }
    if (!visible) grid.innerHTML = '<span class="sub">无匹配球队</span>';
    sec.appendChild(grid);
    main.appendChild(sec);
  }
  document.getElementById('count').textContent = `已选 ${selected.size} 支`;
  main.querySelectorAll('button[data-act]').forEach(b => b.addEventListener('click', () => {
    const ids = state.teams.filter(t => t.leagues.includes(b.dataset.lg)).map(t => t.id);
    ids.forEach(id => b.dataset.act === 'all' ? selected.add(id) : selected.delete(id));
    render(); queueSave();
  }));
}

async function load() {
  const r = await fetch('/api/state'); state = await r.json();
  (state.selection || []).forEach(id => selected.add(id));
  render();
  const at = state.updatedAt ? `上次保存：${state.updatedAt.slice(0,16).replace('T',' ')}` : '尚未保存过选择';
  document.getElementById('msg').textContent = at;
}

let saveTimer = null;
async function doSave() {
  const r = await fetch('/api/selection', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ teamIds: [...selected] }) });
  const d = await r.json();
  document.getElementById('msg').textContent = d.ok ? `已自动保存 ${d.count} 支球队 · ${d.updatedAt.slice(11,19)}` : '保存失败，请重试';
}
function queueSave() {
  document.getElementById('msg').textContent = '保存中…';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(doSave, 500);
}

document.getElementById('q').addEventListener('input', render);
document.getElementById('save').addEventListener('click', doSave);
document.getElementById('refresh').addEventListener('click', async () => {
  document.getElementById('msg').textContent = '正在刷新球队列表…';
  const r = await fetch('/api/refresh-catalog', { method: 'POST' });
  const d = await r.json();
  if (d.ok) { await load(); document.getElementById('msg').textContent = `球队列表已刷新，共 ${d.total} 支`; }
});
load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默
        pass

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            catalog = load_catalog()
            selection = load_selection()
            self._json({
                "leagues": catalog["leagues"],
                "teams": catalog["teams"],
                "selection": selection.get("teamIds", []),
                "updatedAt": selection.get("updatedAt"),
                "catalogAt": catalog.get("generatedAt"),
            })
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self.path == "/api/selection":
            try:
                data = json.loads(raw.decode("utf-8"))
                wanted = {str(x) for x in data.get("teamIds", [])}
            except Exception:
                self._json({"ok": False, "error": "bad json"}, 400)
                return
            catalog = load_catalog()
            valid = {t["id"] for t in catalog["teams"]}
            team_ids = sorted(wanted & valid)
            payload = {"teamIds": team_ids, "updatedAt": datetime.now(TZ).isoformat()}
            atomic_write(SELECTION_PATH, payload)
            self._json({"ok": True, "count": len(team_ids), "updatedAt": payload["updatedAt"]})
        elif self.path == "/api/refresh-catalog":
            catalog = build_catalog()
            self._json({"ok": True, "total": len(catalog["teams"])})
        else:
            self._json({"ok": False, "error": "not found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build-catalog", "serve"))
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    if args.command == "build-catalog":
        catalog = build_catalog()
        for lg in catalog["leagues"]:
            print(lg["code"], lg["name"], lg["count"])
        print("unique teams:", len(catalog["teams"]))
    else:
        load_catalog()
        server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
        print(f"team picker on :{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
