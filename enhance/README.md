# Seminar Audio Tools

Two Python scripts for cleaning up academic seminar recordings — the kind downloaded from YouTube or Zoom where audio quality is poor.

## Scripts

### `enhance_audio.py` — AI audio enhancement

Fixes the most common problems in seminar recordings:

| Problem | Fix |
|---|---|
| Screeching / static when speaker gets loud | `adeclip` reconstructs clipped peaks |
| Background hum, HVAC, keyboard noise | Two-pass spectral noise reduction |
| Speakers at wildly different volumes | EBU R128 loudness normalisation |
| Mic bleed between speakers | Silence gate |

**Pipeline:** extract → declip → denoise → limit/compress/normalise → remux

The video stream is copied losslessly; only the audio is touched.

#### Requirements

- Python 3.8+ (tested on 3.13)
- [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg` on Mac)
- Python packages are installed automatically into a local `.venv` on first run

#### Usage

```bash
# Single file — saves sample_enhanced.webm next to the original
python enhance_audio.py sample.webm

# All .webm files in the current directory
python enhance_audio.py

# Save to a separate folder
python enhance_audio.py . -o ./enhanced
```

#### Performance

Tested on Apple M1 Max (32 GB). A 1-hour seminar takes roughly 10–15 minutes.

---

### Transcription — moved to [`../econ/`](../econ/)

This README used to document a `transcribe.py` here (faster-whisper → SRT). That
script left, grew an mlx-whisper/Metal backend and an econometrics prompt, and now
lives at [`../econ/transcribe.py`](../econ/transcribe.py) — faster-whisper is still
in there as the CPU fallback.

## Typical workflow

```bash
python enhance_audio.py lecture.webm            # this dir
python ../econ/transcribe.py lecture_enhanced.webm --format srt
```

For a Hebrew meeting you recorded yourself, use the repo root's `record` /
`transcribe` instead — see the [top-level README](../README.md).
