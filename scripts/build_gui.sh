#!/usr/bin/env bash
# Build a standalone OptionChain GUI binary with PyInstaller.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Syncing dependencies"
uv sync --extra build

echo "==> Running unit tests"
uv run pytest -q

echo "==> Building GUI binary (this can take a few minutes)"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "OptionChain" \
  --paths . \
  --collect-all customtkinter \
  --collect-all matplotlib \
  --hidden-import optionchain \
  --hidden-import optionchain.gui \
  --hidden-import optionchain.fetcher \
  --hidden-import optionchain.history \
  --hidden-import optionchain.leaders \
  --hidden-import optionchain.compare \
  --hidden-import optionchain.plotting \
  --hidden-import optionchain.watchlist \
  --hidden-import optionchain.metrics \
  --hidden-import optionchain.filters \
  --hidden-import yfinance \
  --hidden-import curl_cffi \
  --hidden-import PIL \
  --hidden-import matplotlib.backends.backend_tkagg \
  --hidden-import matplotlib.backends.backend_agg \
  optionchain/gui.py

echo ""
echo "Done."
if [[ "$(uname)" == "Darwin" ]]; then
  echo "  macOS app:  dist/OptionChain.app"
  echo "  Run:        open dist/OptionChain.app"
else
  echo "  Binary:     dist/OptionChain"
fi
echo "  Or dev mode: uv run optionchain-gui"
