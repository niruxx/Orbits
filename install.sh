#!/bin/sh
# Install Orbits for the current user (runs from this source tree).
#   ./install.sh            install launcher, desktop entry and icon into ~/.local
#   ./install.sh --uninstall
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
ID=io.github.niruxx.Orbits

if [ "$1" = "--uninstall" ]; then
    rm -f "$BIN/orbits" "$APPS/$ID.desktop" "$ICONS/$ID.svg"
    rm -f "$SHARE/gnome-shell/search-providers/$ID.search-provider.ini" "$SHARE/dbus-1/services/$ID.SearchProvider.service" "$SHARE/locale/es/LC_MESSAGES/orbits.mo"
    echo "Orbits uninstalled (your wallpapers, fonts and settings were left in place)."
    exit 0
fi

missing=""
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' 2>/dev/null || missing="$missing python-gobject/gtk4/libadwaita"
python3 -c 'from PIL import Image' 2>/dev/null || missing="$missing python-pillow"
python3 -c 'from fontTools.ttLib import TTFont' 2>/dev/null || missing="$missing python-fonttools"
command -v pkexec >/dev/null || missing="$missing polkit(pkexec)"
command -v glib-compile-resources >/dev/null || missing="$missing glib2(glib-compile-resources)"
command -v fc-cache >/dev/null || missing="$missing fontconfig"
[ -n "$missing" ] && echo "Warning: missing dependencies:$missing"

mkdir -p "$BIN" "$APPS" "$ICONS"
ln -sf "$SRC/orbits-run" "$BIN/orbits"
python3 - "$SRC" "$APPS/$ID.desktop" <<'PYTHON'
from pathlib import Path
import sys
root, destination = sys.argv[1:]
launcher = root + '/orbits-run'
quoted = '"' + launcher.replace(chr(92), chr(92)*2).replace('"', chr(92)+'"').replace('%', '%%') + '"'
text = (Path(root) / 'data/io.github.niruxx.Orbits.desktop.in').read_text()
Path(destination).write_text(text.replace('@EXEC@', quoted))
PYTHON
cp "$SRC/data/icons/hicolor/scalable/apps/$ID.svg" "$ICONS/"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "${ICONS%/scalable/apps}" 2>/dev/null || true
mkdir -p "$SHARE/gnome-shell/search-providers" "$SHARE/dbus-1/services" "$SHARE/locale/es/LC_MESSAGES"
cp "$SRC/data/$ID.search-provider.ini" "$SHARE/gnome-shell/search-providers/"
# D-Bus Exec is parsed as argv; quote the launcher path for checkouts containing spaces.
python3 - "$SRC" "$BIN/orbits" "$SHARE/dbus-1/services/$ID.SearchProvider.service" <<'PYTHON'
from pathlib import Path
import sys
root, launcher, destination = sys.argv[1:]
quoted = '"' + launcher.replace(chr(92), chr(92)*2).replace('"', chr(92)+'"') + '"'
text = (Path(root) / 'data/io.github.niruxx.Orbits.SearchProvider.service.in').read_text()
Path(destination).write_text(text.replace('@EXEC@', quoted))
PYTHON
if command -v msgfmt >/dev/null; then
    msgfmt "$SRC/po/es.po" -o "$SHARE/locale/es/LC_MESSAGES/orbits.mo"
fi
echo "Orbits installed. Launch it from your app grid or run: orbits"
