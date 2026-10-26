#!/usr/bin/env bash
# Installs or updates Nothing Ear Control in Decky Loader (Steam Deck, CachyOS, Bazzite, other Linux).
# Usage: curl -fsSL https://raw.githubusercontent.com/JayPrates/nothing-ear-control-decky/main/install.sh | bash
set -euo pipefail

REPO="JayPrates/nothing-ear-control-decky"
PLUGINS="${DECKY_PLUGINS_DIR:-$HOME/homebrew/plugins}"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (it asks for sudo itself), not with sudo." >&2
  exit 1
fi
if [ ! -d "$PLUGINS" ]; then
  echo "Decky Loader isn't installed ($PLUGINS not found). Install it first: https://decky.xyz" >&2
  exit 1
fi
if ! command -v bluetoothctl >/dev/null 2>&1; then
  echo "Note: bluetoothctl is missing; install it (CachyOS/Arch: sudo pacman -S bluez-utils)." >&2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "Downloading the latest release..."
curl -fsSL "https://github.com/$REPO/releases/latest/download/NothingEarControl.zip" -o "$tmp/plugin.zip"
if command -v bsdtar >/dev/null 2>&1; then
  bsdtar -xf "$tmp/plugin.zip" -C "$tmp"
else
  unzip -q "$tmp/plugin.zip" -d "$tmp"
fi

echo "Installing to $PLUGINS/NothingEarControl (needs sudo)..."
sudo rm -rf "$PLUGINS/NothingEarControl"
sudo cp -r "$tmp/NothingEarControl" "$PLUGINS/"
sudo systemctl restart plugin_loader
echo "Done. Open the Quick Access menu (...) -> Decky -> Ear Control."
