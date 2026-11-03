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
- **Manage themes**: GTK, GNOME Shell, icon, cursor and Plymouth boot splash themes.
  - Browse gnome-look.org, or install from a downloaded archive.
  - Archives holding several variants (e.g. Light/Dark) are unpacked, and every variant is installed.
  - Preview themes before applying: icon themes show real icons, cursor themes show their pointers.
  - Apply with one click. GTK themes can also be linked into `~/.config/gtk-4.0` to restyle libadwaita
    apps. Shell themes turn on the User Themes extension for you.
  - Install a theme just for you, or for all users so the login screen can use it.
  - Boot splashes are installed system-wide and set with `plymouth-set-default-theme`. Orbits then
    rebuilds the initramfs (mkinitcpio, dracut or update-initramfs) and warns you if `splash` or
    the `plymouth` hook is missing.
- **Save complete setups as Looks.** A Look holds your wallpaper, themes, fonts, accent color, app colors and
  optionally the login screen. Switch between Looks in one click, or share one as a single `.orbits`
  file; anything missing is downloaded from its store on the other machine. Each time a Look is
  applied, Orbits first saves a "before" snapshot you can restore.
- **App Colors** recolor libadwaita apps using a preset (Nord, Catppuccin, Gruvbox, Dracula,
  Tokyo Night, …) or your own palette, with a live preview and separate light and dark versions. It
  works by overriding libadwaita's color variables in `~/.config/gtk-4.0/gtk.css`, so apps keep
  their normal look otherwise. It can also apply to GTK3 (adw-gtk3) and Flatpak apps.
- **Updates** checks store-installed wallpapers, themes and fonts once a day, and updates each one
  in place using the same download as before.
- **System Check** (main menu) lists which features work on this machine.
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
| Background & CSS | Rebuilds `/usr/share/gnome-shell/gnome-shell-theme.gresource`. The original is saved first as `gnome-shell-theme.gresource.orbits-backup`. On **Debian/Ubuntu**, where GDM's theme is chosen with `update-alternatives` (`gdm-theme.gresource`), Orbits never edits distribution files. It builds `/usr/share/gnome-shell/orbits/gdm-theme.gresource` from your current theme (for example Yaru), registers it as an alternative and selects it. Reset switches back to your previous choice. |
| GDM settings | `/etc/dconf/db/gdm.d/99-orbits` (plus `dconf update`) |
| Logo | `/usr/share/orbits/gdm-logo.*` |
| Auto-login / Wayland | `[daemon]` section of `/etc/gdm/custom.conf`. Comments and other sections are preserved. |
| System-wide fonts | `/usr/share/fonts/orbits/<font>/` |
| System-wide themes | `/usr/share/themes`, `/usr/share/icons`, `/usr/share/plymouth/themes`. Each gets an `.orbits.json` marker, and Orbits only ever removes folders that carry one. |
| Boot splash | `plymouth-set-default-theme <name>`, then the initramfs is rebuilt |

- The rebuilt theme carries a marker file, so Orbits always works from a pristine copy and never
  stacks changes on top of each other.
- When a `gnome-shell` package update replaces the theme, Orbits notices and offers to re-apply your
  design.
- **⋮ → Reset Everything to Defaults** restores the original theme and removes everything Orbits added.
- Changes show up the next time you log out.

On SELinux systems (Fedora), everything the helper writes is relabelled with `restorecon`. On
immutable systems with a read-only `/usr` (Silverblue, NixOS), the system-wide features say clearly
that they're unavailable instead of failing.

The background image is pre-rendered (blur and dim, up to 4K JPEG) and embedded in the theme, so the
`gdm` user never needs access to your home folder. The helper only reads files you own, and it
validates every dconf key and config value it writes.

## Your files

| Data | Location |
|---|---|
| Wallpapers | `~/.local/share/backgrounds/orbits/` (changeable in Preferences) |
| Fonts | `~/.local/share/fonts/orbits/` |
| Themes | `~/.local/share/themes/`, `~/.local/share/icons/` |
| Libadwaita theme links / App Colors | `~/.config/gtk-4.0/` (real files there are moved to `orbits-backup/` first) |
| Looks | `~/.local/share/orbits/looks/` (snapshots in `looks-history/`) |
| Settings | `~/.config/orbits/` |
| Cache | `~/.cache/orbits/` |

## Tests

```sh
tests/run.sh        # unittest suite in an isolated XDG environment; no root needed
```

The suite covers the root helper (theme rebuild and restore, update-alternatives mode against a fake
`update-alternatives`, `custom.conf` editing, path and symlink checks). It also covers App Colors, the
validation of imported Looks, update detection and theme detection.
