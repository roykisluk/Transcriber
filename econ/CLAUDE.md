# EconTranscriber (`econ/`)

English econ seminars/lectures from an existing file, via mlx-whisper on Metal.
See `README.md` for usage. Root `Transcriber/` is the separate Hebrew meeting pipeline.

## Operating rules

- Run through `econ/.venv/` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
- `python transcribe.py --self-check` runs the repetition-gate self-checks. Run it after
  touching `drop_degenerate_segments`.
- ~0.02–0.03x RTF on Apple Silicon: 6h40m of video transcribes in about 8 minutes. Budget
  a full re-run rather than patching output by hand.

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
