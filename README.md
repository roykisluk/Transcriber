# Local Hebrew meeting transcription (macOS)

Zero-cost, fully local pipeline: record your mic **plus** system audio (e.g. a
Zoom call, without Zoom's recorder), then transcribe to Hebrew with
whisper.cpp and the [ivrit.ai](https://huggingface.co/ivrit-ai) fine-tuned
`whisper-large-v3` model, then file it into a Notion "Meeting Notes" database
(auto title + summary). Commands: `record`, `transcribe`, `notion-meeting`.

Mic and system audio are recorded on **separate channels** and transcribed
separately, so the final transcript is speaker-labeled: `[Me]` (your mic / near
end) and `[Them]` (the Zoom call / far end). Silence gaps are dropped with
Voice Activity Detection (VAD), which kills whisper's habit of hallucinating a
stray word into a quiet stretch.

## The other two tools in here

The root of this repo is the Hebrew meeting pipeline above. Two sibling tools,
merged in on 2026-08-04 (histories preserved), handle the cases it doesn't:

| Dir | Tool | Reach for it when |
|---|---|---|
| — (root) | `record` / `transcribe` / `notion-meeting` | **Hebrew**, a live meeting you record yourself, speaker labels, filed to Notion. whisper.cpp + ivrit.ai. |
| [`econ/`](econ/) | `transcribe.py` | **English** econ seminars and lectures you already have a file of. mlx-whisper on Metal, econometrics vocabulary prompt, SRT/VTT/JSON out. |
| [`enhance/`](enhance/) | `enhance_audio.py` | The recording sounds bad — a YouTube rip or a far-mic Zoom. Run **before** either transcriber. |

They stay separate programs on purpose: different models (whisper.cpp vs
mlx-whisper), different languages, different inputs. `enhance/` is the shared
pre-step for both. Each dir has its own README and, where it needs them, its own
`requirements.txt`.

## Fresh-machine setup

```sh
git clone <this-repo> Transcriber
cd Transcriber
./install.sh          # run in a Terminal — BlackHole needs your macOS password
```

`install.sh` is idempotent (safe to re-run). It:
- installs Homebrew (if missing), then `whisper-cpp`, `ffmpeg`,
  `switchaudio-osx`, and the `blackhole-2ch` cask;
- verifies the model file `ggml-model-whisper-custom.bin` by size and downloads
  it only if missing/corrupt (it is **not** committed to git — ~3 GB);
- downloads the small Silero VAD model `ggml-silero-v5.1.2.bin` (~865 KB,
  size-verified, also gitignored);
- compiles the Swift CoreAudio helper `meeting-devices` into `~/bin`;
- symlinks `record`, `transcribe`, `transcribe-meeting`, and `notion-meeting`
  into `~/bin` and adds `~/bin` to your `PATH` in `~/.zshrc` if it isn't already
  there.

Open a new terminal (or `source ~/.zshrc`) afterward so `~/bin` is on `PATH`.

## Usage

```sh
record                 # or: record standup   -> ~/Recordings/standup-<timestamp>.m4a
```
`record` builds the audio devices around your **current** default mic/output
(so it keeps working when you switch to AirPods), switches system output to
"Meeting Output" while recording, and restores your previous output on exit —
including Ctrl-C. Press **Ctrl-C once** to stop and wait a moment — repeated
presses hard-kill ffmpeg. (Recordings are written as fragmented mp4, so even a
hard kill loses at most the last ~5 seconds.) It then **automatically transcribes
the recording and creates a Notion "Meeting Notes" page** — auto-generated
English title (or your `record <name>` verbatim), a structured English summary
(Action Items as checkboxes + thematic sections), the recording date, and the
full transcript at the bottom of the page — printing the saved file, transcript path, and
Notion page URL. The transcript is speaker-labeled `[Me]` (your mic) / `[Them]`
(the call), so the summary and action items can attribute who said what. If
transcription or the upload fails, the recording is safe on disk and it prints
the exact `notion-meeting` command to retry.

Requires the `claude` CLI logged in (`claude login`) with the Notion connector
enabled — the upload uses your Claude Max subscription, no API key.

```sh
notion-meeting ~/Recordings/standup-20260719-143000.m4a        # or ... "Weekly sync"
```
Transcribes the file if its `.txt` doesn't exist yet, then files it into Notion.
Run it by hand to retry a failed upload (no re-recording) or to import an old
recording. With a second argument it becomes the Notion "Meeting name" verbatim.

```sh
transcribe ~/Recordings/standup-20260719-143000.m4a
```
Accepts any format (m4a/mp4/mp3/wav…) and multiple files; paths with spaces are
fine. Writes `<name>.txt` and `<name>.srt` next to each input. `transcribe` is
the plain single-track path; the split-channel `[Me]`/`[Them]` transcript is
produced by `transcribe-meeting <recording.m4a>`, which `record` and
`notion-meeting` call for you. `transcribe-meeting` splits the recording's
channels (mic first, BlackHole last), transcribes each side, and merges the two
by timestamp into one labeled `<name>.txt`. Recordings with 1–2 channels (old
single-track files) fall through to plain `transcribe` unchanged.

### Environment overrides

Swap in a faster model without editing anything:
```sh
TRANSCRIBE_MODEL=/path/to/whisper-large-v3-turbo-ggml.bin transcribe file.m4a
```

**VAD** (Voice Activity Detection) is on by default — it feeds whisper only the
speech regions, so silence gaps stop producing hallucinated words. Turn it off
for a run with `TRANSCRIBE_VAD=0 transcribe file.m4a` (e.g. very quiet audio
where the detector may over-trim). Override the VAD model with
`TRANSCRIBE_VAD_MODEL=/path/to/ggml-silero.bin`.

**Vocabulary biasing** — set `TRANSCRIBE_PROMPT` to nudge whisper toward spelling
domain terms in proper Latin script (e.g. English econometrics jargon that would
otherwise be transliterated into Hebrew letters). It is passed as whisper's
initial prompt (~224-token budget; its influence decays over the meeting, so it
mainly biases the opening minutes) and is unset by default (exactly the stock
behavior). Worth a real-meeting A/B before you trust it — a wrong prompt can also
push spurious terms in. Example:
```sh
export TRANSCRIBE_PROMPT="Economics seminar. Terms: identification, instrumental variables, treatment effects, GMM, standard errors, fixed effects, difference-in-differences, counterfactual."
```

## Manual steps (unavoidable)

1. **macOS password** — once, when `install.sh` installs BlackHole. If
   "Meeting Input/Output" still can't be created afterwards (BlackHole not
   visible as an audio device), run `sudo killall coreaudiod` once to load the
   new driver.
2. **Microphone permission** — the first time you `record`, macOS shows a
   mic-access dialog; click **Allow** (for your terminal app).
3. **Zoom** — leave audio output on **"Same as System"** (Zoom's default) so it
   plays through Meeting Output and gets captured.

## Notes

- **Volume keys stop working while recording.** That's expected macOS behavior
  with any multi-output device active; it reverts when `record` restores your
  output on exit.
- **Use headphones during calls.** With speakers, the mic also picks up the room
  echo of the far end — it still transcribes, just messier.
- **Speed:** large-v3 on Apple Silicon runs ≈ 2–4× realtime (a 1-hour meeting
  transcribes in ≈ 15–30 min). Split-channel meetings transcribe **twice** (near
  and far), so budget roughly double that; VAD claws some of it back by skipping
  silence. First run of `transcribe` loads the 3 GB model, which takes a bit longer.
- If `record` reports "the default input/output is BlackHole itself", set a real
  device as your system default in System Settings → Sound, then rerun.

## Lecture subtitles (`lecture-subtitles`)

A second, separate pipeline for **recorded lectures**: heavily accented English,
dense technical vocabulary, 2+ hours long, output meant to be watched as
subtitles. Different problem from meetings, so different settings (see below).

```sh
lecture-subtitles -s 46:00 -S lecture_object.mp4 lecture_dv.mp4
lecture-subtitles -n talk.mp4                      # subtitles only, no render
```
`-s/-t` take ffmpeg time syntax (`46:00`, `1:15:30`, seconds). `-S` supplies a
slide-capture video: its picture is muxed with the main file's audio and
letterboxed, so subtitles sit in a black strip and never cover the slide.
Writes `<base>_subs.srt`, `<base>_transcript.txt`, and `<base>_subtitled.mp4`
next to the input. Burning needs `mpv` (`brew install mpv`) because Homebrew's
ffmpeg is built without libass and cannot render subtitles at all.

Vocabulary lives in `lecture-prompt.txt` (fed to whisper every window) and
`lecture-glossary.txt` (regex→replacement fixes applied after). **Both are
course-specific — swap them per subject.** Build the prompt from the actual
slide deck; it is the single biggest accuracy lever for an unfamiliar accent.

### Why these settings differ from `transcribe`

Measured on a 2.5 h lecture, scoring transcripts against known technical terms:

- **`-mc 192`** — whisper's text context is 224 tokens, so any `-mc` ≥ 224 means
  *no cap*. Uncapped, the transcript collapses into lowercase run-on gibberish
  (91% of cues, one 88-cue run) and terminology is destroyed. 192 is the highest
  usable value and scored best (net 33 vs 10–13 at other caps).
- **`-sns`** — without it whisper replaces stretches it cannot decode with canned
  *"thanks for watching, subscribe to my channel"* filler. This is not silence
  padding: it overwrites **real speech** with invented text that reads fluently.
  `srtfix.py` also fails loudly if any such phrase survives.
- **VAD on** — dead air (breaks, writing on the board) is where the canned
  filler appears. VAD alone makes raw cue timing *worse*, which is why it looked
  wrong at first — but `srtfix.py` re-times every cue from the silence map
  afterwards, so that cost is undone and only the benefit remains. Measured over
  70 min of lecture: 2 hallucinations → 0, and *more* speech recovered
  (7595 → 7718 words). `LECTURE_VAD=0` disables it.
- **Prompt carried every window** (`--carry-initial-prompt`) is what keeps
  technical spelling stable across hours of audio.
- **No `-ml`/`-sow`/`-dtw`** — these look like the obvious way to get
  subtitle-sized cues, but they alter decoding and measurably degrade the text.
  Cue splitting is done afterwards, in `srtfix.py`, where it is free.

### `srtfix.py`

Turns whisper's 30 s / 300-character segments into subtitles:

- splits cues to ≤ 84 chars (2 × 42) on word boundaries;
- places each cue on the audio's **speech** timeline (from ffmpeg
  `silencedetect`), so text never drifts across a pause, and a cue never starts
  inside one;
- **holds each line until the next one needs the screen** — the speaker pauses
  mid-sentence, and cues timed to speech alone vanish mid-thought. On a 10 min
  sample this cut blank screen from 201 s to 4.5 s. `HOLD = False` reverts;
  `HOLD_MAX` caps very long holds;
- applies the glossary, then runs the hallucination gate.

`python3 srtfix.py --demo` runs its self-checks.

**Speed:** ≈ 20× realtime on an M-series Mac (a 70 min lecture transcribes in
≈ 3 min); the video render is usually the slower half.

**Caveat:** the gate catches whisper's *known* canned phrases. A hallucination
phrased like plausible subject matter would pass it — spot-check long runs.
