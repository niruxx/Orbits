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

Appearance applies and wallpaper/font uninstall operations offer **Undo**. Deleted user files are
retained in temporary snapshots for the session; up to twenty recent records are kept. Privileged
changes use up to ten root-owned snapshots in `/var/lib/orbits/undo` and require authentication
again to undo. Background Look applies expose Undo through a desktop notification for thirty
seconds. Undo does not fetch deleted files from a remote store. Settings changed after an
operation may be replaced when that operation is undone.

The GTK, Shell, Icons and Cursors pages offer **Restore Default Theme**. This restores the
configured desktop default for that theme type. GTK reset also removes Orbits' libadwaita link
override, restoring any pre-existing configuration. Installed themes remain available; restart
open applications to refresh their styling.

On **Bootloader → Installed**, choose **rEFInd Default Theme → Use Default** or
**Restore Default Theme** to return to rEFInd's built-in appearance. Authentication is required.
Installed themes and unrelated boot entries are retained. If an included configuration mixes
appearance with boot entries or other settings, Orbits asks for manual review instead of disabling it.

Theme uninstall is permanent: it removes the selected installed theme files, tracked unshared
download/preview caches, matching staging files and retained theme Undo copies. Shared caches
remain until the last installed variant is removed. Older archive caches without ownership
records can be cleared through **Downloads → Clear Finished**.

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

Arch: `sudo pacman -S bubblewrap python-gobject gtk4 libadwaita polkit glib2 fontconfig python-pillow python-fonttools`

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

### Security and malware prevention

Downloaded appearance assets can contain more than pictures: archives, fonts and themes
are parsed by software, and some theme packages include executable installers. Orbits
uses several independent checks to reduce the risk of malicious downloads, unsafe file
layouts and parser vulnerabilities. The built-in validation and sandbox protections work
independently of the optional malware scanner.

| Protection | What it does | Benefit |
|---|---|---|
| Local malware scanning (optional) | Checks downloads, updates, previews and supported imports with ClamAV before use; scans cached downloads again | Detects known threats without uploading your files to a scanning service |
| Virus definition updates (optional) | Uses a private, unprivileged database with automatic daily checks or a manual update action | Keeps detection data current without changing the system ClamAV service |
| Rejection alerts and cleanup | Explains scanner detections and deletes Orbits-owned rejected download/cache/temporary copies; reports deletion failures | Makes blocked operations understandable and removes rejected copies from Orbits' download workflow |
| Sandboxed parsing and previews | Processes images, archives and font inspection/previews in restricted workers with resource limits | Limits access to your files and network if a parser encounters hostile input, and bounds resource use |
| Archive prevalidation and bounded extraction | Rejects unsafe paths, links, special files and oversized content; counts actual extracted bytes | Helps prevent archive traversal, writes outside the extraction directory and decompression bombs |
| Data-only theme installation | Rejects executable assets and script-only installers, including downloaded scripted Plymouth themes | Installs supported theme assets without running a downloaded installation script |
| Protected HTTPS requests | Checks certificates, public destination addresses and redirects; pins the address used for the connection | Reduces interception and attempts to make a catalogue contact local or private-network services |
| Publisher verification (optional) | Verifies Minisign signatures against keys you explicitly trust; strict mode requires verified publishers for new installs | Checks that the downloaded bytes were signed by a configured publisher |
| Installed-file integrity | Records SHA-256 manifests and offers explicit integrity checks | Reveals changes to managed files after their baseline was recorded |
| Privileged staging checks | Copies and revalidates assets before replacing system files, without running third-party cache parsers as root | Reduces exposure during system-wide installs and keeps the previous installation when staging fails |

**Enable malware protection:** install ClamAV (`clamscan` and `freshclam`), open
Preferences → General → Downloads, enable **Scan Downloads for Malware**, then use
**Update Virus Definitions Now** or enable **Automatically Update Virus Definitions**.
Both switches are off by default. **Malware Protection Status** shows missing tools,
scanning state and definition information. If scanning is requested but cannot complete,
Orbits blocks the operation rather than treating the file as clean. Disabling scanning
leaves the other validation and sandbox checks in place.

These protections reduce risk; they do not certify that a theme or font is harmless.
ClamAV can miss new threats, publisher signatures establish who signed bytes rather than
whether they are safe, and local integrity records are not publisher authentication.
Applying an asset also exposes it to the desktop or bootloader that uses it, outside
Orbits' worker isolation. Keep those components and virus definitions updated.

#### Malware scanning, rejection and cleanup

Preferences → General → Downloads → **Scan Downloads for Malware** enables local
ClamAV scanning for all files fetched through the installation download manager,
including wallpaper, theme and font updates. Install `clamscan` (usually the
`clamav` package) and install `freshclam` for definition updates. The scanning switch can be enabled or
disabled independently of **Automatically Update Virus Definitions**. Automatic
updates run before scans, at most once per day; a failed update blocks that scan.
**Update Virus Definitions Now** runs an update immediately and displays its result.
Both update paths use a private database in Orbits' user data directory without
administrator access. The first download can take several minutes and substantial
disk space. Orbits does not install ClamAV for you.

With automatic updates disabled, scans use existing Orbits definitions when
available, otherwise the system database. Offline Mode skips automatic updates and
blocks manual updates; scans can still use installed definitions. Turning off
Orbits updates does not stop system-wide freshclam services. Turning off scanning
does not run automatic updates; the manual update button remains available. Files are
not uploaded to a third-party scanning service.

When enabled, a missing scanner, scanner error, timeout, detection, encrypted
content or exceeded scanner limits blocks installation. The Downloads page shows
scan results. Detections also show a persistent alert with the scanner reason.
Orbits deletes its rejected payload, partial download, cache metadata and temporary
working copies before reporting the rejection. Cleanup failures list paths that
need manual removal. User-selected originals and existing installations are kept.
Cached downloads are scanned again on reuse. Scans may take up to
three minutes; cancellation prevents delivery but a running scan finishes or times
out in the background. “No known malware detected” is not a guarantee of safety.

#### File validation and isolated previews

Installation downloads are limited to 512 MiB. Recognized ZIP/tar archives are
checked before delivery: at most 10,000 entries and 1 GiB of declared expanded
content; links, special files and encrypted ZIPs are rejected. Downloaded wallpaper
images must fully decode as a supported single-frame raster image, with at most
40 megapixels. SVG wallpaper downloads are rejected. These checks do not execute
wallpaper QML or installation scripts.

Wallpaper installation, browsing images, local thumbnails, imported wallpaper
images and image assets inside Looks use a separate bubblewrap decoder with no
network, restricted filesystem mounts, a 1 GiB address-space limit, CPU and output
limits, and a 40-second wall timeout. Only validated raw pixels leave that worker;
Orbits constructs a fresh PNG without original metadata. Bubblewrap and working
unprivileged namespaces are required. If unavailable, these image operations fail
closed; there is no unsandboxed fallback. SVG is currently unsupported.

Remote previews are limited to 32 MiB and 2048 pixels per side after conversion.
Cached originals also pass through checks before display. The scanning switch
also covers these previews, local font/theme/boot-theme imports, wallpaper imports
and imported Looks. Local assets are copied into bounded private snapshots before
scanning so the importer consumes the bytes checked. HEIC imports preserve up to
200 frames, bounded by a 512 MiB aggregate raw-pixel limit; other imported and
downloaded wallpapers must be single-frame images.

**Scan Existing Appearance Assets** in Preferences explicitly scans current user
wallpapers, Orbits fonts, user theme directories and Looks, even when automatic
scanning is disabled. It reports errors/detections without deleting or rewriting
installed files. System assets and symlink targets are excluded. Existing files
are not automatically converted; unsafe existing assets should be removed or
reimported through the protected path.

Orbits font inspection and explicit font previews are sandboxed; the desktop’s theme and font engines remain outside this isolation. Applying
an existing theme or font still invokes those components. Keep their libraries
and ClamAV signatures updated. Signatures supplied alongside an upload establish
integrity, not publisher trust or harmlessness.

#### Network, archive and system-install protections

Remote catalogue, preview and installation requests require HTTPS, validate
all resolved addresses as public, and pin the selected numeric address for the TLS
connection while retaining hostname/certificate checks. Redirects are limited to
five and cannot downgrade to HTTP or redirect into local/private networks. Cross
origin redirects drop authorization and cookie headers. These protected requests
do not use environment proxies. Local files are accepted only by explicit import
actions; a remote catalogue cannot request a `file:` download. HTTP-only sources,
private-network hosts and proxy-only connections are unsupported by this policy.

All wallpaper, font, theme and Look extraction uses the same streaming limits:
512 MiB per member, 1 GiB total output, 10,000 entries and 20 path components.
Actual bytes are counted while writing. Traversal, duplicate/conflicting paths,
links, special files and encrypted ZIP entries are rejected; archive permission
bits are not preserved.

Fonts receive structural SFNT/collection table checks before installation. Theme
assets have an allowlist, executable-content/signature checks, bounded text and
XML checks, and restrictions on external stylesheet/SVG references and launchable
metadata. Shell scripts, native binaries, QML and JavaScript are blocked. Plymouth's
own `.script` format and script modules are blocked for downloaded themes because
the real boot runtime cannot sandbox them. Existing Orbits-managed scripted
Plymouth themes cannot be applied until replaced; distribution-managed themes
remain available. These conservative rules can reject previously accepted theme packages.

The privileged helper snapshots source trees using directory descriptors,
rejects links/hardlinks, unsafe ownership/permissions and special files, and
revalidates its staged copy before replacing fonts or themes. Staging failures
leave the previous installation in place. This does not establish publisher trust
or prove the desktop's font/theme parsers are vulnerability-free.

New managed wallpapers, fonts, themes and Looks record SHA-256 manifests. Orbits
checks them before applicable appearance changes. **Check Installed File Integrity**
in Preferences finds changed, added or deleted files without replacing their
baseline. Older assets without a baseline are reported as unverified. These are
local change records, not signed attestations; a process with access to the same
account can also modify its user-owned records. Root-installed records retain the
system installation's permissions.

**Malware Protection Status** in Preferences shows scanning state, missing tools,
the last successful Orbits definition check and private daily-database file age.
A file older than seven days is flagged for an update. File age is not a guarantee
of current signature contents; system-managed database age is labeled unknown
and scanner version/database information is displayed when available.

### Parser isolation, publishers and older assets

Archive probing and ZIP/tar extraction run in a separate bubblewrap process with
memory, CPU, file-size and time limits. Orbits independently checks returned paths,
file types, links, byte totals and counts before accepting the output. FontTools
metadata inspection and FreeType sample rendering also run in restricted workers.
Preview fonts are never registered in the GUI's Pango font map. Font samples are
fresh PNGs reconstructed from bounded raw pixels; glyph-map button labels use the
system font. System font discovery uses Fontconfig; installing or applying a font
still exposes it to the desktop's font engine. These processes cannot be isolated
by Orbits on behalf of every other application.

**Trusted Publishers** in Preferences lets users pin a publisher hostname, display
name and Minisign public key. Obtain keys through an independently authenticated
channel; no key from a download is trusted automatically. For a configured host,
Orbits requires a valid prehashed Minisign signature at the original download URL
with `.minisig` appended to its path. Redirects do not change which host's keys are
trusted. Signature verification runs on every use, including cached payloads;
offline verification needs the cached signature for those exact bytes. Invalid or
missing signatures for a pinned host block installation. Install `minisign` to
use this feature.

**Require Verified Publishers for New Installs** additionally rejects sources
without a configured key. This is optional because most community stores do not
publish signatures. Explicit local imports in this mode need a `.minisig` sidecar
signed by one of the configured keys. Strict mode does not retroactively identify
publishers of installed assets, verify unsigned browsing thumbnails, or establish
that a signed asset is harmless. A valid old signature alone does not establish
that a release is the newest version; this is not a transparency or anti-rollback
service.

**Validate Older Managed Assets** checks supported user assets and creates an
integrity baseline only after successful validation. Existing manifests are
verified and never silently replaced. Invalid or external Look references and
scripted themes are rejected; reimport these assets through the normal protected
path. This action records local validation, not publisher authentication, and does
not rewrite original legacy image formats.

Run `python3 -m unittest tests.test_live_security` for live integration checks.
Set `ORBITS_REQUIRE_LIVE_SECURITY=1` to fail instead of skip when tools, definitions
or user namespaces are unavailable. The security CI workflow provisions a disposable
Linux runner, updates ClamAV definitions and runs real sandbox, signature and EICAR
checks. Its separate root-staging profile uses temporary installation and cache
paths. It does not change a real login screen or bootloader. Real desktop/boot
verification still requires a disposable VM or test machine.

The privileged installer does not run Fontconfig or icon-cache parsers on new
third-party files as root. Cache refresh remains in the user session or is handled
lazily by the desktop. Applying an installed font/theme still allows the desktop
to consume it with that component's privileges; Orbits cannot sandbox GNOME, GDM,
Plymouth or other applications from inside this installer.
