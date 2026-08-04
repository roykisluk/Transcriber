#!/usr/bin/env python3
"""
EconTranscriber — Whisper-based transcriber for academic economics videos.
Primary backend: mlx-whisper (Apple Silicon Metal GPU)
Fallback backend: faster-whisper (CPU)
"""

import argparse
import json
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ECON_PROMPT = (
    "Academic economics seminar or lecture. Technical terms include: "
    "econometrics, instrumental variables, IV, two-stage least squares, 2SLS, "
    "difference-in-differences, diff-in-diff, OLS, GLS, GMM, MLE, "
    "DSGE, VAR, SVAR, panel data, fixed effects, random effects, "
    "heteroskedasticity, endogeneity, Bayesian, MCMC, "
    "structural estimation, general equilibrium, welfare analysis, "
    "Pareto, Nash equilibrium, Ramsey, Bellman equation, HJB, "
    "Lagrangian, first-order conditions, calibration, identification, "
    "reduced form, causal inference, regression discontinuity, RD, RDD."
)


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------

def extract_audio(video_path: Path, tmp_dir: Path) -> Path:
    wav_path = tmp_dir / (video_path.stem + "_audio.wav")
    print(f"  Extracting audio from {video_path.name}...")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(wav_path),
        ],
        capture_output=True,
        check=True,
    )
    return wav_path


# ---------------------------------------------------------------------------
# Transcription backends
# ---------------------------------------------------------------------------

def transcribe_mlx(audio_path: Path, model: str, language: str, initial_prompt: str, verbose: bool) -> dict:
    import mlx_whisper
    print(f"  Transcribing with mlx-whisper on Apple Silicon GPU...")
    print(f"  Model: {model}")
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model,
        language=None if language == "auto" else language,
        initial_prompt=initial_prompt or None,
        verbose=verbose,
        word_timestamps=False,
    )
    segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in result.get("segments", [])
    ]
    return {"text": result.get("text", "").strip(), "segments": segments}


def _map_model_size(model: str) -> str:
    name = model.lower()
    for size in ("large-v3-turbo", "large-v3", "large-v2", "large", "medium", "small", "base", "tiny"):
        if size in name:
            return size
    return "large-v3-turbo"


def transcribe_faster_whisper(audio_path: Path, model: str, language: str, initial_prompt: str, verbose: bool) -> dict:
    from faster_whisper import WhisperModel
    model_size = _map_model_size(model)
    print(f"  Transcribing with faster-whisper (CPU)...")
    print(f"  Model size: {model_size}")
    fw_model = WhisperModel(model_size, device="cpu", compute_type="int8")
    lang = None if language == "auto" else language
    segments_gen, info = fw_model.transcribe(
        str(audio_path),
        language=lang,
        initial_prompt=initial_prompt or None,
        vad_filter=True,
    )
    segments = []
    full_texts = []
    for seg in segments_gen:
        text = seg.text.strip()
        segments.append({"start": seg.start, "end": seg.end, "text": text})
        full_texts.append(text)
        if verbose:
            print(f"    [{seg.start:.1f}s → {seg.end:.1f}s] {text}")
    return {"text": " ".join(full_texts), "segments": segments}


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _fmt_srt_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def write_txt(result: dict, out_path: Path):
    text = result["text"]
    wrapped = textwrap.fill(text, width=100)
    out_path.write_text(wrapped + "\n", encoding="utf-8")


def write_srt(result: dict, out_path: Path):
    lines = []
    for i, seg in enumerate(result["segments"], 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(seg['start'])} --> {_fmt_srt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_vtt(result: dict, out_path: Path):
    lines = ["WEBVTT", ""]
    for seg in result["segments"]:
        lines.append(f"{_fmt_vtt_time(seg['start'])} --> {_fmt_vtt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_json(result: dict, out_path: Path, meta: dict):
    payload = {
        "metadata": meta,
        "text": result["text"],
        "segments": result["segments"],
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


WRITERS = {
    "txt": write_txt,
    "srt": write_srt,
    "vtt": write_vtt,
    "json": write_json,
}


def save_outputs(result: dict, video_path: Path, output_dir: Path, formats: list, meta: dict):
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        out_path = output_dir / f"{video_path.stem}.{fmt}"
        if fmt == "json":
            write_json(result, out_path, meta)
        else:
            WRITERS[fmt](result, out_path)
        written.append(out_path)
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Transcribe economics seminar/lecture videos using Whisper on Apple Silicon GPU.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python transcribe.py sample.mp4
          python transcribe.py lecture1.mp4 lecture2.mp4 --format srt txt
          python transcribe.py *.mp4 --model mlx-community/whisper-large-v3 --output-dir ./transcripts
          python transcribe.py seminar.mp4 --backend faster-whisper --verbose
        """),
    )
    parser.add_argument("videos", nargs="+", type=Path, help="Video file(s) to transcribe")
    parser.add_argument(
        "--model", default="mlx-community/whisper-large-v3-turbo",
        help="Whisper model (default: mlx-community/whisper-large-v3-turbo)"
    )
    parser.add_argument(
        "--backend", choices=["mlx", "faster-whisper"], default="mlx",
        help="Transcription backend (default: mlx — Apple Silicon GPU)"
    )
    parser.add_argument(
        "--format", dest="formats", nargs="+",
        choices=["txt", "srt", "vtt", "json", "all"], default=["all"],
        metavar="FORMAT",
        help="Output format(s): txt srt vtt json all (default: all)"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Output directory (default: same directory as each video)"
    )
    parser.add_argument(
        "--language", default="en",
        help="Language code, or 'auto' for detection (default: en)"
    )
    parser.add_argument(
        "--no-econ-prompt", action="store_true",
        help="Disable the built-in economics vocabulary prompt"
    )
    parser.add_argument(
        "--prompt", default="",
        help="Additional initial prompt appended to the economics prompt"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print segment-level transcription output"
    )
    return parser.parse_args()


def resolve_formats(formats: list) -> list:
    if "all" in formats:
        return ["txt", "srt", "vtt", "json"]
    return list(dict.fromkeys(formats))  # deduplicate, preserve order


def main():
    args = parse_args()
    formats = resolve_formats(args.formats)

    initial_prompt = ""
    if not args.no_econ_prompt:
        initial_prompt = ECON_PROMPT
    if args.prompt:
        initial_prompt = (initial_prompt + " " + args.prompt).strip()

    transcribe_fn = transcribe_mlx if args.backend == "mlx" else transcribe_faster_whisper

    total_videos = len(args.videos)
    failed = []

    for idx, video_path in enumerate(args.videos, 1):
        video_path = video_path.resolve()
        if not video_path.exists():
            print(f"[{idx}/{total_videos}] ERROR: File not found: {video_path}", file=sys.stderr)
            failed.append(video_path)
            continue

        output_dir = args.output_dir.resolve() if args.output_dir else video_path.parent

        print(f"\n[{idx}/{total_videos}] {video_path.name}")
        print(f"  Output dir : {output_dir}")
        print(f"  Backend    : {args.backend}")
        print(f"  Formats    : {', '.join(formats)}")

        t0 = time.time()

        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                audio_path = extract_audio(video_path, Path(tmp_dir))
            except subprocess.CalledProcessError as e:
                print(f"  ERROR: ffmpeg failed — {e.stderr.decode()}", file=sys.stderr)
                failed.append(video_path)
                continue

            try:
                result = transcribe_fn(audio_path, args.model, args.language, initial_prompt, args.verbose)
            except Exception as e:
                print(f"  ERROR: Transcription failed — {e}", file=sys.stderr)
                failed.append(video_path)
                continue

        elapsed = time.time() - t0
        duration = result["segments"][-1]["end"] if result["segments"] else 0
        rtf = elapsed / duration if duration > 0 else 0

        meta = {
            "source": str(video_path),
            "model": args.model,
            "backend": args.backend,
            "language": args.language,
            "duration_s": round(duration, 2),
            "processing_time_s": round(elapsed, 2),
            "real_time_factor": round(rtf, 3),
        }

        written = save_outputs(result, video_path, output_dir, formats, meta)

        print(f"  Done in {elapsed:.1f}s (audio: {duration:.0f}s, RTF: {rtf:.2f}x)")
        for p in written:
            print(f"  → {p}")

    if failed:
        print(f"\n{len(failed)} file(s) failed:", file=sys.stderr)
        for p in failed:
            print(f"  {p}", file=sys.stderr)
        sys.exit(1)

    print("\nAll done.")


if __name__ == "__main__":
    main()
