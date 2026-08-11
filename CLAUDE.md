# Transcriber — operating rules & lesson log

## Rules
- `record` → `transcribe-meeting` → `transcribe` → `notion-meeting`. Env vars set on
  `record` propagate down (child processes), so per-run overrides go on the `record` line.
- Knobs: `TRANSCRIBE_LANG` (default `he`), `TRANSCRIBE_MODEL`, `TRANSCRIBE_VAD` (`0` off),
  `TRANSCRIBE_VAD_MODEL`, `TRANSCRIBE_PROMPT` (vocab biasing).
- Recordings live in `~/Recordings/`. The `.m4a` is the only irreplaceable artifact —
  `.txt`/`.srt` are always regenerable, and re-running `transcribe-meeting` **overwrites**
  them. Copy the old `.txt` aside first if it still has value.

## Lesson log

### 2026-08-11 — English meeting came out as Hebrew
- **Symptom:** `record` on an English meeting produced a fluent-Hebrew transcript
  (`meeting-20260811-172946.txt`) — whisper translated instead of transcribing.
- **Root cause (two layers):** (1) `transcribe` hardcoded `-l he`. (2) The bigger one —
  `ggml-model-whisper-custom.bin` is a **Hebrew fine-tune and emits Hebrew regardless of
  `-l`**. Setting `-l en` alone changed almost nothing. Verified on a 60 s slice:
  custom model + `-l en` → `היי`, stock `ggml-large-v3.bin` + `-l en` → `Hi.`
- **Fix:** `LANG_CODE=${TRANSCRIBE_LANG:-he}`, `-l "$LANG_CODE"`, **and** the model
  default follows the language — `he` → custom fine-tune, anything else → stock
  `ggml-large-v3.bin`. `TRANSCRIBE_MODEL` still overrides both. English meetings:
  `TRANSCRIBE_LANG=en record`. Wrong-language transcript kept as `<file>.he-wrong.txt`.
- **Lesson:** a language flag is not a language switch when the model is fine-tuned.
  Check what the model *is* before trusting `-l`. The first fix looked right, changed
  the flag, and produced the same Hebrew output.
- **Rejected:** `-l auto`. Whisper's auto-detect flips mid-file on code-switched
  Hebrew/English speech, which is Roy's common case — a wrong explicit language is at
  least consistent and obvious. (Previously rejected 2026-07-20 for the same reason.)
- **Open:** `notion-meeting` uploaded the Hebrew version to Notion before the fix; there
  is no update-in-place path, re-running creates a second page.
### 2026-08-11 — `record` asks the language, without costing audio
- **Want:** `record` prompts "Meeting in English? [y/N]", 5 s timeout → Hebrew, and
  capture must start the instant `record` is typed (nothing lost to the prompt).
- **Design:** the language isn't needed until transcription (after Ctrl-C), so ffmpeg is
  **backgrounded first** and the prompt runs *during* recording. Measured: ffmpeg starts
  at +0.04 s, unchanged by the prompt.
- **Trap 1 — `read -q -t5` never times out in zsh.** Prompt appears, waits forever.
  `read -k1 -t5` timed out in isolation but hung inside `record`. Settled on letting the
  **tty driver** enforce it: `stty -icanon min 0 time 50` + `dd bs=1 count=1`, which
  cannot hang and gives a true single-keypress. Measured: y→en and n→he instantly,
  silence→he at 5.1 s.
- **Trap 2 — zsh without job control does NOT forward the terminal's SIGINT to a
  background job.** Backgrounding ffmpeg silently breaks Ctrl-C unless the trap relays
  it: `trap 'stop; restore' INT TERM` with `stop() { kill -INT $ff_pid }`. SIGINT, never
  KILL, so ffmpeg finalizes the file. Verified: ffmpeg receives INT, m4a finalized,
  record proceeds to transcription.
- **Trap 3 — `wait` is interrupted by the trap**, so it needs a loop:
  `while kill -0 $ff_pid; do wait $ff_pid; done`.
- **Behaviour change:** ffmpeg now runs with `-nostdin` (so it can't swallow the language
  keypress), which means **`q` no longer stops a recording — Ctrl-C only.**
- **Also:** the INT/TERM handler doesn't exit, so `record` survives a plain `kill`; test
  leftovers needed `kill -9`. Pre-existing, not a regression.
- **Testing note:** `zpty` harnesses for this were flaky and produced contradictory
  results across trivially different variants; `script(1)` can't allocate a pty in the
  agent sandbox. Test the prompt in isolation, and test the signal relay with a plain
  background process + `kill -INT` (no pty needed). Also: `print "--- x ---"` fails with
  `bad option: -` — leading dashes are parsed as options.

### 2026-08-11 — `-ml` on the near channel only
- **Symptom:** in the English transcript the `[Me]` side had 21 lines to `[Them]`'s 170,
  with four minutes of separate replies fused into one line at the top.
- **Root cause:** VAD + a sparse channel. The mic is mostly silence, so whisper's window
  spans minutes of wall-clock and emits one segment stamped at the first reply's time.
  The merge sorts on start time, so every reply in that block lands at the top.
- **Fix:** `-ml 60 -sow` (via `TRANSCRIBE_MAXLEN`) on the **near channel only**, so
  `transcribe-meeting` now makes two `transcribe` calls instead of one.
- **Measured** (21 min meeting, speaker switches as the interleaving proxy): no `-ml` 20
  switches · `-ml 60` both channels 40 but every `[Them]` line chopped at 60 chars ·
  `-ml 60` near only 38 switches with `[Them]` intact. `-ml 30` (164 segments) too choppy.
- **Costs:** none in time — `-ml` re-segments already-decoded output, measured 33 s vs
  34 s vs 33 s for baseline/60/30. The real cost is that split points get whisper's
  *heuristic* word timestamps, so a fused utterance can still be cut mid-sentence
  ("But I can observe" / "bonuses.") with the pieces landing at different times.
  Acceptable on the near side (short acknowledgements); not on the content-bearing side.
- **Rejected:** capping both channels. Better ordering isn't worth shredding the far
  side, which is where the meeting's substance lives.

### 2026-08-11 — whisper decode loop silently ate 2.5 minutes of the meeting
- **Symptom:** `[Them] And then you have to pay for it.` repeated 60x in the transcript.
- **Not cosmetic — data loss.** That span (17:16–19:45) measures **−30 dB, LOUDER than
  the rest of the call** (−36 dB): ordinary speech, not silence. Sixty repeated lines
  replaced real content (first welfare theorem ⇒ efficient flexible-price allocation ⇒
  no role for policy; skepticism that sticky wages bind under a positive shock). It was
  missing from the transcript *and* from the Notion summary built on it.
- **Root cause:** whisper's text-context carry-over. Default `-mc -1` = unlimited, so each
  segment's text is fed back as the next one's prompt; once a phrase repeats it reinforces
  itself. Transcribing the same slice standalone (fresh context) decoded it correctly —
  that's the test that proves it's context, not audio.
- **Fix:** `-mc 64` (`TRANSCRIBE_MAXCTX`), now the default in `transcribe`.
- **Measured** on the far channel, loop lines / real words / punctuation marks per 100
  words: `-mc -1` 60 / 1082 / 18.3 · `-mc 0` 0 / 1341 / **6.7** · `-mc 64` **0 / 1316 /
  17.6**. `-mc 0` is the widely-cited fix but it strips punctuation continuity along with
  the loop; a *bounded* window keeps both. 16 and 128 also work — 64 is the middle.
- **Checked:** Hebrew recordings don't show this (top repeat is a genuine `אוקיי?` ×7),
  so it's a latent trap, not chronic.
- **Also settled:** `-mc 64` does NOT make the near-side `-ml` redundant — without `-ml`
  the mic channel still returns 21 segments with one 4.8-minute fused cue. Keep both.
- **Lesson:** a repeated line in a transcript is not noise to skim past. Check the audio
  level of the span it covers before assuming nothing was there.

- **Open:** `transcribe-meeting`'s awk merge printed `towc: multibyte conversion failure`
  on a Hebrew `.srt` line — macOS awk under a non-UTF-8 locale. Ran the retry with
  `LC_ALL=en_US.UTF-8`. If it recurs, set the locale inside `transcribe-meeting`.
