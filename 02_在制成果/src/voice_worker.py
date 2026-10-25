#!/home/taqula/.openclaw/workspace-wa-deep/.venv-whisper/bin/python
"""常驻转写 worker：模型只加载一次；stdin 每行一个 wav 路径，stdout 每行一个结果。

协议（行文本，无 JSON，不新增依赖）：
  启动完成 -> READY
  成功 -> OK\t<简体文本>（空识别结果时文本为空）
  失败 -> ERR\t<错误信息>

识别偏向：initial_prompt 给模型领域词表（球队、德比、F1、命令词），
专有名词不再全靠猜——此前“拜仁慕尼黑”曾被听成“半人目的黑”。
不换模型、不变慢，只改解码偏向。
"""
import sys

from faster_whisper import WhisperModel
from opencc import OpenCC

MODEL_NAME = "medium"

# 领域词表：8 支选队＋常问换队目标＋德比＋命令＋F1（224 token 上限内）。
INITIAL_PROMPT = (
    "巴塞罗那，皇家马德里，拜仁慕尼黑，阿森纳，AC米兰，国际米兰，"
    "尤文图斯，巴黎圣日耳曼，曼联，切尔西，西班牙国家德比，"
    "意大利国家德比，米兰德比，下一场，上一页，下一页，翻页，"
    "刷新，比分，F1，美国大奖赛，正赛，排位赛。"
)


def main() -> int:
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    cc = OpenCC("t2s")
    print("READY", flush=True)
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            segments, _info = model.transcribe(
                path, language="zh", vad_filter=True,
                initial_prompt=INITIAL_PROMPT,
            )
            text = "".join(seg.text for seg in segments).strip()
            text = " ".join(text.split())
            text = cc.convert(text)
            print("OK\t" + text, flush=True)
        except Exception as exc:  # noqa: BLE001 - 单条失败不能拖垮常驻进程
            print("ERR\t" + str(exc).replace("\n", " "), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
