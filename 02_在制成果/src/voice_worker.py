#!/home/taqula/.openclaw/workspace-wa-deep/.venv-whisper/bin/python
"""常驻转写 worker：模型只加载一次；stdin 每行一个 wav 路径，stdout 每行一个结果。

协议（行文本，无 JSON，不新增依赖）：
  启动完成 -> READY
  成功 -> OK\t<简体文本>（空识别结果时文本为空）
  失败 -> ERR\t<错误信息>
"""
import sys

from faster_whisper import WhisperModel
from opencc import OpenCC

MODEL_NAME = "medium"


def main() -> int:
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    cc = OpenCC("t2s")
    print("READY", flush=True)
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            segments, _info = model.transcribe(path, language="zh", vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
            text = " ".join(text.split())
            text = cc.convert(text)
            print("OK\t" + text, flush=True)
        except Exception as exc:  # noqa: BLE001 - 单条失败不能拖垮常驻进程
            print("ERR\t" + str(exc).replace("\n", " "), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
