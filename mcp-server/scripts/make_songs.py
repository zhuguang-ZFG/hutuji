"""合成小派曲库：公版儿歌旋律 → Opus OGG + catalog.json。

设计决策（为什么是合成而不是下载）：
- 唱歌功能只缺「合法音源」：市售歌曲有版权，第三方音乐服务不可控；
  三首儿歌旋律均为公版（《生日快乐》旋律 2016 年起在美判公有领域），自合成零版权风险。
- 单音旋律 + 包络 + 少量泛音，听感接近八音盒/玩具钢琴，与小派「陪玩」人设一致；
  用户后续可提供真实音频，替换 songs/ 目录即可（catalog.json 重新生成就生效）。

用法：`python scripts/make_songs.py`（venv 内，依赖 numpy + imageio-ffmpeg）。
产物：`mcp-server/songs/{twinkle,tigers,birthday}.ogg` + `catalog.json`；
部署时整目录 scp 到生产 `<output_dir>/songs/`。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

try:
    import imageio_ffmpeg
except ImportError:  # pragma: no cover - 开发机 venv 已带，生产不跑本脚本
    sys.exit("缺少 imageio-ffmpeg：请用 mcp-server 的 venv 运行本脚本")

SAMPLE_RATE = 24000  # 与设备 Opus 解码链同采样率，省一次重采样
_TEMPO_BPM = 120
_QUARTER = 60.0 / _TEMPO_BPM  # 一拍（四分音符）秒数

_OUT_DIR = Path(__file__).resolve().parent.parent / "songs"

# 音名 → MIDI 号；A4=440，freq = 440 * 2**((midi-69)/12)
_NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _midi(name: str) -> int:
    return _NOTE_BASE[name[0]] + (int(name[1]) + 1) * 12


def _freq(name: str) -> float:
    return 440.0 * 2.0 ** ((_midi(name) - 69) / 12.0)


# 旋律表：(音名, 拍数)；2 = 二分音符，0.5 = 八分音符。
_TWINKLE = [
    ("C4", 1), ("C4", 1), ("G4", 1), ("G4", 1), ("A4", 1), ("A4", 1), ("G4", 2),
    ("F4", 1), ("F4", 1), ("E4", 1), ("E4", 1), ("D4", 1), ("D4", 1), ("C4", 2),
    ("G4", 1), ("G4", 1), ("F4", 1), ("F4", 1), ("E4", 1), ("E4", 1), ("D4", 2),
    ("G4", 1), ("G4", 1), ("F4", 1), ("F4", 1), ("E4", 1), ("E4", 1), ("D4", 2),
    ("C4", 1), ("C4", 1), ("G4", 1), ("G4", 1), ("A4", 1), ("A4", 1), ("G4", 2),
    ("F4", 1), ("F4", 1), ("E4", 1), ("E4", 1), ("D4", 1), ("D4", 1), ("C4", 2),
]

_TIGERS = [
    ("C4", 1), ("D4", 1), ("E4", 1), ("C4", 1),
    ("C4", 1), ("D4", 1), ("E4", 1), ("C4", 1),
    ("E4", 1), ("F4", 1), ("G4", 2),
    ("E4", 1), ("F4", 1), ("G4", 2),
    ("G4", 0.5), ("A4", 0.5), ("G4", 0.5), ("F4", 0.5), ("E4", 1), ("C4", 1),
    ("G4", 0.5), ("A4", 0.5), ("G4", 0.5), ("F4", 0.5), ("E4", 1), ("C4", 1),
    ("D4", 1), ("G3", 1), ("C4", 2),
    ("D4", 1), ("G3", 1), ("C4", 2),
]

_BIRTHDAY = [
    ("G4", 0.75), ("G4", 0.25), ("A4", 1), ("G4", 1), ("C5", 1), ("B4", 2),
    ("G4", 0.75), ("G4", 0.25), ("A4", 1), ("G4", 1), ("D5", 1), ("C5", 2),
    ("G4", 0.75), ("G4", 0.25), ("G5", 1), ("E5", 1), ("C5", 1), ("B4", 1), ("A4", 2),
    ("F5", 0.75), ("F5", 0.25), ("E5", 1), ("C5", 1), ("D5", 1), ("C5", 2),
]

# 曲库元数据：id/file 只能用小写 [a-z0-9_-]（server 端 _SONG_FILE_RE 白名单）。
SONGS = [
    {"id": "twinkle", "title": "小星星", "file": "twinkle.ogg", "melody": _TWINKLE},
    {"id": "tigers", "title": "两只老虎", "file": "tigers.ogg", "melody": _TIGERS},
    {"id": "birthday", "title": "生日快乐", "file": "birthday.ogg", "melody": _BIRTHDAY},
]


def synth_note(freq: float, seconds: float) -> np.ndarray:
    """单音：基波 + 三个递减泛音，仿玩具钢琴；ADSR 简化包络防爆音。"""
    n = max(int(SAMPLE_RATE * seconds), 1)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    wave = (
        np.sin(2 * np.pi * freq * t)
        + 0.45 * np.sin(2 * np.pi * freq * 2 * t)
        + 0.20 * np.sin(2 * np.pi * freq * 3 * t)
        + 0.08 * np.sin(2 * np.pi * freq * 4 * t)
    )
    attack = max(int(0.012 * SAMPLE_RATE), 1)
    release = max(int(0.08 * SAMPLE_RATE), 1)
    env = np.ones(n, dtype=np.float64)
    env[:attack] = np.linspace(0.0, 1.0, attack)
    env[-release:] = np.linspace(1.0, 0.0, release)
    # 释音前的轻度指数衰减，让长音不那么「电子」
    env *= np.exp(-1.2 * t / max(seconds, 1e-6)) * 0.5 + 0.5
    return wave * env


def synth_melody(melody: list[tuple[str, float]]) -> np.ndarray:
    """整曲：音符拼接 + 音间 20ms 间隙，尾部补 0.4s 空白，峰值归一到 -3dB。"""
    gap = np.zeros(int(0.02 * SAMPLE_RATE), dtype=np.float64)
    parts: list[np.ndarray] = []
    for name, beats in melody:
        parts.append(synth_note(_freq(name), beats * _QUARTER))
        parts.append(gap)
    parts.append(np.zeros(int(0.4 * SAMPLE_RATE), dtype=np.float64))
    pcm = np.concatenate(parts)
    peak = np.max(np.abs(pcm))
    if peak > 0:
        pcm = pcm / peak * 0.7
    return pcm


def encode_opus(pcm: np.ndarray, out_path: Path) -> None:
    """int16 单声道 PCM 走 stdin 喂 ffmpeg → libopus 32k/24kHz OGG，不落中间 WAV。"""
    int16 = np.clip(pcm * 32767.0, -32768, 32767).astype("<i2")
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y",
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
        "-c:a", "libopus", "-b:a", "32k", "-ar", str(SAMPLE_RATE),
        str(out_path),
    ]
    proc = subprocess.run(cmd, input=int16.tobytes(), capture_output=True)
    if proc.returncode != 0:
        # stderr 只可能是编码器诊断，不含用户数据，可原样抛出
        raise RuntimeError(f"ffmpeg 编码失败（exit {proc.returncode}）：{proc.stderr.decode('utf-8', 'replace')[-400:]}")


def build_songs(out_dir: Path = _OUT_DIR) -> list[dict]:
    """生成全部歌曲与 catalog.json；返回 catalog 条目（含真实时长，供测试断言）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog: list[dict] = []
    for song in SONGS:
        pcm = synth_melody(song["melody"])
        out_path = out_dir / song["file"]
        encode_opus(pcm, out_path)
        duration = round(len(pcm) / SAMPLE_RATE, 1)
        catalog.append({
            "id": song["id"],
            "title": song["title"],
            "file": song["file"],
            "duration_seconds": duration,
        })
        print(f"{song['file']}: {duration}s, {out_path.stat().st_size} bytes")
    (out_dir / "catalog.json").write_text(
        json.dumps({"songs": catalog}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return catalog


if __name__ == "__main__":
    build_songs()
    print(f"曲库已生成：{_OUT_DIR}")
