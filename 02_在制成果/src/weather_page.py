"""把液态玻璃天气卡渲染成一张 PNG，供赛事看板更新脚本装配为末页。

只做三件事：读 weather-glass 的 settings.json、用它的 WeatherClient
（自带缓存）取预报、用它的 model/render 出图。密钥只从 settings.json
或 WEATHERAPI_KEY 环境变量读，不打印、不进日志。任何失败都以非零
退出码结束，由调用方决定跳过天气页还是沿用上一张图。

用法：weather_page.py <输出 PNG 路径>
解释器：weather-glass 仓库 .venv 的 python（依赖 Pillow/tzdata）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WEATHER_GLASS_DIR = Path(
    os.environ.get("WEATHER_GLASS_DIR", "/mnt/lexar512/apps/zectrix-weather-glass")
)
DATA_DIR = Path(
    os.environ.get("WEATHER_GLASS_DATA", Path.home() / ".zectrix" / "weather-glass")
)


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：weather_page.py <输出 PNG 路径>", file=sys.stderr)
        return 2
    out_path = Path(sys.argv[1])

    sys.path.insert(0, str(WEATHER_GLASS_DIR / "src"))
    from model import normalize
    from provider import WeatherClient
    from render import png

    config = json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8"))
    api_key = os.environ.get("WEATHERAPI_KEY") or config.get("api_key", "")
    if not api_key:
        print("WEATHER_FAILED\t未配置 API Key", file=sys.stderr)
        return 1
    query = config.get("query", "30.27,120.15")
    lang = config.get("lang", "zh")
    city = config.get("city_zh" if lang == "zh" else "city_en", "")

    client = None
    payload = cached = None
    last_error: Exception | None = None
    for attempt in range(3):
        # NUC 到 WeatherAPI 的 TLS 握手偶发被掐断（SSLEOFError，2026-10-29
        # 实测约 1/4 概率）；provider 的退避状态是实例级的，同一实例重试
        # 会被自己的 5 分钟退避挡住，所以每次尝试必须新建 WeatherClient。
        try:
            client = WeatherClient(DATA_DIR / "cache")
            payload, cached, _error = client.fetch(
                api_key, query, ttl=int(config.get("interval", 900))
            )
            break
        except Exception as error:  # WeatherError 等：换新实例重试
            last_error = error
            if attempt < 2:
                import time

                time.sleep(3)
    if payload is None:
        raise RuntimeError(f"WEATHER_FAILED\t{last_error}")
    model = normalize(payload, lang, city or None, None)
    if cached:
        # 与 weather-glass 服务端一致：用缓存时在卡上标注离线/旧数据。
        model["stale"] = True
        model["updated"] = (
            "离线 · " if lang == "zh" else "Offline · "
        ) + model["updated"]
    image_bytes = png(model)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_suffix(out_path.suffix + ".tmp")
    temporary.write_bytes(image_bytes)
    os.replace(temporary, out_path)
    print(f"WEATHER_OK\t{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
