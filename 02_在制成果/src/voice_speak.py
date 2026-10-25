#!/usr/bin/env python3
"""P2：把文字合成为 16kHz 单声道 PCM，供宿主 device.play 播放。

用法：voice_speak.py <文字> <输出 pcm 路径>
流程：口语句式改写（shape_speak）→ Piper（中文华艳 medium）→ WAV
（22050Hz）→ ffmpeg 重采样到 16000Hz 单声道 s16le。成功 exit 0；
失败 exit 非 0（宿主跳过播报，只显示文字）。Piper 在自身目录下
运行，以找到自带的 espeak-ng 数据。

为什么要改写：屏幕答案是排版碎片行（下一场：／联赛时间／主 vs 客），
照搬念出来断句全落在不该停的地方；改写后是一句完整口语句子，
停顿只落在逗号和句号处。长文本截断只截到句末，绝不半句切断。
"""

import os
import re
import subprocess
import sys
import tempfile

PIPER_BIN = "/home/taqula/voice-tools/piper/piper/piper"
PIPER_MODEL = "/home/taqula/voice-tools/models/zh_CN-huayan-medium.onnx"
PIPER_CONFIG = "/home/taqula/voice-tools/models/zh_CN-huayan-medium.onnx.json"

SPEAK_CAP = 200  # 播报字数上限（约 40 秒，远低于 120 秒播放上限）

_ONES = "零一二三四五六七八九"
_RE_TIME_LEAGUE = re.compile(
    r"^(.*?)\s*(\d+)月(\d+)日周([一二三四五六日])\s+(\d{1,2}):(\d{2})$"
)
_RE_TIME_ONLY = re.compile(
    r"^(\d+)月(\d+)日周([一二三四五六日])\s+(\d{1,2}):(\d{2})$"
)
_RE_SCORE = re.compile(r"^(.+?)\s+(\d+):(\d+)\s+(.+)$")
_RE_CONFIRM = re.compile(r"^改成：(.+?)（原：(.+?)）$")


def num_cn(n):
    """0-99 的中文基数读法：10→十，11→十一，26→二十六，0→零。"""
    n = int(n)
    if n < 10:
        return _ONES[n]
    if n < 20:
        return "十" + (_ONES[n % 10] if n % 10 else "")
    return _ONES[n // 10] + "十" + (_ONES[n % 10] if n % 10 else "")


def date_cn(month, day):
    return f"{num_cn(month)}月{num_cn(day)}日"


def time_cn(hour, minute):
    """04:00→凌晨四点，19:45→晚上七点四十五，13:30→下午一点半。"""
    hour = int(hour)
    minute = int(minute)
    if hour < 6:
        period = "凌晨"
    elif hour < 12:
        period = "上午"
    elif hour == 12:
        period = "中午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    h12 = hour % 12 or 12
    if minute == 0:
        return f"{period}{num_cn(h12)}点"
    if minute == 30:
        return f"{period}{num_cn(h12)}点半"
    if minute < 10:
        return f"{period}{num_cn(h12)}点零{_ONES[minute]}分"
    return f"{period}{num_cn(h12)}点{num_cn(minute)}分"


def time_expr(month, day, week, hour, minute):
    return f"{date_cn(month, day)}周{week}{time_cn(hour, minute)}"


def join_sentence(segments):
    """过滤空段后以逗号连接，加句号收尾。"""
    parts = [s for s in segments if s]
    if not parts:
        return ""
    return "，".join(parts) + "。"


def cap_sentence(text):
    """超长只截到句末：优先句号，其次逗号改句号，最后硬截加句号。"""
    if len(text) <= SPEAK_CAP:
        return text
    cut = text[:SPEAK_CAP]
    for mark in ("。", "！", "？"):
        i = cut.rfind(mark)
        if i >= 0:
            return text[: i + 1]
    i = cut.rfind("，")
    if i >= 0:
        return text[:i] + "。"
    return cut + "。"


def shape_speak(text):
    """屏幕文字 → 口语句子。无法识别的结构走通用规整（不断句、不硬截）。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""

    # 下一场：队下一场：／联赛 时间／主 vs 客
    if len(lines) >= 3 and lines[0].endswith("下一场：") and " vs " in lines[2]:
        m = _RE_TIME_LEAGUE.match(lines[1])
        if m:
            league, mo, da, wk, hh, mm = m.groups()
            home, away = lines[2].split(" vs ", 1)
            team = lines[0][:-4]
            head = f"{team}下一场" if team else "下一场"
            return join_sentence(
                [head, league, time_expr(mo, da, wk, hh, mm), f"{home}对{away}"]
            )

    # 最新比分：队最新比分：／联赛 时间／主 hs:as 客
    if len(lines) >= 3 and lines[0].endswith("最新比分："):
        m = _RE_TIME_LEAGUE.match(lines[1])
        s = _RE_SCORE.match(lines[2])
        if m and s:
            league, mo, da, wk, hh, mm = m.groups()
            home, hs, as_, away = s.groups()
            team = lines[0][:-5]
            head = f"{team}最新比分" if team else "最新比分"
            return join_sentence(
                [
                    head,
                    league,
                    time_expr(mo, da, wk, hh, mm),
                    f"{home}{num_cn(hs)}比{num_cn(as_)}{away}",
                ]
            )

    # 德比：标题／时间／主 vs 客（场馆行不念，屏幕上有）
    if len(lines) >= 3 and "德比" in lines[0] and " vs " in lines[2]:
        m = _RE_TIME_ONLY.match(lines[1])
        if m:
            mo, da, wk, hh, mm = m.groups()
            home, away = lines[2].split(" vs ", 1)
            return join_sentence(
                [lines[0], time_expr(mo, da, wk, hh, mm), f"{home}对{away}"]
            )

    # F1 正赛：标题正赛／时间（赛道行不念，屏幕上有）
    if len(lines) >= 2 and lines[0].endswith("正赛"):
        m = _RE_TIME_ONLY.match(lines[1])
        if m:
            mo, da, wk, hh, mm = m.groups()
            return join_sentence([lines[0], time_expr(mo, da, wk, hh, mm)])

    # F1 排位赛（未开始）：排位赛／时间
    if len(lines) >= 2 and lines[0] == "排位赛":
        m = _RE_TIME_ONLY.match(lines[1])
        if m:
            mo, da, wk, hh, mm = m.groups()
            return join_sentence(["排位赛", time_expr(mo, da, wk, hh, mm)])

    # F1 排位赛（已结束）：排位赛已结束／时间（／正赛 时间）
    if len(lines) >= 2 and lines[0] == "排位赛已结束":
        m = _RE_TIME_ONLY.match(lines[1])
        if m:
            mo, da, wk, hh, mm = m.groups()
            out = join_sentence(["排位赛已结束", time_expr(mo, da, wk, hh, mm)])
            if len(lines) >= 3 and lines[2].startswith("正赛 "):
                r = _RE_TIME_ONLY.match(lines[2][3:])
                if r:
                    mo, da, wk, hh, mm = r.groups()
                    out += join_sentence(["正赛", time_expr(mo, da, wk, hh, mm)])
            return out

    # 选队确认：改成：新（原：旧）／按确认键保存，其他键取消
    m = _RE_CONFIRM.match(lines[0])
    if m:
        new, old = m.groups()
        rest = "，".join(lines[1:])
        base = f"改成{new}，原来是{old}"
        return base + ("，" + rest + "。" if rest else "。")

    # 通用：本身就是整句的回答与提示，只做行连接与 vs 替换，不断句。
    out = lines[0]
    for ln in lines[1:]:
        if out and out[-1] in "。！？；：":
            out += ln
        else:
            out += "，" + ln
    out = out.replace(" vs ", "对")
    return cap_sentence(out)


def main() -> int:
    if len(sys.argv) < 3:
        return 1
    text = sys.argv[1].strip()
    out_path = sys.argv[2]
    if not text:
        return 1
    text = shape_speak(text)
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
