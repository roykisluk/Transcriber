# Local Hebrew meeting transcription (macOS)

Zero-cost, fully local pipeline: record your mic **plus** system audio (e.g. a
Zoom call, without Zoom's recorder), then transcribe to Hebrew with
whisper.cpp and the [ivrit.ai](https://huggingface.co/ivrit-ai) fine-tuned
`whisper-large-v3` model, then file it into a Notion "Meeting Notes" database
(auto title + summary). Commands: `record`, `transcribe`, `notion-meeting`.

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
- compiles the Swift CoreAudio helper `meeting-devices` into `~/bin`;
- symlinks `record`, `transcribe`, and `notion-meeting` into `~/bin` and adds
  `~/bin` to your `PATH` in `~/.zshrc` if it isn't already there.

Open a new terminal (or `source ~/.zshrc`) afterward so `~/bin` is on `PATH`.

## Usage

```sh
record                 # or: record standup   -> ~/Recordings/standup-<timestamp>.m4a
```
`record` builds the audio devices around your **current** default mic/output
(so it keeps working when you switch to AirPods), switches system output to
"Meeting Output" while recording, and restores your previous output on exit —
including Ctrl-C. Press **Ctrl-C** to stop. It then **automatically transcribes
the recording and creates a Notion "Meeting Notes" page** — auto-generated
English title (or your `record <name>` verbatim), a structured English summary
(Action Items as checkboxes + thematic sections), the recording date, and the
full transcript at the bottom of the page — printing the saved file, transcript path, and
Notion page URL. If transcription or the upload fails, the recording is safe on
disk and it prints the exact `notion-meeting` command to retry.

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
fine. Writes `<name>.txt` and `<name>.srt` next to each input.

Swap in a faster model without editing anything:
```sh
TRANSCRIBE_MODEL=/path/to/whisper-large-v3-turbo-ggml.bin transcribe file.m4a
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
  transcribes in ≈ 15–30 min). First run of `transcribe` loads the 3 GB model,
  which takes a bit longer.
- If `record` reports "the default input/output is BlackHole itself", set a real
  device as your system default in System Settings → Sound, then rerun.
