# EconTranscriber (`econ/`)

English econ seminars/lectures from an existing file. Default backend since 2026-09-29:
Qwen3-ASR + Qwen3-ForcedAligner via mlx-audio on Metal (`--backend mlx` = whisper turbo).
See `README.md` for usage. Root `Transcriber/` is the separate Hebrew meeting pipeline.

## Operating rules

- Run through `econ/.venv/` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
- **On tbox (CUDA GPU):** run through `~/envs/asr`; same command, `transcribe_qwen` picks PyTorch
  off Apple Silicon. Pass the lecture's own terms with `--prompt` (see 2026-09-30). Rebuild the env
  (uv, because conda's Python 3.13 rejects the TAU proxy CA):
  `export PATH=$HOME/.local/bin:$PATH UV_SYSTEM_CERTS=1; uv venv ~/envs/asr --python 3.12`
  `uv pip install -p ~/envs/asr/bin/python torch torchaudio --index-url https://download.pytorch.org/whl/cu128`
  `uv pip install -p ~/envs/asr/bin/python qwen-asr`. ffmpeg comes from the image (apt).
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

### 2026-09-29 — throttled batch: whole Stat Mech course (10 lectures, 17 h) in ~2 h
Roy wanted it cool, not fast. **Throttle = SIGSTOP/SIGCONT duty cycle** on the python pid,
10 s on / 20 s off, 60 s cool-down between files, `caffeinate -i -w $$` for the run.
`nice` is useless here — the work is on the Metal GPU (see root `CLAUDE.md`, 2026-09-18).
Result: 11:09–13:04, ~10 min per 100-min lecture (vs ~4 unthrottled), no thermal warnings
in `pmset -g therm`. All 10 full coverage, 123–135 wpm, no zero-length cues, no loops.
- **Trap — `wait $pid` returns 145 after a SIGSTOP-throttled child exits cleanly.**
  145 = 128 + 17 = SIGSTOP on macOS: zsh reports the last *stop* status, not the exit
  code. Every lecture "failed" by rc while every log said `All done.`. Judge success by
  the log / output files, or poll `kill -0` then read the status another way.
- Shorter bursts at the same duty (e.g. 3 s / 6 s) would smooth temperature swings
  further; 10/20 was fine.

### 2026-09-30 — Susskind QM batch; a zero-length cue when two cues share a start
Same throttle at 5 s on / 10 s off, 90 s cool-down: 10 lectures (18.1 h) in 2 h 13 min, RTF
~0.10, no thermal warnings. Full coverage, 125–136 wpm.
- **Symptom:** one cue in ~16k with `start == end` (QM L5, 59:21, "no" from "no no no no").
- **Root cause:** the aligner gave word 1 `start == end` and word 2 the *same* start but a 14 s
  span. Word 2 alone exceeds `CUE_MAX_S`, so `_cues` split between them; the 1 s hold is capped
  at the next cue's start, which equalled this cue's start → 0 s.
- **Fix:** `_cues` merges a group into the previous cue when its start is not later. Self-check
  case added and mutation-tested (merge disabled → AssertionError).

### 2026-09-30 — ported to tbox's GPU (PyTorch `qwen-asr`); a flaky host CPU
Same `--backend qwen`; off Apple Silicon `_transcribe_qwen_torch` runs Qwen's own package with the
original `Qwen/Qwen3-ASR-1.7B` + `Qwen3-ForcedAligner-0.6B` (bf16, RTX 4000 Ada 20 GB).
- **Speed:** 100-min Moll lecture in ~6 min (RTF 0.06-0.08, incl. model load); GPU 95-100%, 20 GB.
- **Own chunking at 180 s, not qwen-asr's.** Its `transcribe()` re-chunks anything longer than
  180 s for alignment and then `"".join`s the chunk texts — English words fuse at every boundary.
  So chunks are cut here (its `split_audio_into_chunks`) and passed as one batch; offsets added back.
- **`max_new_tokens` default is 512** in the transformers path; 180 s of fast speech is ~500 words.
  Raised to 2048.
- **Timing matches the Mac:** median word-time gap 0.07 s, flat across the lecture, same last cue,
  no zero-length cues, no identical-timestamp runs (the 266 s aligner trap does not apply at 180 s).
- **Jargon is worse without a prompt, and it is the model build, not the chunking.** Unprompted:
  HJB 8 vs Mac 20, Dirac 1 vs 9 ("direct"), Kolmogorov 0 vs 7 ("Komarov 40"), "gonna" x40 vs 8.
  One 179 s slice run alone, no batching: MLX 8-bit → "Dirac" x8; PyTorch bf16 *and* fp32 →
  "direct" x8. **Dead ends:** left padding (tokenizer ships `right`) — output identical;
  fp32 — no help; the generic ECON_PROMPT — no change at all.
- **Fix: a short list of the lecture's own terms via `--prompt`.** qwen-asr really uses the
  context (the mlx-audio *CLI* `--context` was ignored, 2026-09-29; its `generate()` now takes
  `system_prompt`/`hotwords` — untested, the probe slice was already right on the Mac). With
  "Dirac delta, Kolmogorov forward equation, Fokker-Planck equation, Hamilton-Jacobi-Bellman
  equation" (ECON_PROMPT + terms, rep1): Dirac 11, Kolmogorov 7, HJB 12 on the full lecture.
- **Trap — the prompt gets recited into the transcript.** The 90 s silent lead-in test passed
  (first word at 90.1 s with any prompt) and was false comfort: on the full lecture the prompt
  appeared verbatim twice, ECON_PROMPT and terms alike, at 33:02 and 1:38:4x — exactly the
  ends of chunks 11 and 33 (boundaries at 1982 s, 5925 s): the model recites the context glued to
  a chunk's last real words ("So now.Continuous-time ..."). **Fixes:** `_strip_echo` cuts any match
  covering >= 60% of the prompt from each chunk's text (a first version only looked at the chunk
  *start* and missed both); the aligner's recital words then go unmatched in `_attach_punct`.
  ECON_PROMPT is now whisper-only — on qwen it bought nothing and leaked. Recitals do not
  reproduce on those chunks run alone; only a full run shows them.
- **Final, terms-only prompt, recitals stripped:** Dirac 9 (Mac 9), Kolmogorov 18 (Mac 7), HJB 12
  (Mac 20), borrowing constraint 11 (Mac 13); word agreement with the Mac 95.9%, median word-time
  gap 0.06 s. Counts before the strip were inflated by the two recitals (+2 each).
- **Deterministic:** two unprompted full runs gave byte-identical `.srt` files — evidence that runs
  which *complete* are not silently corrupted by the flaky CPU below.
- **Random crashes are the host CPU — CONFIRMED later the same day** by a pure-Python stress loop
  that failed the same way at max single-core boost (container-standard `ops/CONTAINER.md`).** 5 of 10 full
  runs failed: four "impossible" TypeErrors inside qwen-asr's pure-Python `fix_timestamp` loop
  (`dp = [1] * n`, then `dp[i]` → `'range_iterator' object is not subscriptable`) and one hard
  segfault in python3.12 (dmesg, 14:13). Different error every time, same code and audio passes on
  rerun, and the box's dmesg shows Python segfaults for days (container-standard
  `ops/CONTAINER.md` 2026-09-24). CPU is an i9-14900K — the Raptor Lake instability family. BIOS is
  Dell 1.27.0 (2026-06), which should carry Intel's microcode fix; it stops degradation, it does not
  undo it. **Rerun on failure** until the host is fixed (warranty, or turbo capped).
- **Also works:** `mlx[cuda]` 0.32.3 + mlx-audio on tbox (`~/envs/asr-mlx`, GPU matmul OK) — the
  exact Mac stack is available there if the MLX output is wanted; not wired or benchmarked.

