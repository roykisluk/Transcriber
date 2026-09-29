# EconTranscriber

Transcriber for academic economics videos — seminars, lectures, and classes. Optimized for Apple Silicon via Metal GPU acceleration. Default model is Qwen3-ASR (chosen over whisper-large-v3-turbo on 2026-09-29 — see `CLAUDE.md`).

## Features

- **Apple Silicon GPU** — Qwen3-ASR + Qwen3-ForcedAligner via [mlx-audio](https://github.com/Blaizzy/mlx-audio) (default), or [mlx-whisper](https://github.com/ml-explore/mlx-examples), both on Metal
- **Economics-tuned** — Qwen3 gets econ jargon right out of the box (Dirac, borrowing constraint, Hamilton-Jacobi-Bellman); the whisper backends use a built-in econometrics vocabulary prompt instead
- **Multiple output formats** — plain text, SRT subtitles, WebVTT, and JSON with full segment metadata
- **Batch processing** — transcribe multiple videos in one command
- **Fallback backend** — drops to `faster-whisper` (CPU) if needed

## Requirements

- macOS with Apple Silicon (M1 or later)
- Python 3.10+
- `ffmpeg` — install via Homebrew:
  ```bash
  brew install ffmpeg
  ```

## Installation

```bash
pip install mlx-audio mlx-whisper faster-whisper
```

Or from `requirements.txt`:

```bash
pip install -r requirements.txt
```

On first run, the models (Qwen3-ASR ~2.5 GB + aligner ~1 GB, or whisper ~800 MB) are downloaded automatically from HuggingFace and cached locally.

## Usage

```bash
python transcribe.py <video> [options]
```

### Basic examples

```bash
# Transcribe a single video (outputs .txt, .srt, .vtt, .json)
python transcribe.py lecture.mp4

# Transcribe multiple videos
python transcribe.py seminar1.mp4 seminar2.mp4 seminar3.mp4

# Save all transcripts to a dedicated folder
python transcribe.py *.mp4 --output-dir ./transcripts

# Only generate a plain-text transcript and SRT subtitles
python transcribe.py lecture.mp4 --format txt srt

# Use the larger model for maximum accuracy
python transcribe.py lecture.mp4 --model mlx-community/whisper-large-v3

# Add context about the specific lecture
python transcribe.py seminar.mp4 --prompt "Job market talk on monetary policy and fiscal multipliers"

# Show segment-level output as it transcribes
python transcribe.py lecture.mp4 --verbose
```

### All options

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | per backend | `mlx-community/Qwen3-ASR-1.7B-8bit` for `qwen`, `mlx-community/whisper-large-v3-turbo` otherwise |
| `--backend` | `qwen` | `qwen` (Qwen3 on GPU), `mlx` (whisper on GPU) or `faster-whisper` (whisper on CPU) |
| `--format` | `all` | Output formats: `txt srt vtt json all` |
| `--output-dir` | same dir as video | Where to save transcripts |
| `--language` | `en` | Language code, or `auto` to detect |
| `--prompt` | — | Additional context appended to the economics prompt (whisper backends only) |
| `--no-econ-prompt` | — | Disable the built-in economics vocabulary prompt (whisper backends only) |
| `--verbose` | — | Print each segment as it is transcribed |

## Output formats

| Format | Description |
|--------|-------------|
| `.txt` | Clean paragraph transcript, no timestamps |
| `.srt` | SubRip subtitles — works with VLC, most video players |
| `.vtt` | WebVTT — for browser-based video players |
| `.json` | Full segment data (start/end/text) + run metadata |

## Model selection

| Model | Speed | Accuracy | Use case |
|-------|-------|----------|----------|
| `mlx-community/Qwen3-ASR-1.7B-8bit` (`--backend qwen`) | ~4 min per 100 min | Best on econ jargon | Default |
| `mlx-community/whisper-large-v3-turbo` (`--backend mlx`) | ~2 min per 100 min | Garbles jargon, drops short phrases | When you need a vocabulary prompt |
| `mlx-community/whisper-large-v3` | ~0.5x real-time | Best | Dense technical content |
| `mlx-community/whisper-medium` | ~2x real-time | Good | Quick drafts |

Qwen3/turbo speeds measured on an M5 Pro, 2026-09-29; whisper rows below them are older M1 Max figures (x real-time).

## Tips for academic videos

- **Add a speaker/topic prompt** for better proper-noun accuracy (whisper backends only — `--backend mlx`):
  ```bash
  python transcribe.py talk.mp4 --prompt "Speaker: Emi Nakamura. Topic: fiscal stimulus and regional multipliers."
  ```
- **SRT files** can be loaded as subtitles in VLC (`Subtitle → Add Subtitle File`) for navigating long lectures
- **JSON output** includes `real_time_factor` — a value near 1.0 means transcription took as long as the video; values below 1.0 are faster than real-time
- For very long videos (2+ hours), `large-v3-turbo` is recommended over `large-v3` to keep processing time reasonable
