# Transcriber — operating rules & lesson log

## Rules
- `record` → `transcribe-meeting` → `transcribe` → `notion-meeting`. Env vars set on
  `record` propagate down (child processes), so per-run overrides go on the `record` line.
- Knobs: `TRANSCRIBE_LANG` (default `he`), `TRANSCRIBE_MODEL`, `TRANSCRIBE_VAD` (`0` off),
  `TRANSCRIBE_VAD_MODEL`, `TRANSCRIBE_PROMPT` (vocab biasing).
- Recordings live in `~/Recordings/`. The `.m4a` is the only irreplaceable artifact —
  `.txt`/`.srt` are always regenerable, and re-running `transcribe-meeting` **overwrites**
  them. Copy the old `.txt` aside first if it still has value.
- `RECORD_CONTROL=<pidfile> record` (set by Hammerspoon's `zoom_record.lua`, in
  MacSync): no terminal language prompt; `record` writes its pid and obeys
  `USR1` = discard (stop + delete the .m4a, no transcription) and `USR2` = English.

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

### 2026-09-18 — `lecture-subtitles` on a clean-audio physics course (Susskind, 10×~100 min)
Ran the script outside its design case: clear American English, not heavily accented.
Three knobs added, all defaulting to previous behaviour. 17.4 h → 10 sidecar `.srt`.

- **The default prompt is a footgun for any other subject.** `lecture-prompt.txt` is
  International Macroeconomics and `--carry-initial-prompt` re-injects it into every
  30 s window. Running on physics without `-p` biases every window toward Calvo pricing
  and the Solow residual. The `-p`/`-g` defaults should arguably be per-course files.
- **`LECTURE_MAXLEN` (new, default 0 = old behaviour).** whisper's `-ml` default is 0 =
  uncapped. On fluent continuous speech that gives 17 s / 217-char cues — fine as a
  transcript, unusable as subtitles. `-ml 90 -sow` → max 84 chars, median 3.5 s.
- **`LECTURE_BEAM` (new, default 8 = old behaviour).** Beam 8 is tuned for accented
  speech. On clean audio beam 2 was ~16x realtime vs ~9x, i.e. ~6 min per 110 min
  lecture, with no measurable quality loss (cue density identical, 17-18 cues/min).
- **whisper was never niced** — `NICE` was applied only to the x264 encode. Fixed.
  But note it barely matters: whisper.cpp runs on the **Metal GPU** and sits at ~15%
  CPU, so `nice` does not reduce heat. **Beam width is the only real thermal lever.**

#### The `-mc 192` header comment is probably wrong, and the collapse is cosmetic
The header blames `-mc 192` for a "lowercase run-on gibberish" collapse. On this course:
- The collapse is **real and progressive**: no-leading-capital ran 48% → 98% → 99.6%
  across the thirds of one 89 min lecture, *at* `-mc 192`, so 192 does not prevent it.
- It is **not** caused by the prompt's style. Four variants on identical minute-60 audio
  (comma-list prompt, prose prompt, prose without carry, no prompt at all) all gave
  33-40%. Disproven.
- Cause is **accumulated context feeding forward**: a *fresh* run on minute-60 audio is
  clean (39%); the *full* run arriving at that same audio is collapsed (98%).
- **It costs almost nothing.** Word-level similarity, collapsed vs fresh on the same
  audio: **95.2%**, residual is window-boundary alignment, not wrong words. Only case
  and punctuation are lost. Untested: whether `-mc 0` removes it — the run was cancelled
  as irrelevant once Roy said he doesn't care about case/punctuation.
- **~35% no-capital is the healthy floor** once `-ml`/`-sow` is on, since cues split
  mid-sentence. Measuring against 0% invents a defect.
- **Method lesson:** a 3 min benchmark cannot see a failure that develops over 30 min.
  I concluded twice from slices and was wrong twice. Validate on one full lecture.

#### Gotcha repeat
- `print "--- cooling ${x}s ---"` → `print:15: bad option: -`. **Already in this log**
  (2026-08-11) and I hit it again. Non-fatal without `set -e`, so the `sleep` after it
  still ran — but the log line vanished. Use `print -r --` or `printf`.
- **Never edit a running zsh script.** zsh reads it incrementally from disk, so a
  mid-run edit can corrupt the part not yet parsed. Fix after the batch exits.

### 2026-09-22 — a garbled tail is not automatically a hallucination
- **Symptom:** a 26 s Hebrew WhatsApp voice note ended with `בכיכר תה ביציאה השנייה,
  אין שלום רוזנפלד` — nonsense in context (an econ argument about labor-share channels),
  exactly the shape of the decode-loop damage from 2026-08-11.
- **Root cause:** not whisper. It is **Waze talking over the speaker** — "בכיכר, צא
  ביציאה השנייה" (at the roundabout, take the second exit) plus a street name. The
  sender recorded while driving.
- **How to tell the two apart, cheaply:** re-decode the slice **with the other model**.
  Context carry-over is model-specific; real audio is not. The Hebrew fine-tune and
  stock `ggml-large-v3.bin` produced the same words here (`רוזנטל`/`רוזנפלד` apart), so
  it is signal. A loop would have decoded correctly with fresh context — that was the
  2026-08-11 test and it is the *opposite* result.
- **Also worth the 10 s:** compare the last `.srt` timestamp against the file duration.
  Full coverage means nothing was dropped, which rules out the loss case without any
  audio-level analysis. (`volumedetect` with `-ss` before `-i` silently printed nothing;
  put `-ss`/`-t` before `-i` *and* keep `-hide_banner` off `grep`'s way, or skip it.)
- **Lesson:** the 2026-08-11 entry says don't skim past a weird transcript line. The
  other half of that rule: don't assume it is the decoder's fault either. Two models
  agreeing is the discriminator.

### 2026-09-22 — Notion accepts `.ogg` voice notes directly, no transcode
- WhatsApp voice notes (`audio/ogg`, opus) upload to Notion as-is via
  `notion-create-file-upload` → one multipart POST to `upload_url` → use the returned
  `markdown_source` in `<audio src="file-upload://…">` inside create-pages content.
  No `ffmpeg -i in.ogg out.m4a` step needed, and they render as playable audio blocks.
- Upload the media **before** creating the page: the `file-upload://` source can go
  straight into the page's markdown, so it is one create call instead of create+update.
  Upload URLs expire in ~15 min, so don't create them and then go do something slow.
- Still no update-in-place path for `notion-meeting` (open item above) — unrelated, but
  the same page-per-run trap applies to anything that re-uploads.

### 2026-10-07 — auto-start on Zoom meetings, with a keep/discard popup
- **Want:** a Zoom meeting starts → `record` starts on its own, and a 10 s popup
  [Hebrew] [English] [Discard] lets Roy throw it away. No answer = keep, Hebrew.
- **Design:** `~/.hammerspoon/zoom_record.lua` polls every 3 s for Zoom's `CptHost`
  helper and fires on its not-running → running edge, opening `record` in an iTerm tab
  (reuses `command_runner.in_iterm`). The popup is `osascript display dialog … giving up
  after 10` run via `hs.task` (async; `hs.dialog.blockAlert` would freeze Hammerspoon).
  Its answer reaches `record` as a signal to the pid in `~/Recordings/.record.pid`.
- **CptHost = in a meeting, verified both ways:** Zoom launched 13:59:56, CptHost
  appeared 14:01:22 at join, and exited 15:39:56 when the meeting ended.
- **Trap 1 — `pgrep -f` inside `hs.execute` always matches.** `hs.execute` runs the
  command through `sh -c`, whose own argv contains the pattern; pgrep skips itself but
  not its parent shell, so "is record running?" was always yes and nothing ever
  started. Fix: `pgrep -qf 'zsh .*/bin/[r]ecord'`. The `[r]` matches the real process
  but not the literal text.
- **Trap 2 — the `hs` CLI hangs from a non-tty shell** (even with `-t 3`) while waiting
  on stdin. Hammerspoon itself was idle. Always call it as `hs -t 3 -c '…' </dev/null`.
- **Guard:** the watcher skips if any `record` is already running, including one typed by
  hand (which writes no pidfile). Two of them would fight over the default output.
- **Order matters:** traps are installed before the pid is published, since USR1/USR2
  kill by default. Language is now printed after the stop, because it can change
  mid-recording.
- **Tested live:** start→Discard and start→English→Discard (ffmpeg survives USR2, the tab
  shows `Language: en` and `Discarded:`, and the .m4a is gone). **Not yet seen:** a real
  click on the popup, or the timer firing at a real meeting join.
