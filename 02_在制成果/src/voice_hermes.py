#!/usr/bin/env python3
"""P2：把识别文字交给 Hermes，stdout 只回简短纯文本。

用法：voice_hermes.py <识别文字>
成功：stdout 打印答案（首尾空白已 strip），exit 0。
超时（20 秒）/调用失败/空回答：stdout 为空，exit 非 0（宿主据此回没听懂）。
为适合语音播报，送给 Hermes 的提示词后会追加一句“请用一两句话简短回答”。
"""
import subprocess
import sys

HERMES = "/home/taqula/.local/bin/hermes"
TIMEOUT = 20


def main() -> int:
    if len(sys.argv) < 2:
        return 1
    text = sys.argv[1]
    if not text.strip():
        return 1
    prompt = text + "请用一两句话简短回答。"
    try:
        out = subprocess.run(
            [HERMES, "-z", prompt],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 2
    except OSError:
        return 1
    if out.returncode != 0:
        return 1
    lines = [line.strip() for line in out.stdout.strip().splitlines()]
    answer = "\n".join(line for line in lines if line)
    if not answer:
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
