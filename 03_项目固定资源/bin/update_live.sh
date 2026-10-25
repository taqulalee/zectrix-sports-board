#!/usr/bin/env bash
set -Eeuo pipefail

# 仓库根目录：由脚本自身位置推导（脚本位于 <root>/03_项目固定资源/bin/）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Python：默认用仓库根的 .venv，可用 PYTHON 环境变量覆盖
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
# 中文字体：默认用仓库根 assets/ 下的字体，可用 FONT 环境变量覆盖；
# 版式按 zfull_GB 量过，换字体需重核版式。字体文件需自行准备，不随仓发布。
FONT="${FONT:-$ROOT/assets/zfull_GB.ttf}"

if [ ! -x "$PYTHON" ]; then
  echo "错误：找不到 Python：$PYTHON（请先在仓库根创建 .venv 并安装依赖，见 DEPLOY.md）" >&2
  exit 1
fi
if [ ! -f "$FONT" ]; then
  echo "错误：找不到中文字体：$FONT（请自备中文字体，见 DEPLOY.md）" >&2
  exit 1
fi

export TZ="Asia/Shanghai"
exec "$PYTHON" "$ROOT/02_在制成果/src/update_live.py" \
  --root "$ROOT" \
  --font "$FONT" \
  --track-geojson "$ROOT/01_当前有效资料/circuit_data/cota_us_2012.geojson" \
  --f1-logo "$ROOT/03_项目固定资源/赛事标识/私人测试/F1官方Logo_用户提供截图提取.png"
