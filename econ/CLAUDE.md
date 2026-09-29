# EconTranscriber (`econ/`)

English econ seminars/lectures from an existing file. Default backend since 2026-09-29:
Qwen3-ASR + Qwen3-ForcedAligner via mlx-audio on Metal (`--backend mlx` = whisper turbo).
See `README.md` for usage. Root `Transcriber/` is the separate Hebrew meeting pipeline.

## Operating rules

- Run through `econ/.venv/` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
- `python transcribe.py --self-check` runs the repetition-gate self-checks. Run it after
  touching `drop_degenerate_segments`.
- ~0.04x RTF with Qwen3 (100 min in ~4 min), ~0.02x with whisper turbo. Budget a full
  re-run rather than patching output by hand.

## Lesson log

### 2026-08-11 — whisper invents text over the silent lead-in; three separate shapes
Webinar recordings open with dead air while people join. Whisper decodes that silence,
and it comes out three different ways — all at `00:00:00,000`, all needing different fixes:

1. **Looped token** — `"sca sca sca sca …"` ×221. Hit 6 of 11 files.
2. **Loop with no spaces** — `"termintermintermin…"` as one run-on token. The word-based
   repetition check misses this entirely; it needs a repeating-substring test.
3. **Prompt echo** — with `--prompt`, whisper regurgitates the prompt into the first cue:
   `"Paul Goldsmith-Pinkhampered Welcome back…"`, `'Terms:" Welcome back…'`.

**Fixes, in the order they actually mattered:**
- `hallucination_silence_threshold=2.0` + `condition_on_previous_text=False` in the
  mlx backend. Requires `word_timestamps=True` (roughly 2x runtime — still only ~0.03 RTF).
  This is the real fix and removes most of it.
- `drop_degenerate_segments()` as a backstop for both loop shapes, applied to *both*
  backends via `_assemble()`.
- **Do not pass a long prose `--prompt`.** This was the cause of shape 3, not a cure for
  anything: the built-in `ECON_PROMPT` already matched YouTube's auto-captions
  one-for-one on DuckDB / Parquet / EDGAR / Overleaf / Anthropic, so the custom
  per-episode prompts bought nothing and got echoed verbatim. Dropping them fixed the
  remaining 5 files.

### 2026-08-11 — `while read` loop silently truncated file paths
**Symptom:** batch script transcribed 6 of 11 files; the other 5 failed with
`File not found: <cwd>/Users/roykisluk/...` — paths missing leading characters, and
losing progressively more of them (`/Users` → `Users` → `sers`).
**Root cause:** the loop read its file list from stdin (`done < <(find … -print0)`), and
the python child inherited that same pipe and consumed bytes off the front of the next
record.
**Fix:** redirect the child's stdin: `python transcribe.py "$f" … </dev/null`. Applies to
any `while read` loop that runs a command in its body.

### 2026-08-11 — the machine transcript still needs a human proper-noun pass
Sentence quality is good, but proper nouns come out phonetic and *confidently*, so
nothing flags them. From 6h40m of these seminars: Markus→"Marcus" (89), Claude→"Cloud"
(29), EDGAR→"Edgar", FRED→"Fred", Cowork→"Co-Work", Refine.ink→"Refine.Inc",
ChatGPT→"JetGPT", Herfindahl→"Herfendahl", Safe House→"Safehouse"/"StaveHouse",
Theis Jensen→"Tice Jensen", Drew Fudenberg→"Drew Feudenberg", HMDA→"HUMDA", Golub→"Golob".

**Cheap way to find them:** list every capitalised token with a count, subtract common
sentence-initial English words, and eyeball what's left (~250 candidates for 65k words).
Check each in context before replacing — "GAP" vs "GAAP" was a real ticker discussion
(Gap Inc. moved from GPS to GAP), not an error. Then apply across `.srt`/`.txt`/`.vtt`/
`.json` together, or the formats drift apart.

### 2026-09-29 — model bake-off: Qwen3-ASR-1.7B beats whisper-large-v3-turbo on econ jargon
**Question:** is there a better English model than the default `whisper-large-v3-turbo`?
**Test:** three 5-min windows (start/middle/end, joined = 15 min) from Ben Moll's STEG
Lecture 9 (heterogeneous agents) and Susskind's Stat Mech Lecture 1, all models fed the
same 16 kHz WAV. No human captions exist for either video (YouTube auto only), so there
is **no reference WER** — scoring was a jargon checklist written before looking at
output, plus a 3-way majority vote (turbo+prompt / parakeet-tdt-0.6b-v3 /
mlx-community/Qwen3-ASR-1.7B-8bit) read by hand.
- **Econ: Qwen3 clearly best.** Where turbo and Parakeet agreed against Qwen3, Qwen3 was
  usually the one that was *right*: "Dirac" (others: "direct"), "borrowing constraint"
  (others: "boring constraint"), "Hamilton-Jacobi-Bellman" (others: "Hamilton Jackie
  Bauman" — despite HJB/Bellman sitting in ECON_PROMPT), "Den Haan" (others: "then
  Han"). **Lesson: majority vote punishes the best model on jargon** — read the spots.
- **Turbo drops short phrases** (~8 in 15 min: "because time is continuous", "rather than
  discrete time", "value function"); Parakeet and Qwen3 independently heard the same
  words. Turbo also normalises "going to"→"gonna" and "one"→"1" — style, not errors.
- **Physics: roughly a tie.** Susskind's vocabulary is plain (entropy, probability); every
  model drops a few short phrases.
- **Parakeet v3:** fastest (9 s per 15 min vs turbo ~20 s, Qwen3 ~30 s) but worst on
  names ("heterogeneous asian", "frocker", "h" for "age"). No prompt support.
- **ECON_PROMPT barely matters:** turbo with vs without it — same jargon hits, 5% WER apart.
- **Dead ends:** Cohere Transcribe 03-2026 — both `beshkenadze/*-mlx-8bit` and `-fp16`
  output multilingual gibberish under mlx-audio (needs `sentencepiece` just to load);
  the original `CohereLabs/` repo is HF-gated. mlx-audio's `--context` is **silently
  ignored** for Qwen3 (output identical with/without) — no vocab biasing via that runtime.
- **Open:** Qwen3 via mlx-audio chunks long audio itself (`--chunk-duration`, default
  30 s in the CLI) and its `.srt` is **one cue per chunk** (4 cues for 15 min) — usable
  subtitles need the separate Qwen3-ForcedAligner (≤5-min spans); not yet
  checked on a full 100-min file for drift or truncation. Default model **unchanged**.

### 2026-09-29 — Qwen3-ASR + ForcedAligner full-length: subtitles work, with two traps
Full 100-min run on the Moll lecture (Qwen3-ASR-1.7B-8bit text, Qwen3-ForcedAligner-0.6B-8bit
word times, both mlx-community, via mlx-audio; own glue script — mlx-audio does not chain them).
- **Trap 1 — the aligner's clock stops at 266.56 s** (= 3332 x 80 ms) although the model
  card says "up to 5 minutes". With ~280 s chunks the last ~15 s of every chunk (51 words
  in chunk 1) all got `start == end == 266.56`. **Symptom:** a run of identical word
  timestamps at the chunk tail. **Fix:** chunk at 240 s (`split_audio_into_chunks`,
  low-energy cut points).
- **Trap 2 — lone words with `start == end`** (7 in 100 min, e.g. "However," after a
  pause) became zero-length cues that never display. **Fix:** hold every cue >= 1 s,
  capped at the next cue's start.
- **Timing verified three ways:** Qwen3 vs turbo word starts: median 0.06 s, flat in every
  5-min bin (no drift). Parakeet as tie-breaker: |Qwen3 − Parakeet| median 0.08 s vs
  |turbo − Parakeet| 0.12 s; on the 1.5% of words where Qwen3 and turbo disagree by >1 s,
  Parakeet sided with Qwen3 90 times, turbo 64. **Qwen3's timing is at least as good as turbo's.**
- **Coverage:** Qwen3 16.7k words vs turbo 15.9k, spread evenly (every 5-min bin +2–10%),
  no gaps, no loops — consistent with turbo dropping short phrases, not Qwen3 inventing.
- **Cost:** 4 min for 100 min of audio (ASR 215 s, aligner 23 s) vs turbo ~2 min.
- **Cues:** built from word times — break at sentence end (if >1.5 s), >1 s pause, 84
  chars or 7 s; two lines split at the space nearest the middle.
- Output parked next to the video as `….qwen3.en.srt`; `transcribe.py` still unchanged.

### 2026-09-29 — Qwen3 is now the default backend (`--backend qwen`)
Roy chose it after the two entries above. `transcribe_qwen()` in `transcribe.py` = the
scratch glue script: 240 s chunks, ASR → aligner per chunk, `_attach_punct` carries the
ASR's punctuation onto the aligner's bare words, `_cues` builds cues, then the same
`_assemble` (repetition gate) and the same four writers. `--backend mlx` = old turbo path.
- **Silent lead-in, the old worst case, is clean.** 3 webinar starts (Golub Pt 1, GP Ep 1
  — the batch that looped `sca sca` under turbo — and SoFiE) plus a synthetic 90 s silence
  + 90 s pink noise before speech: Qwen3 emitted **nothing** over the dead air; first
  cue at exactly 180.0 s. An all-silence file returns empty text → the aligner is skipped
  (it would get an empty string otherwise).
- **SRT/VTT now wrap cues to two lines** (`_two_lines`, split near the middle) for every
  backend — turbo's long cues were one 300-char line.
- **`--prompt`/`--no-econ-prompt` do nothing on `qwen`** (mlx-audio drops Qwen3's context);
  it prints a note. ECON_PROMPT stays for the whisper backends.
- **Close calls flip between environments.** Same bit-identical audio, run in the scratch
  venv (mlx 0.32.3) vs `econ/.venv` (mlx 0.32.0): ~100 word diffs in 16.7k, mostly
  punctuation, but also "borrowing"↔"boring" and "Kolmogorov forward"↔"Komogorov-Fokker".
  Within one venv, three runs were identical — it is numerics, not sampling. Neither run
  is better overall, and both keep the lead over turbo on the full lecture:
  Hamilton-Jacobi-Bellman 20/20 vs 1 (turbo: "Jackie" x16), borrowing constraint 13/18
  vs 3 (turbo: "boring constraint" x21), Dirac 9/9 vs 2. **Regression to know:** one run
  spelled Kolmogorov wrong every time (0 vs turbo's 6) — the proper-noun pass (entry
  above) is still needed.
- **Verified:** `--self-check` extended (punctuation carry, sentence split, zero-length
  cue held 1 s but never into the next cue, two-line wrap) and mutation-tested
  (`CUE_MIN_S=0` → caught). End-to-end on the full .webm: 233 s for 99.6 min, all four
  formats; silent file OK; `--backend mlx` still OK.
