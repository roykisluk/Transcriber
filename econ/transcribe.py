#!/usr/bin/env python3
"""
EconTranscriber — transcriber for academic economics videos.
Primary backend: Qwen3-ASR + Qwen3-ForcedAligner via mlx-audio (Apple Silicon Metal GPU),
or via Qwen's PyTorch package qwen-asr elsewhere (CUDA GPU, e.g. tbox)
Alternatives: mlx-whisper (Metal GPU), faster-whisper (CPU)
"""

import argparse
import difflib
import json
import platform
import re
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

def drop_degenerate_segments(segments: list, verbose: bool = False) -> list:
    """Remove segments that are a single token looped, e.g. "sca sca sca sca ...".

    Whisper does this when it is decoding something that isn't speech — in practice
    almost always the silent lead-in before a webinar starts, which lands in the very
    first cue and reads as gibberish at the top of every transcript. A real sentence
    never repeats one token over half its length at this kind of segment size.
    """
    kept = []
    for seg in segments:
        text = seg["text"].strip()
        words = text.split()
        reason = None

        if len(words) >= 12:
            top = max(set(words), key=words.count)
            if words.count(top) / len(words) > 0.5:
                reason = f"{top!r} x{words.count(top)}"

        # The loop is not always space-separated: whisper also emits one long run-on
        # token ("termintermintermin..."). Look for a short repeating unit.
        if reason is None and len(text) >= 40 and " " not in text[:40]:
            for period in range(1, len(text) // 4 + 1):
                if text.startswith((text[:period] * (len(text) // period + 1))[:len(text)]):
                    reason = f"{text[:period]!r} repeated"
                    break

        if reason:
            if verbose:
                print(f"    [dropped repetition loop at {seg['start']:.1f}s: {reason}]")
            continue
        kept.append(seg)
    return kept


def _assemble(segments: list, verbose: bool = False) -> dict:
    segments = drop_degenerate_segments(segments, verbose)
    return {"text": " ".join(s["text"] for s in segments).strip(), "segments": segments}


def transcribe_mlx(audio_path: Path, model: str, language: str, initial_prompt: str, verbose: bool) -> dict:
    import mlx_whisper
    print(f"  Transcribing with mlx-whisper on Apple Silicon GPU...")
    print(f"  Model: {model}")
    # Seminar recordings open with dead air while people join. Whisper decodes that
    # silence into text — either a looped token or, when --prompt is used, a mangled
    # echo of the prompt itself ("Paul Goldsmith-Pinkhampered Welcome back...").
    # hallucination_silence_threshold skips speechless stretches and removes both;
    # it needs word_timestamps, which costs roughly 2x runtime and is worth it.
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model,
        language=None if language == "auto" else language,
        initial_prompt=initial_prompt or None,
        verbose=verbose,
        word_timestamps=True,
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
    )
    segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in result.get("segments", [])
    ]
    return _assemble(segments, verbose)


# Apple Silicon runs Qwen3 through mlx-audio (8-bit MLX weights); anywhere else (tbox's
# CUDA GPU) through Qwen's own PyTorch package `qwen-asr`, which needs the original weights.
APPLE = sys.platform == "darwin" and platform.machine() == "arm64"
if APPLE:
    QWEN_ASR = "mlx-community/Qwen3-ASR-1.7B-8bit"
    QWEN_ALIGNER = "mlx-community/Qwen3-ForcedAligner-0.6B-8bit"
else:
    QWEN_ASR = "Qwen/Qwen3-ASR-1.7B"
    QWEN_ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"
# The aligner's timestamps stop at 266.56 s (3332 x 80 ms) despite the "5 minutes" on its
# model card; past that every word piles onto the last timestamp. Chunk well under it.
QWEN_CHUNK_S = 240
# qwen-asr re-chunks anything longer than 180 s for alignment and then joins the chunk texts
# with no space ("end.Next"), so the PyTorch path chunks at its limit itself. Its generate
# cap defaults to 512 tokens, which ~180 s of fast speech can exceed; raise it.
QWEN_TORCH_CHUNK_S, QWEN_TORCH_MAX_TOKENS, QWEN_TORCH_BATCH = 180, 2048, 16
CUE_CHARS, CUE_MAX_S, CUE_GAP_S, CUE_MIN_S = 84, 7.0, 1.0, 1.0


def _norm_word(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def _attach_punct(asr_text: str, items: list) -> list:
    """The aligner returns bare words; carry the ASR's punctuated tokens onto their times."""
    toks = asr_text.split()
    a, b = [_norm_word(t) for t in toks], [_norm_word(i.text) for i in items]
    words = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            words += [(toks[i], items[j].start_time, items[j].end_time) for i, j in zip(range(i1, i2), range(j1, j2))]
        elif i2 > i1 and j2 > j1:   # spread the ASR tokens over the aligned span
            s, e, n = items[j1].start_time, items[j2 - 1].end_time, i2 - i1
            words += [(toks[i1 + k], s + (e - s) * k / n, s + (e - s) * (k + 1) / n) for k in range(n)]
        elif i2 > i1:               # tokens the aligner skipped (bare punctuation)
            t = words[-1][2] if words else (items[j1].start_time if items else 0.0)
            words += [(toks[i], t, t) for i in range(i1, i2)]
    return words


def _cues(words: list) -> list:
    """Group (word, start, end) into subtitle cues: sentence end, pause, length or duration."""
    groups, cur = [], []
    for w in words:
        if cur and (w[1] - cur[-1][2] > CUE_GAP_S
                    or len(" ".join(x[0] for x in cur)) + len(w[0]) + 1 > CUE_CHARS
                    or w[2] - cur[0][1] > CUE_MAX_S):
            groups.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w[0]) and cur[-1][2] - cur[0][1] > 1.5:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    segs = []
    for g in groups:
        s = {"start": g[0][1], "end": g[-1][2], "text": " ".join(x[0] for x in g)}
        # Same start as the previous cue (a zero-length word before one spread past CUE_MAX_S):
        # merge, or the hold below would be capped at that start and leave a 0 s cue.
        if segs and s["start"] <= segs[-1]["start"]:
            segs[-1]["end"] = max(segs[-1]["end"], s["end"])
            segs[-1]["text"] += " " + s["text"]
        else:
            segs.append(s)
    # The aligner sometimes gives a lone word start == end, which would never display.
    for s, nxt in zip(segs, segs[1:] + [None]):
        s["end"] = max(s["end"], min(s["start"] + CUE_MIN_S, nxt["start"] if nxt else s["start"] + CUE_MIN_S))
    return segs


def _transcribe_qwen_torch(audio_path: Path, model: str, language: str, initial_prompt: str, verbose: bool) -> dict:
    import torch
    from qwen_asr import Qwen3ASRModel
    from qwen_asr.inference.utils import normalize_audio_input, split_audio_into_chunks
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"  Transcribing with Qwen3-ASR + forced aligner (PyTorch, {device})...")
    print(f"  Model: {model}")
    # Unlike mlx-audio, qwen-asr does use the context. The generic ECON_PROMPT changes nothing,
    # but a lecture's own terms via --prompt fix what it mishears ("Dirac" -> "direct").
    lang = None if language == "auto" else {"en": "English"}.get(language, language)
    kw = dict(dtype=torch.bfloat16, device_map=device)
    asr = Qwen3ASRModel.from_pretrained(
        model, forced_aligner=QWEN_ALIGNER, forced_aligner_kwargs=kw,
        max_inference_batch_size=QWEN_TORCH_BATCH, max_new_tokens=QWEN_TORCH_MAX_TOKENS, **kw)
    wav =normalize_audio_input(str(audio_path))
    chunks = split_audio_into_chunks(wav, 16000, max_chunk_sec=QWEN_TORCH_CHUNK_S)
    results = asr.transcribe([(c, 16000) for c, _ in chunks], context=initial_prompt,
                             language=lang, return_time_stamps=True)
    words = []
    for k, ((_, offset), r) in enumerate(zip(chunks, results), 1):
        text = r.text.strip()
        if not text:                # silence: qwen-asr skips the aligner too
            continue
        words += [(w, s + offset, e + offset) for w, s, e in _attach_punct(text, r.time_stamps.items)]
        if verbose:
            print(f"    [chunk {k}/{len(chunks)} @{offset:.0f}s] {len(text.split())} words")
    return _assemble(_cues(words), verbose)


def transcribe_qwen(audio_path: Path, model: str, language: str, initial_prompt: str, verbose: bool) -> dict:
    if not APPLE:
        return _transcribe_qwen_torch(audio_path, model, language, initial_prompt, verbose)
    import numpy as np
    from mlx_audio.stt.utils import load_audio, load_model
    from mlx_audio.stt.models.qwen3_asr.qwen3_asr import split_audio_into_chunks
    print(f"  Transcribing with Qwen3-ASR + forced aligner on Apple Silicon GPU...")
    print(f"  Model: {model}")
    if initial_prompt:
        # mlx-audio silently drops Qwen3's context argument, so a prompt would do nothing.
        print(f"  Note: vocabulary prompts are not supported by this backend and are ignored.")
    lang = None if language == "auto" else {"en": "English"}.get(language, language)
    asr, aligner = load_model(model), load_model(QWEN_ALIGNER)
    wav = np.array(load_audio(str(audio_path), sr=16000))
    chunks = split_audio_into_chunks(wav, 16000, chunk_duration=QWEN_CHUNK_S)
    words = []
    for k, (chunk, offset) in enumerate(chunks, 1):
        text = asr.generate(chunk, language=lang).text.strip()
        if not text:                # silence: nothing to align
            continue
        items = aligner.generate(chunk, text=text, language=lang or "English").items
        words += [(w, s + offset, e + offset) for w, s, e in _attach_punct(text, items)]
        if verbose:
            print(f"    [chunk {k}/{len(chunks)} @{offset:.0f}s] {len(text.split())} words")
    return _assemble(_cues(words), verbose)


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
    for seg in segments_gen:
        text = seg.text.strip()
        segments.append({"start": seg.start, "end": seg.end, "text": text})
        if verbose:
            print(f"    [{seg.start:.1f}s → {seg.end:.1f}s] {text}")
    return _assemble(segments, verbose)


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


def _two_lines(text: str) -> str:
    """Break a subtitle longer than 42 chars at the space nearest its middle."""
    if len(text) <= 42 or " " not in text:
        return text
    k = min((i for i, ch in enumerate(text) if ch == " "), key=lambda i: abs(i - len(text) / 2))
    return text[:k] + "\n" + text[k + 1:]


def write_srt(result: dict, out_path: Path):
    lines = []
    for i, seg in enumerate(result["segments"], 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(seg['start'])} --> {_fmt_srt_time(seg['end'])}")
        lines.append(_two_lines(seg["text"]))
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_vtt(result: dict, out_path: Path):
    lines = ["WEBVTT", ""]
    for seg in result["segments"]:
        lines.append(f"{_fmt_vtt_time(seg['start'])} --> {_fmt_vtt_time(seg['end'])}")
        lines.append(_two_lines(seg["text"]))
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
          python transcribe.py *.mp4 --backend mlx --model mlx-community/whisper-large-v3 --output-dir ./transcripts
          python transcribe.py seminar.mp4 --backend faster-whisper --verbose
        """),
    )
    parser.add_argument("videos", nargs="+", type=Path, help="Video file(s) to transcribe")
    parser.add_argument(
        "--model", default=None,
        help=f"Model (default: {QWEN_ASR} for qwen, mlx-community/whisper-large-v3-turbo otherwise)"
    )
    parser.add_argument(
        "--backend", choices=["qwen", "mlx", "faster-whisper"], default="qwen",
        help="Transcription backend (default: qwen — Qwen3-ASR on Apple Silicon or CUDA GPU; "
             "mlx — whisper on GPU; faster-whisper — whisper on CPU)"
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

    transcribe_fn = {"qwen": transcribe_qwen, "mlx": transcribe_mlx,
                     "faster-whisper": transcribe_faster_whisper}[args.backend]
    if args.model is None:
        args.model = QWEN_ASR if args.backend == "qwen" else "mlx-community/whisper-large-v3-turbo"

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


def _self_check():
    """python transcribe.py --self-check — verifies the repetition gate."""
    loop = {"start": 0.0, "end": 30.0, "text": "sca " * 60}
    real = {"start": 30.0, "end": 34.0, "text":
            "So the key distinction here is that Claude Code reads your local filesystem."}
    # A real sentence with a common word repeated normally must survive.
    the = {"start": 34.0, "end": 38.0, "text":
           "the data and the code and the paper and the tables and the figures and the notes"}
    short = {"start": 38.0, "end": 39.0, "text": "no no no"}  # under the 12-word floor
    runon = {"start": 39.0, "end": 45.0, "text": "termin" * 20}   # loop with no spaces
    # A long single word that is NOT a repetition must survive.
    longword = {"start": 45.0, "end": 47.0, "text": "counterfactuals"}

    kept = drop_degenerate_segments([loop, real, the, short, runon, longword])
    assert kept == [real, the, short, longword], kept
    assert drop_degenerate_segments([]) == []
    assert _assemble([loop, real])["text"] == real["text"]

    # Qwen cue building: punctuation carried onto aligner words, sentence split,
    # and a zero-length lone word held open for CUE_MIN_S without overlapping the next cue.
    class Item:
        def __init__(self, text, start, end):
            self.text, self.start_time, self.end_time = text, start, end
    words = _attach_punct("Hello there, world. However, yes",
                          [Item("Hello", 0.0, 0.5), Item("there", 0.5, 1.0), Item("world", 1.0, 2.0),
                           Item("However", 4.0, 4.0), Item("yes", 4.5, 5.0)])
    assert [w[0] for w in words] == ["Hello", "there,", "world.", "However,", "yes"], words
    cues = _cues(words)
    assert [c["text"] for c in cues] == ["Hello there, world.", "However, yes"], cues
    assert _cues([("However,", 4.0, 4.0)])[0]["end"] == 5.0
    assert _cues([("A", 4.0, 4.0), ("B", 5.5, 6.0)])[0]["end"] == 5.0
    # Zero-length word, then a word at the same start spread past CUE_MAX_S (Susskind QM L5):
    # one cue, never a 0 s one.
    assert _cues([("no", 4.0, 4.0), ("no", 4.0, 14.0)]) == [{"start": 4.0, "end": 14.0, "text": "no no"}]
    assert _two_lines("short") == "short"
    assert _two_lines("a" * 20 + " " + "b" * 30) == "a" * 20 + "\n" + "b" * 30
    print("ok")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()
