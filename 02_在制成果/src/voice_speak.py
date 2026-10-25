#!/usr/bin/env python3
"""P2：把文字合成为 16kHz 单声道 PCM，供宿主 device.play 播放。

用法：voice_speak.py <文字> <输出 pcm 路径>
流程：Piper（中文华艳 medium）→ WAV（22050Hz）→ ffmpeg 重采样到
16000Hz 单声道 s16le。成功 exit 0；失败 exit 非 0（宿主跳过播报，
只显示文字）。Piper 在自身目录下运行，以找到自带的 espeak-ng 数据。
"""
import os
import subprocess
import sys
import tempfile

PIPER_BIN = "/home/taqula/voice-tools/piper/piper/piper"
PIPER_MODEL = "/home/taqula/voice-tools/models/zh_CN-huayan-medium.onnx"
PIPER_CONFIG = "/home/taqula/voice-tools/models/zh_CN-huayan-medium.onnx.json"


def main() -> int:
    if len(sys.argv) < 3:
        return 1
    text = sys.argv[1].strip()
    out_path = sys.argv[2]
    if not text:
        return 1
    piper_dir = os.path.dirname(PIPER_BIN)
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "speak.wav")
        p = subprocess.run(
            [PIPER_BIN, "--model", PIPER_MODEL, "--config", PIPER_CONFIG,
             "--output_file", wav],
            input=text, capture_output=True, text=True, timeout=120,
            cwd=piper_dir,
        )
        if p.returncode != 0 or not os.path.exists(wav):
            return 1
        f = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", wav,
             "-ar", "16000", "-ac", "1", "-f", "s16le", out_path],
            capture_output=True, text=True, timeout=120,
        )
        if f.returncode != 0 or not os.path.exists(out_path):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
