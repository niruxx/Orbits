# Orbits Roadmap

Orbits is a wallpaper and theming app for GNOME. This document covers where it stands compared
with similar tools, what's missing, and what would be nice to have.

---

## What Orbits does today (0.3.0)

| Area | What ships |
|---|---|
| Wallpapers | KDE Store, Wallhaven, Bing, NASA APOD, Openverse and Reddit; favorites/collections; exact duplicates; editing; daily XML/HEIC schedules; per-monitor composition; systemd rotation |
| Fonts | Google/Nerd/local fonts; collections, activation, comparison/pairings, glyph/OpenType/license details, variable axes and per-user fontconfig rules |
| Themes | GTK (including libadwaita linking), GNOME Shell, icon, cursor and Plymouth boot splash themes, all from gnome-look.org, with real previews |
| App Colors | Presets and wallpaper palettes; GNOME accent matching; Gradience import/local gallery; Kitty, Alacritty, Ptyxis, VS Code and Firefox color exports; GTK3/Flatpak support |
| Looks | Whole/partial desktop profiles; screenshots; portable static/XML wallpapers; community catalogue import/share; day/night and weekday schedules; before snapshots |
| Login screen | GDM background, style, clock, branding, behaviour and auto-login, with a live preview |
| Updates | Daily checks for store-installed wallpapers, themes and fonts |
| System | System Check; Debian/Ubuntu `update-alternatives` support; SELinux relabeling; read-only `/usr` detection |

---

## How Orbits compares

Most existing tools each cover one slice of what Orbits does. The table shows which tool is the
usual choice for each job.

| Job | Common tool(s) | What they do better | Where Orbits differs |
|---|---|---|---|
| Wallpaper rotation | **Variety** | Mature rotation, many sources, quotes, filters, tray icon | Orbits now rotates Library collections, Bing Daily or store searches using a systemd user timer |
| Per-monitor wallpapers | **HydraPaper** | A different wallpaper on each monitor | Orbits composes selected images into one spanned wallpaper |
| Downloading wallpapers | **Wallpaper Downloader**, browser + Wallhaven | Simple, focused | Orbits merges several sources and installs Plasma wallpaper packages |
| Animated / live wallpapers | **Hidamari**, **Komorebi** (unmaintained) | Video and animated backgrounds | Orbits handles still images only |
| Palette from wallpaper | **pywal**, **wallust**, **matugen** | Generate colors from an image for terminals, editors and bars | App Colors generates light/dark palettes from a wallpaper and exports selected application color files |
| Libadwaita recoloring | **Gradience** (archived) | Large community preset gallery, plugins | Orbits uses current libadwaita CSS variables (Gradience predates them) and adds GTK3/Flatpak options |
| Theme store (KDE) | **Plasma's "Get New…" / Discover** | Built into the desktop; ratings and comments in-app | Orbits brings the same Pling/OCS catalogues to GNOME, plus updates |
| Theme store (GNOME) | **ocs-url / Pling Store app**, manual downloads | Covers every Pling category | Orbits detects variants, previews, applies and uninstalls |
| GNOME appearance basics | **GNOME Tweaks**, **Refine** | Many small desktop settings | Orbits focuses on looks and adds installing and sharing |
| Font management | **Font Manager**, **FontBase** | Collections, comparing fonts, deep font metadata, activate/deactivate | Orbits adds local imports, collections, activation, comparison, metadata and variable-axis controls |
| Login screen | **Login Manager Settings** (gdm-settings) | Long track record, wide distro testing | Orbits has a live preview, update-alternatives support, Looks integration and the full theming suite |
| Boot splash | `plymouth-set-default-theme`, distro tools | — | Orbits adds a store, previews and automatic initramfs rebuilds |
| Theme generation | **Themix / Oomox** | Build full GTK, icon and terminal themes from a palette | Orbits installs and recolors themes; it doesn't generate them |

**What sets Orbits apart:** one app covers discovering, installing, previewing, applying,
updating and sharing every visual layer of a GNOME desktop, from the boot splash to app colors.
Looks tie them together.

**Where it falls behind:** single-purpose tools go deeper in their area: rotation (Variety),
monitors (HydraPaper), generated palettes (pywal/matugen) and font management (Font Manager).

---

## Missing features (closing the gaps)

### Wallpapers
- [x] **Rotation and slideshows:** change the wallpaper every N minutes or daily from a Library
  collection, a store search or Bing Daily, using a systemd user timer so it works when Orbits is
  closed.
- [x] **Time-of-day wallpapers** using GNOME's XML slideshow format, plus import of macOS-style
  dynamic wallpapers (`.heic`, optional pillow-heif). HEIC frames use evenly spaced daily times;
  Apple solar/location metadata is not interpreted.
- [x] **Per-monitor wallpapers:** compose one spanned image from separate images per monitor
  (the approach HydraPaper uses).
- [x] **More sources:** NASA Astronomy Picture of the Day, authenticated Reddit wallpaper
  communities, and Openverse (with license/attribution details).
- [ ] **Unsplash and Pexels:** pending provider permission; their published API guidelines exclude
  wallpaper applications. No keys or integration are exposed until this is resolved.
- [x] **Filters:** "fits my screen" (resolution and aspect ratio), color search, and hiding
  wallpapers already in the Library. Screen filtering excludes results with unknown dimensions;
  color filtering is available for Wallhaven in Preferences → Sources.
- [x] **Favorites, collections and a duplicate finder** in the Library.
- [x] **Edit before applying:** crop, blur, dim and tint using Pillow. Edits save a new Library image; originals are retained.

### Colors
- [x] **A palette from the wallpaper** (as pywal/matugen do): pull dominant colors from the image
  into App Colors and the closest GNOME accent color.
- [x] **Colors beyond GTK:** selectable exports for Ptyxis, Kitty, Alacritty, VS Code and Firefox
  userChrome. Exported files are imported manually; existing application config is retained.
- [ ] **GNOME Console custom terminal colors:** pending an arbitrary-palette API from Console.
- [x] **A community palette gallery** (import Gradience/Orbits JSON presets into a local gallery; browse the upstream collection).
- [x] **Match the login screen's accent and text colors** to the current palette. Loads a draft in the login editor; its Apply action authenticates normally.

### Themes
- [x] **Bootloader themes:** GRUB (gnome-look category 109) and rEFInd (GitHub), with local
  archive import, backups, apply/remove/reset, and GRUB menu regeneration in the helper.
- [x] **More theme types:** sound themes, GTK2 and GTK1 themes with separate legacy rc files,
  plus terminal scheme import/export (Konsole, Ptyxis, Kitty, Alacritty, iTerm and terminal.sexy JSON).
- [ ] **Themes that ship only an `install.sh`:** build them in a sandbox (bubblewrap, no network,
  writable output folder only).
- [x] **Store details in the app:** OCS ratings, version/changelog and paginated comments with
  replies, loaded asynchronously in the detail dialog with separate error/retry states.
- [x] **A boot splash preview** using Plymouth's X11 renderer, before rebuilding the initramfs.
  Requires bubblewrap, the X11 renderer, and a local X11/XWayland session; runs for 10 seconds
  with isolated runtime sockets/configuration and leaves the selected boot theme unchanged.
- [x] **Theme health check:** flag removed GTK3 style properties, missing/imported CSS files,
  author-declared GTK/libadwaita version bounds and older styling patterns. Heuristics are labeled;
  checks run before linking a GTK4 theme and are available from each installed GTK theme menu.

### Fonts
- [x] **Install from local files** (`.ttf`/`.otf`/`.zip`) with drag and drop.
- [x] **Font collections, side-by-side comparison** and font details (glyph map, OpenType
  features, license), matching Font Manager.
- [x] **Turn fonts on and off without uninstalling** them.
- [x] **Font pairing suggestions** (heading plus body) and sliders for variable font axes.
- [x] **Fontconfig rules:** default emoji font, per-font hinting, substitutions.

### Looks
- [x] **A community Looks gallery:** browse/preview/import local or HTTPS catalogues, with
  SHA-256 verification. Export shareable Looks, screenshots and a static catalogue manifest.
  No central publishing service is configured; catalogues are shared or hosted by users.
- [x] **Scheduled Looks:** day/night or per-weekday saved Looks via a systemd user timer,
  including while Orbits is closed. Schedules apply desktop settings only, in local time.
- [x] **Partial apply:** choose which parts of a Look to apply (for example only colors and
  fonts).
- [x] **A screenshot preview** of the desktop when saving a Look, requested through the desktop
  screenshot portal. Capture is optional; screenshots are stored with and exported in the Look.

### Login screen
- [x] **Preview the real greeter** before applying (`gnome-shell --devkit`), with a private theme,
  settings database, session bus and runtime directory. Authentication is unavailable in the preview.
- [x] **Re-apply automatically after updates:** opt-in pacman and apt hooks, plus a systemd
  resource watcher for DNF/RPM updates. Only the applied theme is replayed.
- [x] **Per-monitor and light/dark login backgrounds;** clock font and panel opacity. Monitor
  backgrounds use a composed image; GDM must use the saved display layout.

---

## Good to haves

- [x] **Remaining security boundaries:** isolate archive parsing and font inspection/
  rendering; require raster font previews; block downloaded scripted Plymouth themes.
- [x] **Publisher authentication:** explicit hostname/key pins with Minisign, per-use
  verification and optional strict signature enforcement. Community sources remain
  unverified unless publishers provide signatures and users configure trusted keys.
- [x] **Older-asset validation:** explicit validation before creating first integrity
  baselines; never replace a failed or existing baseline or claim publisher identity.
- [x] **Required live test profile and security CI:** real sandbox access restrictions,
  font/archive processing, Minisign, EICAR and root staging against temporary paths.
  This host cannot execute live namespace/antivirus checks; CI has not been run here.

- [x] **Public HTTPS transport:** DNS-pinned connections, checked redirects, no
  downgrade/private-network destinations, and explicit local-file import intent.
- [x] **Bounded archive extraction:** actual-byte budgets, member/count/depth limits,
  and rejection of traversal, duplicate/conflicting paths, links and special files.
- [x] **Asset and privileged-install checks:** structural font validation, data-only
  theme policy, descriptor-anchored privileged snapshots and staged revalidation.
- [x] **Security visibility and change detection:** definition/tool status, database
  file-age warnings, SHA-256 manifests for new managed assets and explicit integrity
  checks. Existing assets without a baseline remain unverified; records are not
  publisher signatures. Adversarial tests include an optional live EICAR check.

- [x] **Download checks and optional malware scanning:** local ClamAV scans before
  installation (including cached downloads and updates), blocking on detections or
  scan errors; download/archive limits; decoded raster wallpaper validation.
  Preferences has independent scanning and automatic definition-update switches,
  plus a manual update action using a private, unprivileged freshclam database.
- [x] **Isolated image processing:** required bubblewrap decoding for wallpaper
  installation, remote/local previews, local wallpaper imports and Look images;
  reconstruct PNGs from bounded raw pixels, stripping original metadata. Local
  fonts/theme archives use snapshot and malware checks. Preferences offers an
  explicit scan of existing user appearance assets without rewriting them.
  Live bubblewrap isolation needs verification on a host permitting namespaces;
  FontTools inspection, FreeType previews and archive parsers now use restricted
  workers. Applied desktop theme/font engines remain outside Orbits isolation.

- [x] **Global search** across installed wallpapers, themes, fonts and Looks (Ctrl+K).
  Online discovery remains in each store's search.
- [x] **A GNOME Shell search provider:** type a saved Look's name in the overview to apply
  its desktop components. The provider uses local IDs and launches an unprivileged apply action.
- [x] **Undo toasts** for appearance applies and uninstall operations. User snapshots retain
  removed files for the session; privileged undo uses authenticated, root-owned snapshots.
- [x] **Download manager:** queue, pause/resume, cancel, retained partial transfers and OCS MD5
  verification before extraction. Retry Install after cancellation or restarting the app.
- [x] **Offline mode:** cached catalogue queries, cached previews, installed items and clear
  offline states. Uncached requests need a connection; online caches are revalidated.
- [x] **Translations** (gettext infrastructure, an initial Spanish catalogue, English fallback)
  and an accessibility pass for icon labels, search focus and keyboard activation in grids.
- [x] **A quick switcher** window for Looks and wallpaper rotation, available from the menu,
  Ctrl+Shift+K, the launcher action and `orbits --quick-switcher`.
- [x] **Other desktops:** Cinnamon and Budgie settings adapters plus XFCE's `xfconf-query`
  backend for GTK, icons, cursors, fonts and wallpaper. Shell and separate lock-screen features
  remain desktop-specific.
- [x] **Packaging:** GPLv3 `LICENSE`, Meson, deterministic GitHub release archives, generated
  checksum-pinned AUR recipes, and Debian/Fedora build recipes. Publishing and distribution
  builds are separate steps; a Flatpak remains unsuitable for the system-wide features.

---

## Known issues and tech debt

- The Good to haves implementations have isolated HTTP, cache, search-provider, undo, desktop
  adapter and release-archive tests. Meson builds and staged installation are validated. Live
  Shell D-Bus activation, screen readers and Cinnamon/Budgie/XFCE settings still need real desktop
  verification. Debian/RPM build tools are unavailable here, so those recipes need distro builds;
  no release or AUR submission has been published. Spanish coverage is initial rather than complete.
- User undo is session-local and capped at twenty records; privileged undo retains ten snapshots.
  Cancelled downloads retain partial data; retry Install to resume after cancellation or app restart.

- Login-screen features have isolated image, resource, update-hook and preview-process regression
  tests. Devkit rendering, GDM monitor geometry and package-manager integration still need
  verification on real GNOME installations. Per-monitor backgrounds must be reapplied after
  display layout changes; the user-session lock background remains GNOME-managed.

- New wallpaper and color backends have isolated regression tests; GTK UI, systemd session behavior,
  mixed-DPI spanned layouts and source credentials still need testing on a real GNOME desktop.
- Looks preserve and bundle GNOME XML slideshow frames. Screenshot portal behavior, font-manager
  dialogs and scheduled application still need verification in a real GNOME desktop session.
- Font enable/disable and fontconfig preferences are per-user. Already-running applications may
  need restarting. Font collection (`.ttc`/`.otc`) inspection currently shows the first face.

- New theme types, OCS response normalization, health checks and preview command isolation have
  regression tests. Actual Plymouth X11 rendering and GTK UI behavior still need desktop testing.
- Bootloader theme changes have temporary-directory regression tests; GRUB regeneration and
  rEFInd rendering still need end-to-end verification on real installations.
- The login screen Apply/Reset, the boot splash change and installing for all users have only
  been tested against temporary copies, not end-to-end on a real system.
- update-alternatives support (Ubuntu) and SELinux relabeling (Fedora) have only been tested in
  simulation; they need real installs.
- Large grids use `Gtk.FlowBox`; switch to `GridView` so memory stays flat while scrolling.
- `gdm_page.py` and `themes_page.py` are large and should be split up.
- Store items with several downloads install the last archive by default; remember the user's
  choice per item.
