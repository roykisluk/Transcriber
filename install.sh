#!/usr/bin/env zsh
# Idempotent installer for the local Hebrew transcription pipeline.
# Fresh machine:  git clone <repo> && cd <repo> && ./install.sh
set -euo pipefail

REPO=${0:A:h}
BIN="$HOME/bin"
MODEL="$REPO/ggml-model-whisper-custom.bin"
MODEL_URL="https://huggingface.co/ivrit-ai/whisper-large-v3-ggml/resolve/main/ggml-model.bin"
MODEL_SIZE=3095033483
VAD_MODEL="$REPO/ggml-silero-v5.1.2.bin"
VAD_MODEL_URL="https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
VAD_MODEL_SIZE=885098

# 1. Homebrew
if ! command -v brew >/dev/null 2>&1; then
  print "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || brew shellenv)"

# 2. Formulae
for f in whisper-cpp ffmpeg switchaudio-osx mpv; do
  if brew list --formula "$f" >/dev/null 2>&1; then
    print "$f: already installed"
  else
    brew install "$f"
  fi
done

# 3. BlackHole (cask; needs the macOS password, so only attempt with a terminal)
if brew list --cask blackhole-2ch >/dev/null 2>&1; then
  print "blackhole-2ch: already installed"
elif [[ -t 0 ]]; then
  print "Installing blackhole-2ch (will prompt for your macOS password)..."
  brew install --cask blackhole-2ch
else
  print "blackhole-2ch: not installed and no interactive terminal for the password prompt."
  print "  Run ./install.sh in a terminal to finish installing it."
fi

# 4. Model: verify size, download only if missing/corrupt
if [[ -f $MODEL && $(stat -f%z "$MODEL") -eq $MODEL_SIZE ]]; then
  print "model: OK ($MODEL_SIZE bytes)"
else
  print "model: downloading (resumable)..."
  curl -L -C - -o "$MODEL" "$MODEL_URL"
  [[ $(stat -f%z "$MODEL") -eq $MODEL_SIZE ]] || { print -u2 "model: size mismatch after download"; exit 1; }
fi

# 4b. Silero VAD model (small; used by whisper.cpp --vad to skip silence)
if [[ -f $VAD_MODEL && $(stat -f%z "$VAD_MODEL") -eq $VAD_MODEL_SIZE ]]; then
  print "vad model: OK ($VAD_MODEL_SIZE bytes)"
else
  print "vad model: downloading..."
  curl -L -o "$VAD_MODEL" "$VAD_MODEL_URL"
  [[ $(stat -f%z "$VAD_MODEL") -eq $VAD_MODEL_SIZE ]] || { print -u2 "vad model: size mismatch after download"; exit 1; }
fi

# 5. Compile the Swift CoreAudio helpers (skip if binary is up to date)
mkdir -p "$BIN"
for tool in meeting-devices meeting-capture; do
  if [[ ! -x $BIN/$tool || $REPO/$tool.swift -nt $BIN/$tool ]]; then
    print "compiling $tool..."
    swiftc -O -framework CoreAudio -framework AudioToolbox \
      -o "$BIN/$tool" "$REPO/$tool.swift"
  else
    print "$tool: up to date"
  fi
done

# 6. Install record & transcribe into ~/bin (symlink -> repo, so the model
#    beside them is found and repo edits take effect immediately)
chmod +x "$REPO/record" "$REPO/transcribe" "$REPO/transcribe-meeting" "$REPO/notion-meeting" "$REPO/lecture-subtitles"
ln -sf "$REPO/record" "$BIN/record"
ln -sf "$REPO/transcribe" "$BIN/transcribe"
ln -sf "$REPO/transcribe-meeting" "$BIN/transcribe-meeting"
ln -sf "$REPO/notion-meeting" "$BIN/notion-meeting"
ln -sf "$REPO/lecture-subtitles" "$BIN/lecture-subtitles"
print "installed: record, transcribe, transcribe-meeting, notion-meeting, lecture-subtitles, meeting-devices, meeting-capture -> $BIN"

# 7. Ensure ~/bin is on PATH (append to ~/.zshrc only if missing)
case ":$PATH:" in
  *":$BIN:"*) print "PATH: ~/bin already present" ;;
  *)
    if grep -Eqs '(^|[^A-Za-z_])PATH="?\$HOME/bin' "$HOME/.zshrc" 2>/dev/null; then
      print "PATH: ~/bin line already in ~/.zshrc (restart your shell)"
    else
      print '\nexport PATH="$HOME/bin:$PATH"' >> "$HOME/.zshrc"
      print "PATH: added ~/bin to ~/.zshrc (restart your shell or: source ~/.zshrc)"
    fi
    ;;
esac

print "install complete."
