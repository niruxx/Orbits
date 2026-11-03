#!/bin/sh
# Install Orbits for the current user (runs from this source tree).
#   ./install.sh            install launcher, desktop entry and icon into ~/.local
#   ./install.sh --uninstall
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
ID=io.github.niruxx.Orbits

if [ "$1" = "--uninstall" ]; then
    rm -f "$BIN/orbits" "$APPS/$ID.desktop" "$ICONS/$ID.svg"
    echo "Orbits uninstalled (your wallpapers, fonts and settings were left in place)."
    exit 0
fi

missing=""
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' 2>/dev/null || missing="$missing python-gobject/gtk4/libadwaita"
command -v pkexec >/dev/null || missing="$missing polkit(pkexec)"
command -v glib-compile-resources >/dev/null || missing="$missing glib2(glib-compile-resources)"
command -v fc-cache >/dev/null || missing="$missing fontconfig"
[ -n "$missing" ] && echo "Warning: missing dependencies:$missing"

mkdir -p "$BIN" "$APPS" "$ICONS"
ln -sf "$SRC/orbits-run" "$BIN/orbits"
sed "s|@EXEC@|$SRC/orbits-run|" "$SRC/data/$ID.desktop.in" > "$APPS/$ID.desktop"
cp "$SRC/data/icons/hicolor/scalable/apps/$ID.svg" "$ICONS/"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "${ICONS%/scalable/apps}" 2>/dev/null || true
echo "Orbits installed. Launch it from your app grid or run: orbits"
