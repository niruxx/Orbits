# Orbits

A modern GTK4 / libadwaita app for GNOME that lets you:

- **Discover wallpapers** from the **KDE Store** (the same catalogue Plasma's "Get New Wallpapers" uses),
  **Wallhaven**, **Bing Daily**, **NASA APOD**, **Openverse**, and authenticated **Reddit** communities.
  Search, filter by category, hide installed results, and filter known resolutions to fit your screen.
  Wallhaven also has a color filter in Preferences → Sources.
- **Manage a wallpaper library.** Install and uninstall with one click. Plasma wallpaper packages
  (`.tar.xz`/`.zip`) are unpacked, and their light/dark variants are detected and applied as GNOME's
  light/dark wallpaper pair. You can also import local images or drag them in. Installed wallpapers
  appear in GNOME Settings → Appearance too. Mark favorites, assign collections, and find exact
  duplicate files without automatically deleting anything.
- **Wallpaper Tools** (Library header) schedules rotation from collections, Bing Daily or a store
  search using a systemd user timer. Daily wallpapers use GNOME XML with configurable frame times;
  optional HEIC import extracts frames and assigns evenly spaced daily times. Apple solar metadata
  is not interpreted. The monitor tool composes images into a spanned background, with editable
  logical display positions for mixed-DPI layouts. Library wallpaper details also offer crop, blur,
  dim and tint controls that save a new copy before applying.
- **Discover and manage fonts** from **Google Fonts** (2,000+ families, previewed live in their own
  typeface) and **Nerd Fonts**. Apply a font as your interface, document or monospace font, or to the
  login screen. You can install a font for just you or for all users. My Fonts also imports local
  TTF/OTF/ZIP files by chooser or drag and drop, organizes collections, enables/disables fonts for
  your user, compares pairs, and shows glyphs, licenses, OpenType features and variable axes.
- **Manage themes**: GTK3/4, GTK2, GTK1, GNOME Shell, icon, cursor, sound and Plymouth themes,
  plus terminal color schemes. Store detail dialogs show OCS ratings, release notes and paginated comments.
  **Browse Source** opens the theme catalogue; **Open Source** in details or an installed theme’s
  menu opens its original page. Installed GRUB/rEFInd themes also have a **Source** button, which
  opens the GitHub repository for rEFInd themes downloaded from GitHub. Links appear when the
  origin is known; local archives without source metadata do not get an invented URL.
- **Bootloader themes**: detect GRUB or rEFInd, browse themes, import local archives, apply, remove,
  and restore the previous look. System changes require authentication; GRUB settings are backed up
  and its menu is regenerated when applying or resetting a theme.
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
  applied, Orbits first saves a "before" snapshot you can restore. Choose which components to apply,
  optionally capture a screenshot, schedule day/night or weekday setups, and browse shared Looks
  catalogues. Exported XML wallpapers include their frame assets.
- **App Colors** recolor libadwaita apps using a preset (Nord, Catppuccin, Gruvbox, Dracula,
  Tokyo Night, …) or your own palette, with a live preview and separate light and dark versions. It
  can generate a light/dark palette from your wallpaper and match the nearest GNOME accent.
  Import Gradience or Orbits JSON presets into a local gallery, export selected color files for
  Kitty, Alacritty, Ptyxis, VS Code and Firefox, or send the accent/text colors to the login editor.
  Apply in the login editor to authenticate and save system changes. App Colors
  works by overriding libadwaita's color variables in `~/.config/gtk-4.0/gtk.css`, so apps keep
  their normal look otherwise. It can also apply to GTK3 (adw-gtk3) and Flatpak apps.
- **Updates** checks store-installed wallpapers, themes and fonts once a day, and updates each one
  in place using the same download as before.
- **System Check** (main menu) lists which features work on this machine.
- **Fully customize the GDM login screen**, with a live preview of both the login screen and the lock screen:
  - Background: an image (with blur and dim), a solid color or a gradient; separate light/dark
    images and per-monitor composition using the saved display layout
  - Accent color, light/dark style, text color, font, icon theme, cursor theme and size, text scaling
  - Top-bar clock options, panel opacity, and a custom lock-screen clock font, size and weight
  - A real nested greeter preview before applying, using GNOME Shell Devkit and disposable settings
  - Optional automatic theme reapplication after system updates (pacman, apt, and systemd for DNF/RPM)
  - Logo, welcome banner, hiding the user list or power buttons, and authentication methods
  - Automatic login, Wayland on/off, touchpad, power-button and idle actions, night light, accessibility
- A **welcome tour** on first launch and a **Preferences** window. Preferences covers the download
  location (it can move your existing wallpapers), GNOME Settings integration, source options such as
  Wallhaven's minimum resolution and API key, the app's style, and the thumbnail cache.

## Search, downloads and quick switching

**Ctrl+K** searches installed wallpapers, themes, fonts and saved Looks. Results open the
matching library item or installed-theme card. Store discovery keeps its own **Ctrl+F** search.
GNOME Shell also exposes saved Looks through a SearchProvider2 service: activate a Look in
the overview to apply its desktop components without opening the main window. Login-screen
settings still require authenticated Apply in the editor. The provider only resolves local Look IDs.

**Downloads** shows queued and active transfers. Pause and Resume continue the original installation;
Cancel stops it and retains partial data for the next Install attempt, including after restarting
Orbits. Servers must supply a valid range and unchanged validator for resume, or the transfer
restarts. OCS MD5 checksums are checked before extraction. Cached downloads without a checksum
are revalidated online so upstream updates are not hidden. **Clear Finished** removes completed
archive cache and finished rows while preserving partial transfers.

Enable **Preferences → General → Offline Mode** to browse previously cached catalogue queries and
installed items. It pauses active transfers. Cached previews and completed downloads remain usable;
uncached requests explain that a connection is needed. Network failures also fall back to cached
catalogue responses when available. Authentication errors are reported rather than hidden by cache.

Apply and uninstall operations offer **Undo**. Deleted user files are retained in temporary snapshots
for the session; up to twenty recent records are kept. Privileged changes use up to ten root-owned
snapshots in `/var/lib/orbits/undo` and require authentication again to undo. Background Look applies
expose Undo through a desktop notification for thirty seconds. Undo does not fetch deleted files
from a remote store. Settings changed after an operation may be replaced when that operation is undone.

Open **Quick Switcher** from the menu, the launcher action, **Ctrl+Shift+K**, or
`orbits --quick-switcher`. Its small window applies Looks, toggles wallpaper rotation and advances
to the next wallpaper. It uses a window rather than depending on a tray implementation.

## Desktop support and translations

Cinnamon uses its native appearance and background settings, Budgie uses shared GNOME desktop
settings, and XFCE uses `xfconf-query` for GTK themes, icons, cursors, fonts and monitor wallpapers.
Install `xfconf` for XFCE support. XFCE monitor properties must first be initialized in Desktop
Settings. Separate dark and lock-screen wallpapers are desktop-dependent; independent lock-screen
application is currently limited to GNOME/Budgie. Shell themes require a GNOME Shell session.
Cinnamon font keys absent from its own schema fall back to shared GNOME application settings.

The UI now uses gettext, with an initial Spanish catalogue and English fallback. See
[po/README.md](po/README.md) for translation instructions. Icon buttons have descriptive tooltips;
search results and wallpaper, font, theme and Look grids support keyboard activation. Live screen
reader behavior and desktop-specific settings still need testing on real installations.

Meson, a complete GPLv3 license, deterministic release archives, checksum-pinned AUR recipe
generation, and Debian/Fedora packaging files are included. See
[packaging/README.md](packaging/README.md) for build and staging commands. Local builds do not
publish a release or submit an AUR package.

## Requirements

GNOME 47+ (tested on GNOME Shell 51), Python 3.11+, PyGObject, GTK ≥ 4.14, libadwaita ≥ 1.7,
polkit (`pkexec`), `glib-compile-resources`, fontconfig, Pillow, FontTools and GDM.
Wallpaper rotation requires systemd user services; HEIC import optionally requires pillow-heif.

Arch: `sudo pacman -S python-gobject gtk4 libadwaita polkit glib2 fontconfig python-pillow python-fonttools`

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

The background image is pre-rendered (blur and dim, JPEG bounded to 8192 pixels per side) and embedded in the theme, so the
`gdm` user never needs access to your home folder. The helper only reads files you own, and it
validates every dconf key and config value it writes.

In **Login Screen → Background**, the main image is the fallback for light/dark images and empty
monitor assignments. **Configure…** captures the connected displays' logical positions. Mirrored
outputs share one image. GDM must use the same display layout; apply again after changing it.
Light/dark images follow GDM's chosen appearance, rather than a time schedule. The user-session
lock-screen wallpaper remains managed by GNOME. Shared Looks bundle the global light/dark login
images; monitor layouts and automatic update policy stay local.

**Preview Greeter** opens `dbus-run-session -- gnome-shell --wayland --devkit --mode=gdm` as your
user, with a temporary theme, read-only settings database, isolated XDG directories and runtime
state. It requires GNOME Shell with Devkit support and `dconf`. Authentication is unavailable;
close the preview using **Close Preview**. Previewing does not apply system settings.

Enable **System → Reapply Theme After Updates** and press **Apply** to install update integration.
The authenticated helper saves the applied CSS and images under `/var/lib/orbits/gdm` and installs
a root-owned, self-contained runner under `/usr/local/libexec`. Pacman and apt use package hooks;
a systemd path unit watches theme resources for DNF/RPM and other updates. Reapplication rebuilds
from the latest distribution theme without changing login methods or automatic login. Turning the
option off and applying, resetting the login screen, or uninstalling the Arch package removes the
hooks, watcher and saved theme. These integrations still need verification on real distributions.

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


## Wallpaper sources and color exports

NASA uses the shared `DEMO_KEY` unless you add your own key in Preferences → Sources. Reddit needs
an OAuth access token (and API access); renew expired tokens there. Openverse shows each image's
license, attribution and source link in its detail view. API-backed sources may rate-limit requests.
Unsplash and Pexels integration remains pending provider permission: their
[Unsplash guidelines](https://help.unsplash.com/en/articles/2511245-unsplash-api-guidelines) and
[Pexels guidelines](https://www.pexels.com/api/documentation/) exclude wallpaper applications.

Color exports go to the folder you choose, with a separate folder for each enabled target:

- Kitty: include `kitty/orbits.conf` from your Kitty configuration.
- Alacritty: import `alacritty/orbits.toml` in your Alacritty configuration.
- Ptyxis: import `ptyxis/orbits.palette` using Ptyxis's palette importer.
- VS Code: use `vscode/orbits-color-theme.json` as the theme JSON in a color theme extension.
- Firefox: copy `firefox/userChrome.css` into your profile's `chrome` folder and enable
  `toolkit.legacyUserProfileCustomizations.stylesheets` in `about:config`, then restart Firefox.

Exports do not modify existing application configurations. GNOME Console does not offer an
arbitrary custom terminal-palette target here; its GTK interface still uses App Colors.
The community palette browser opens the upstream Gradience preset collection; downloaded JSON
files are imported into Orbits locally. No remote palette is applied automatically.

Backend tests that do not require a GNOME session can be run with:

```sh
python3 -m unittest tests.test_wallpaper_tools tests.test_wallpaper_state
```


## Additional theme types and checks

Sound Themes sets GNOME's event-sound theme and enables event sounds. GTK2 and GTK1 themes use
separate managed blocks in `~/.gtkrc-2.0` and `~/.gtkrc`; Orbits backs up existing files before
changing them. Choosing the default removes the managed block and retains your other settings.
Some legacy themes require GTK engines installed through your distribution.

Terminal Colors browses Konsole's OCS catalogue and accepts archives or individual `.colorscheme`,
`.palette`, `.conf`, `.toml`, `.itermcolors`, and terminal.sexy/Orbits `.json` files. Valid palettes
are normalized as data, with all 16 ANSI colors retained. **Export** writes `orbits.conf` (Kitty),
`orbits.toml` (Alacritty), `orbits.palette` (Ptyxis) and `terminal.json` into a folder you choose.
Import the appropriate file in your terminal; Orbits does not execute theme installation scripts.
Ptyxis schemes with both styles currently import their dark variant.

Installed GTK themes offer **Theme Health Check** in their menu. Checks also run before linking
GTK4 stylesheets into the libadwaita configuration. They inspect missing/external CSS imports,
[removed GTK3 widget style properties](https://docs.gtk.org/gtk4/migrating-3to4.html), and
[libadwaita color-variable coverage](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1-latest/css-variables.html).
Older widget/color patterns produce labeled heuristic warnings; a clean report is not a rendering
guarantee. Authors can declare tested version bounds in their theme's `index.theme`:

```ini
[Orbits]
GTKMin=4.16
GTKMax=4.20
LibadwaitaMin=1.6
LibadwaitaMax=1.8
```

Boot Splash offers **Preview Boot Splash** in each theme menu and **Preview First** before applying.
Preview requires bubblewrap, Plymouth's X11 renderer (`plymouth-x11` on Debian/Ubuntu; Plymouth
with GTK3 support on Arch), a local X11/XWayland display, and a readable Xauthority file. It asks
for administrator authentication, displays the theme for 10 seconds, and exits automatically.
The preview uses private PID/network namespaces, private `/run` and configuration, a read-only
system view, and no physical console/DRM devices. It does not change the selected theme or rebuild
initramfs. If namespaces or X11 rendering are unavailable, it reports an error without a console fallback.

OCS details load independently of installation: score out of 100, supplied version/changelog, and
paginated comments/replies. Empty release notes and unavailable comment services have explicit states;
failed requests can be retried. Remote markup is displayed as plain text. These read endpoints follow
the [OCS specification](https://wiki.freedesktop.org/www/Specifications/open-collaboration-services/).

Theme backend regressions run without GTK or administrator access:

```sh
python3 -m unittest tests.test_theme_features
```


## Font management

**My Fonts → Import** accepts `.ttf`, `.otf`, `.ttc`, `.otc`, and ZIP archives, including drag and
drop. Files are inspected with FontTools before installation; ZIP paths are flattened, only fonts
and license files are copied, and installation scripts are ignored. Adjacent license files are
preserved for individual-file imports. Reimporting identical font data retains its collections
and enabled state.

Each managed font has a user-level enable switch, **Collections**, and **Font Details & Glyphs**.
Details include searchable/paginated Unicode coverage, embedded license text, OpenType feature
preview controls, and sliders for the variable axes actually present in the font. Axis selections
can be applied to interface, document or monospace roles. TTC/OTC inspection currently shows the
first face. Fontconfig rules hide disabled files, including an installed system copy, for your
user without deleting the files or disabling fonts for other users. Selecting a disabled font for
a desktop role enables it again.

**Compare** previews two families side by side and offers suggested heading/body pairs. Missing
families are labeled and use fallback until installed. The preferences button manages emoji
fallback, per-family hinting, and family substitutions using a dedicated
`~/.config/fontconfig/conf.d/99-orbits.conf`; an existing file at that path is backed up once.
Existing unrelated fontconfig files are kept. Restart applications to refresh cached font choices.
These preferences affect applications that use fontconfig; sandboxed apps may have their own
font configuration.

## Looks, schedules and community catalogues

**Apply** lets you select wallpaper, lock screen, themes, fonts/text scaling, app colors/GTK4/accent,
and an optional login-screen draft. Only selected groups are changed or have missing store assets
downloaded. Login settings still go through the login editor's authenticated Apply action.

When saving a Look, enable **Capture Desktop Screenshot** to request a screenshot through the
[desktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Screenshot.html).
Your desktop controls consent. If capture is cancelled or unavailable, the Look is saved without
an image and the reason is shown. Captured screenshots are copied into the Look folder, displayed
on its card, and included when exporting. XML daily wallpaper frames are also copied/bundled, so
Looks keep working after the original Library wallpaper is removed and on another machine.

The schedule button sets day/night start times or a Look for each weekday. **No Change** leaves
that time/day alone. Systemd user services check the schedule once a minute and apply a changed
selection while Orbits is closed. Times follow the desktop's local timezone. Schedules apply
desktop settings only; they never apply GDM changes. Each change has a history snapshot. Failed
applications are reported in the user-service journal and retried after 15 minutes.

**Community** accepts a local `index.json` or an HTTPS catalogue URL. It previews screenshots and
imports checksum-verified `.orbits` files without applying them. No central Orbits gallery or
publishing account is configured. **Share Gallery Bundle** exports the Look, optional screenshot,
and an `index.json` that you can share as a folder or host on an HTTPS static server. The format is:

```json
{
  "format": 1,
  "looks": [{
    "id": "my-look",
    "name": "My Look",
    "author": "Your name",
    "description": "Optional description",
    "download": "my-look.orbits",
    "screenshot": "my-look.png",
    "sha256": "REPLACE_WITH_THE_64_CHARACTER_SHA256_OF_THE_ORBITS_FILE"
  }]
}
```

Asset URLs can be relative to the manifest. Screenshots are optional. Every archive needs its
SHA-256 checksum; imports reject unsafe paths and oversized archives. Sharing does not upload or
publish anything automatically. Store-installed themes/fonts are referenced for download; local
fonts are not bundled, so recipients may need to install them separately with the proper license.

The GTK-independent regression suites can be run with Pillow and FontTools installed:

```sh
python3 -m unittest tests.test_fonts_looks tests.test_theme_features tests.test_wallpaper_tools tests.test_wallpaper_state
```
