# Task: fully automated, local, free Hebrew meeting transcription on macOS

## Goal
Build and verify a zero-cost, fully local pipeline on this Mac, with two commands:

1. `record` — record meetings: my microphone + system audio (i.e. Zoom calls WITHOUT Zoom's built-in recorder, audio only).
2. `transcribe` — transcribe recordings to Hebrew text with whisper.cpp + the ivrit.ai fine-tuned model.

Everything must be idempotent, fully scripted, and replicable on another Mac by cloning this folder and running one script. Minimize manual steps; the allowed ones are listed at the end.

## Decisions already made — do not relitigate
- Engine: **whisper.cpp** via Homebrew (`brew install whisper-cpp`, provides `whisper-cli`). Note: `whisper-cli` only reads wav/mp3/flac/ogg — always convert inputs with ffmpeg to 16 kHz mono pcm_s16le wav first.
- Model: **ivrit-ai/whisper-large-v3-ggml**. Already downloaded in this folder as `./ggml-model-whisper-custom.bin`, exactly **3,095,033,483 bytes**. Verify size; do NOT re-download if valid. Download fallback for fresh machines (resumable, size-verify after):
  `https://huggingface.co/ivrit-ai/whisper-large-v3-ggml/resolve/main/ggml-model.bin`
- GUI apps (Buzz, Vibe) were already tried and rejected as buggy. CLI only.
- System-audio capture: **BlackHole 2ch** (`brew install --cask blackhole-2ch`) + aggregate devices. No paid tools.

## Deliverables (all in this folder, git-tracked)
1. `install.sh` — idempotent installer:
   - Homebrew present or install it; then: whisper-cpp, ffmpeg, switchaudio-osx, blackhole-2ch (cask).
   - Model: verify local file size; download only if missing/corrupt.
   - Compile the Swift helper (item 4).
   - Install `record` and `transcribe` into `~/bin` (create; add to PATH in `.zshrc` if missing).
2. `transcribe` (zsh): `transcribe <file> [more files...]`
   - Any input format (m4a/mp4/mp3/wav...), spaces in paths must work.
   - ffmpeg → temp 16k mono wav → `whisper-cli -m <model> -l he -otxt -osrt -of <input-path-without-extension>`.
   - Outputs `.txt` + `.srt` next to each input. Clean up temp files.
   - `TRANSCRIBE_MODEL` env var overrides the model path (so I can later swap in ivrit-ai/whisper-large-v3-turbo-ggml for speed).
3. `record` (zsh): `record [name]`
   - Ensure audio devices (item 4) exist, rebuilt around the CURRENT default input/output — must keep working when I switch to AirPods.
   - Save current default output; switch default output to "Meeting Output" via SwitchAudioSource; RESTORE it on exit via trap, including Ctrl-C.
   - Record from "Meeting Input" with `ffmpeg -f avfoundation` (resolve the device index by name at runtime), mono AAC ~96k, to `~/Recordings/<name>-<timestamp>.m4a`. Ctrl-C stops.
   - Print the ready-to-paste `transcribe` command for the saved file.
4. Audio devices created **programmatically** — no Audio MIDI Setup clicking. Small compiled Swift CoreAudio helper using `AudioHardwareCreateAggregateDevice`:
   - "Meeting Input" = aggregate of current default input + BlackHole 2ch; drift correction on BlackHole; master = the mic.
   - "Meeting Output" = stacked aggregate (`kAudioAggregateDeviceIsStackedKey` = multi-output) of current default output + BlackHole 2ch; drift correction on BlackHole; master = the real output.
   - Use fixed aggregate UIDs; destroy + recreate when the underlying defaults changed.
   - Edge cases: if the current default device IS one of our aggregates (stale state), resolve the real device from its sub-device list; if the default is BlackHole itself, abort with a clear message.
5. `README.md` — usage, fresh-machine setup (`git clone` → `./install.sh`), and the unavoidable manual steps.
6. Initialize a git repo here; `.gitignore` the model file and recordings.

## Verification — do all of this yourself, don't hand me steps
- Smoke test: generate 2 s of silence with ffmpeg, transcribe it — proves model loads (integrity) and Metal works.
- Device test: create the aggregates, confirm both appear, confirm default output is restored after a recording.
- Live test: make a short test recording (warn me first — macOS will show the microphone permission dialog and I'll click Allow), speak a Hebrew sentence, run `transcribe` on it, and show me the Hebrew output.
- Re-run `install.sh` once more to prove idempotency.

## Allowed manual steps (exactly these; prompt me when needed)
- My macOS password for Homebrew / BlackHole install.
- Clicking "Allow" on the microphone permission dialog.
- Zoom setting check: audio output = "Same as System" (Zoom's default).

## Notes
- Expected speed: large-v3 on Apple Silicon ≈ 2–4× realtime (1 h meeting ≈ 15–30 min).
- Recommend headphones during calls: with speakers, the mic also picks up room echo of the far end (still transcribes, just messier).
- Volume keys don't work while a multi-output device is active — that's expected macOS behavior, note it in the README.
