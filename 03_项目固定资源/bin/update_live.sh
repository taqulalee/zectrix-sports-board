#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/mnt/lexar512/apps/zectrix-challenge"
PYTHON="/mnt/lexar512/apps/taqula-eink-dashboard/.venv/bin/python"

export TZ="Asia/Shanghai"
exec "$PYTHON" "$ROOT/02_在制成果/src/update_live.py" \
  --root "$ROOT" \
  --font "/mnt/lexar512/apps/taqula-eink-dashboard/assets/zfull_GB.ttf" \
  --track-geojson "$ROOT/01_当前有效资料/circuit_data/cota_us_2012.geojson" \
  --f1-logo "$ROOT/03_项目固定资源/赛事标识/私人测试/F1官方Logo_用户提供截图提取.png"
