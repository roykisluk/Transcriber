#!/usr/bin/env python3
"""
transcribe.py — Transcribe a video/audio file to SRT subtitles using Whisper.

Usage:
  python transcribe.py input.webm              # saves input.srt next to the file
  python transcribe.py input.webm output.srt   # explicit output path

Requires:
  pip install faster-whisper
"""

import sys
from pathlib import Path
from faster_whisper import WhisperModel


def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(video_path: Path, srt_path: Path):
    print("Loading Whisper large-v3 model …", flush=True)
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")

    print(f"Transcribing: {video_path.name}", flush=True)
    segments, info = model.transcribe(str(video_path), language="en", beam_size=5)
    print(f"Detected language: {info.language} (p={info.language_probability:.2f})", flush=True)

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{fmt_time(seg.start)} --> {fmt_time(seg.end)}\n")
            f.write(f"{seg.text.strip()}\n\n")
            print(f"[{fmt_time(seg.start)} --> {fmt_time(seg.end)}] {seg.text.strip()}", flush=True)

    print(f"\nDone — saved to: {srt_path}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    video = Path(sys.argv[1])
    if not video.exists():
        sys.exit(f"File not found: {video}")

    srt = Path(sys.argv[2]) if len(sys.argv) >= 3 else video.with_suffix(".srt")
    transcribe(video, srt)
