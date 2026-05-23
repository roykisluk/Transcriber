#!/usr/bin/env python3
"""
enhance_audio.py — AI audio enhancement for seminar recordings.

Pipeline:
  1. Extract 48 kHz mono WAV (with high-pass pre-filter)
  2. Two-pass noise reduction via noisereduce
  3. Gate + dynamic compression + EBU R128 loudness normalisation (ffmpeg)
  4. Re-mux enhanced audio back into the original video container

Usage:
  python enhance_audio.py                         # all .webm in current dir
  python enhance_audio.py seminar.webm            # single file
  python enhance_audio.py . -o ./enhanced         # save to sub-folder
"""

import os
import sys

# ── Re-exec under the local venv if noisereduce isn't importable ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_VENV_PY = os.path.join(_HERE, ".venv", "bin", "python3")
if not os.environ.get("_ENHANCE_VENV_ACTIVE") and os.path.exists(_VENV_PY):
    try:
        import noisereduce  # noqa: F401 — just checking availability
    except ImportError:
        _env = os.environ.copy()
        _env["_ENHANCE_VENV_ACTIVE"] = "1"
        os.execve(_VENV_PY, [_VENV_PY] + sys.argv, _env)

# ── Standard imports (available after venv activation) ───────────────────────
import argparse
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

SAMPLE_RATE = 48_000


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def run(cmd: list, label: str = "") -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout)[-800:]
        raise RuntimeError(f"{label} failed:\n{tail}")
    return r


def fmt_seconds(s: float) -> str:
    m = int(s) // 60
    return f"{m}m {int(s) % 60}s" if m else f"{int(s)}s"


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline steps
# ─────────────────────────────────────────────────────────────────────────────

def extract_audio(video: Path, wav: Path):
    """Extract audio as 32-bit float 48 kHz mono WAV, with a pre-filter."""
    run([
        "ffmpeg", "-y", "-i", str(video),
        "-vn",
        # high-pass at 80 Hz removes low-frequency rumble / mic handling noise
        "-af", "highpass=f=80",
        "-acodec", "pcm_f32le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        str(wav),
    ], "Audio extraction")


def declip_audio(wav_in: Path, wav_out: Path):
    """
    Reconstruct clipped peaks using an autoregressive model (adeclip).
    This is the primary fix for screeching/static when speakers get loud —
    those sounds are almost always clipping distortion in the original recording.

    threshold=5  → aggressively detects clips (lower = more aggressive).
    method=am    → autoregressive model; best quality for speech.
    """
    print("      Detecting and repairing clipped peaks …", end=" ", flush=True)
    t0 = time.time()
    run([
        "ffmpeg", "-y", "-i", str(wav_in),
        "-af", "adeclip=window=55:overlap=75:arorder=8:threshold=5",
        str(wav_out),
    ], "Declip")
    print(f"done ({fmt_seconds(time.time() - t0)})")


def reduce_noise(raw: Path, out: Path):
    """
    Two-pass spectral noise reduction.

    Pass 1 (stationary)  — targets steady background hum / HVAC using the
                           first second of audio as a noise reference.
    Pass 2 (adaptive)    — removes remaining time-varying noise without
                           over-smoothing speech transients.
    """
    import numpy as np
    import noisereduce as nr
    import soundfile as sf

    audio, sr = sf.read(str(raw))
    audio = audio.astype(np.float32)
    total_samples = len(audio)
    print(f"      Duration : {total_samples / sr / 60:.1f} min  ({total_samples:,} samples)")

    # ── Pass 1: stationary (spectral gating against a noise clip) ──
    t0 = time.time()
    print("      Pass 1/2 : stationary noise floor removal …", end=" ", flush=True)
    noise_clip = audio[: int(sr * 1.5)]          # first 1.5 s as noise reference
    s1 = nr.reduce_noise(
        y=audio,
        sr=sr,
        y_noise=noise_clip,
        stationary=True,
        prop_decrease=0.80,
        n_fft=2048,
        freq_mask_smooth_hz=500,
        time_mask_smooth_ms=50,
        n_jobs=1,
    )
    print(f"done ({fmt_seconds(time.time() - t0)})")

    # ── Pass 2: adaptive (handles chair squeaks, distant voices, etc.) ──
    t0 = time.time()
    print("      Pass 2/2 : adaptive noise removal …", end=" ", flush=True)
    s2 = nr.reduce_noise(
        y=s1,
        sr=sr,
        stationary=False,
        prop_decrease=0.60,
        n_fft=2048,
        freq_mask_smooth_hz=500,
        time_mask_smooth_ms=50,
        n_jobs=1,
    )
    print(f"done ({fmt_seconds(time.time() - t0)})")

    sf.write(str(out), s2, sr, subtype="PCM_24")


def post_process(wav_in: Path, wav_out: Path):
    """
    ffmpeg audio polish:
      • Silence gate       — kills mic bleed between speakers
      • Dynamic compressor — balances loud / quiet speakers
      • EBU R128 two-pass  — broadcast-standard loudness (-16 LUFS, -1.5 dBTP)
    """
    # ── Loudnorm analysis pass ──────────────────────────────────────────────
    r = run([
        "ffmpeg", "-y", "-i", str(wav_in),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ], "Loudnorm analysis")

    match = re.search(r'\{[^}]*"input_i"[^}]*\}', r.stderr, re.DOTALL)
    if match:
        try:
            st = json.loads(match.group())
            ln = (
                f"loudnorm=I=-16:TP=-1.5:LRA=11:linear=true"
                f":measured_I={st['input_i']}"
                f":measured_TP={st['input_tp']}"
                f":measured_LRA={st['input_lra']}"
                f":measured_thresh={st['input_thresh']}"
                f":offset={st['target_offset']}"
            )
        except (json.JSONDecodeError, KeyError):
            ln = "loudnorm=I=-16:TP=-1.5:LRA=11"
    else:
        ln = "loudnorm=I=-16:TP=-1.5:LRA=11"

    # ── Apply limiter + gate + compressor + loudnorm ───────────────────────
    af = ",".join([
        # hard limiter: catches any residual peaks adeclip missed
        "alimiter=level_in=1:level_out=0.95:limit=0.95:attack=5:release=50:level=true",
        # gate: open at -50 dBFS, fast attack, moderate release
        "agate=threshold=-50dB:attack=10:release=250",
        # 3:1 compression, pulls down loud segments, +2 dB make-up
        "acompressor=threshold=-20dB:ratio=3:attack=5:release=100:makeup=2",
        ln,
    ])
    run([
        "ffmpeg", "-y", "-i", str(wav_in),
        "-af", af,
        str(wav_out),
    ], "Post-processing")


def remux(original: Path, audio: Path, output: Path):
    """Copy original video stream + mux enhanced audio as high-quality Opus."""
    run([
        "ffmpeg", "-y",
        "-i", str(original),
        "-i", str(audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",            # lossless video copy
        "-c:a", "libopus",
        "-b:a", "192k",
        "-vbr", "on",
        "-compression_level", "10",
        str(output),
    ], "Remux")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def process_file(src: Path, dst_dir: Path) -> Path:
    dst = dst_dir / (src.stem + "_enhanced" + src.suffix)
    print(f"\n{'─'*60}")
    print(f"  Input  : {src.name}")
    print(f"  Output : {dst.name}")
    print(f"{'─'*60}")

    wall = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        raw      = t / "raw.wav"
        declipped = t / "declipped.wav"
        denoised  = t / "denoised.wav"
        polished  = t / "polished.wav"

        print("  [1/5] Extracting audio …")
        extract_audio(src, raw)

        print("  [2/5] Declipping (repairing loud screeching / distortion) …")
        declip_audio(raw, declipped)

        print("  [3/5] Reducing noise …")
        reduce_noise(declipped, denoised)

        print("  [4/5] Polishing (limit / gate / compress / normalise) …")
        post_process(denoised, polished)

        print("  [5/5] Muxing enhanced audio into video …")
        remux(src, polished, dst)

    mb = dst.stat().st_size / 1_000_000
    print(f"\n  ✓ Done in {fmt_seconds(time.time() - wall)}  →  {dst} ({mb:.0f} MB)")
    return dst


def main():
    ap = argparse.ArgumentParser(
        description="AI audio enhancement for seminar recordings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("input", nargs="?", default=".",
                    help="Input .webm file or directory (default: current dir)")
    ap.add_argument("-o", "--output-dir", default=None,
                    help="Destination directory (default: same as input)")
    args = ap.parse_args()

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except FileNotFoundError:
        sys.exit("ffmpeg not found — install with: brew install ffmpeg")

    src = Path(args.input)
    if src.is_file():
        files, default_out = [src], src.parent
    elif src.is_dir():
        files = sorted(src.glob("*.webm"))
        default_out = src
        if not files:
            sys.exit(f"No .webm files found in {src}")
    else:
        sys.exit(f"Not found: {src}")

    out_dir = Path(args.output_dir) if args.output_dir else default_out
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(files)} file(s) to process.")
    print(f"Output directory: {out_dir}")

    for f in files:
        process_file(f, out_dir)

    print("\nAll done.")


if __name__ == "__main__":
    main()
