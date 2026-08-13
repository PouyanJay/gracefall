#!/usr/bin/env bash
# Open Ghostty and paint the demo inside it.
#
# `gfl view` has to run *in* a graphics-capable terminal. Running it in the
# terminal you already have shows the fallback and a stderr hint, which is
# correct behaviour and also the most common way to be confused by this.
#
#   scripts/view_in_ghostty.sh [font-size] [kitty]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIZE="${1:-14}"
WHICH="${2:-ghostty}"

# The two disagree on how to be handed a command. Ghostty takes -e, the
# xterm convention. kitty takes the program as plain positional arguments
# and parses -e as something else entirely, which fails with the
# distinctly unhelpful "No directories to watch provided".
if [ "$WHICH" = "kitty" ]; then
  APP="/Applications/kitty.app/Contents/MacOS/kitty"
  LAUNCH=(--override "font_size=$SIZE")
  EXEC_FLAG=()
else
  APP="/Applications/Ghostty.app/Contents/MacOS/ghostty"
  LAUNCH=("--font-size=$SIZE")
  EXEC_FLAG=(-e)
fi

if [ ! -x "$APP" ]; then
  echo "$WHICH is not installed. Run: make dev-terminals" >&2
  exit 1
fi

RUN="$(mktemp -t gflview)"
cat > "$RUN" <<EOF
#!/bin/sh
cd "$ROOT" || exit 1
printf '\033[2J\033[H'
export PATH="\$HOME/.local/bin:/opt/homebrew/bin:\$PATH"
uv run -q --with pillow --with-editable . python -m gracefall --force-osc demo \
  | uv run -q --with pillow --with-editable . python -m gracefall view --stats
printf '\n\nfont size $SIZE. Resize the window or change the font size and\n'
printf 'run it again: the images are sized from the cell metrics, so this\n'
printf 'is the direct test of whether those are being read correctly.\n'
printf '\n[press enter to close] '
read -r _
EOF
chmod +x "$RUN"

echo "opening $WHICH at font size $SIZE"
exec "$APP" "${LAUNCH[@]}" "${EXEC_FLAG[@]}" /bin/sh -c "$RUN"
