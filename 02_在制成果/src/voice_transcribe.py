#!/home/taqula/.openclaw/workspace-wa-deep/.venv-whisper/bin/python
"""P0: 墨水屏录音转写（medium，CPU int8，中文）。复用 NUC 已验证的 faster-whisper 形态。"""
import sys

from faster_whisper import WhisperModel

MODEL_NAME = "medium"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: voice_transcribe.py <wav-file>", file=sys.stderr)
        return 2
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(sys.argv[1], language="zh", vad_filter=True)
    text = "".join(seg.text for seg in segments).strip()
    print(" ".join(text.split()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
