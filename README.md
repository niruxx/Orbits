# Orbits

A modern GTK4 / libadwaita app for GNOME that lets you:

- **Discover wallpapers** from the **KDE Store** (the same catalogue Plasma's "Get New Wallpapers" uses),
  **Wallhaven** and **Bing Daily**. You can search, filter by category, and keep scrolling to load more.
- **Manage a wallpaper library.** Install and uninstall with one click. Plasma wallpaper packages
  (`.tar.xz`/`.zip`) are unpacked, and their light/dark variants are detected and applied as GNOME's
  light/dark wallpaper pair. You can also import local images or drag them in. Installed wallpapers
  appear in GNOME Settings → Appearance too.
- **Discover and manage fonts** from **Google Fonts** (2,000+ families, previewed live in their own
  typeface) and **Nerd Fonts**. Apply a font as your interface, document or monospace font, or to the
  login screen. You can install a font for just you or for all users.
- **Fully customize the GDM login screen**, with a live preview of both the login screen and the lock screen:
  - Background: an image (with blur and dim), a solid color or a gradient
  - Accent color, light/dark style, text color, font, icon theme, cursor theme and size, text scaling
  - Top-bar clock options and a custom lock-screen clock size and weight
  - Logo, welcome banner, hiding the user list or power buttons, and authentication methods
  - Automatic login, Wayland on/off, touchpad, power-button and idle actions, night light, accessibility
- A **welcome tour** on first launch and a **Preferences** window. Preferences covers the download
  location (it can move your existing wallpapers), GNOME Settings integration, source options such as
  Wallhaven's minimum resolution and API key, the app's style, and the thumbnail cache.

## Requirements

GNOME 47+ (tested on GNOME Shell 51), Python 3.11+, PyGObject, GTK ≥ 4.14, libadwaita ≥ 1.7,
polkit (`pkexec`), `glib-compile-resources`, fontconfig and GDM.

Arch: `sudo pacman -S python-gobject gtk4 libadwaita polkit glib2 fontconfig`

## Install on Arch Linux

```sh
cd packaging/arch
makepkg -si            # builds orbits-<ver>-1-any.pkg.tar.zst from this tree and installs it
```

The package installs to `/usr/lib/orbits`, ships a polkit action (so the password prompt names Orbits),
and on removal restores the original login-screen theme and removes Orbits' GDM settings.

## Run from source

```sh
./orbits-run          # straight from the source tree
./install.sh          # add "Orbits" to your app grid (~/.local); ./install.sh --uninstall to remove
```

## How the login screen customization works

Changing GDM needs root. Orbits runs a small, self-contained helper
(`orbits/gdm/helper.py`) through `pkexec`, so you're asked for your password each time you press **Apply**.

| What | Where |
|---|---|
| Background & CSS | Rebuilds `/usr/share/gnome-shell/gnome-shell-theme.gresource`. The original is saved first as `gnome-shell-theme.gresource.orbits-backup`. |
| GDM settings | `/etc/dconf/db/gdm.d/99-orbits` (plus `dconf update`) |
| Logo | `/usr/share/orbits/gdm-logo.*` |
| Auto-login / Wayland | `[daemon]` section of `/etc/gdm/custom.conf`. Comments and other sections are preserved. |
| System-wide fonts | `/usr/share/fonts/orbits/<font>/` |

- The rebuilt theme carries a marker file, so Orbits always works from a pristine copy and never
  stacks changes on top of each other.
- When a `gnome-shell` package update replaces the theme, Orbits notices and offers to re-apply your
  design.
- **⋮ → Reset Everything to Defaults** restores the original theme and removes everything Orbits added.
- Changes show up the next time you log out.

The background image is pre-rendered (blur and dim, up to 4K JPEG) and embedded in the theme, so the
`gdm` user never needs access to your home folder. The helper only reads files you own, and it
validates every dconf key and config value it writes.

## Your files

| Data | Location |
|---|---|
| Wallpapers | `~/.local/share/backgrounds/orbits/` (changeable in Preferences) |
| Fonts | `~/.local/share/fonts/orbits/` |
| Settings | `~/.config/orbits/` |
| Cache | `~/.cache/orbits/` |
