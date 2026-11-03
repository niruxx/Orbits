# Orbits Roadmap

Ideas for where Orbits goes next, grouped by priority. Checked items shipped in 0.3.0. **Now** covers gaps in what already ships
(reliability, safety, polish). **Next** adds features that build on the existing pieces. **Later**
holds bigger bets.

---

## Now — harden what exists

### Reliability
- [ ] **Automatically re-apply the login theme after `gnome-shell` updates.** Ship a pacman hook
  (`/usr/share/libalpm/hooks/orbits-gdm.hook`) that re-runs the helper with the last applied spec.
  Today the user has to notice the "A system update reset your login screen theme" banner.
- [ ] **Save the last applied GDM spec** (and the rendered background) in `/var/lib/orbits/`, so
  re-applying doesn't depend on the user's cache folder still existing.
- [ ] **Preview the real login screen before applying**, e.g. by launching the greeter in a nested
  window (`gnome-shell --devkit` on GNOME 49+). The current preview is a mock-up.
- [ ] **Verify downloads.** The OCS API returns `downloadmd5sum`, so check it before unpacking.
- [ ] **Cancel and retry downloads.** A cancel button on progress bars, and resuming after
  network errors.
- [ ] **Undo after destructive actions.** "Uninstalled X — Undo" toasts that hold the files in a
  trash folder until the toast expires. The same for apply actions (wallpaper, themes, fonts).

### Safety
- [ ] **Separate polkit actions per operation** (login screen, fonts, themes, boot splash), each
  with its own message, so the password prompt says exactly what is about to change.
- [ ] **Pass the GDM spec to the helper over stdin** instead of a file in the user's cache, so
  there's no window to swap the file between writing it and the helper reading it.
- [ ] **Warn about store content.** gnome-look and KDE Store items are user uploads. Show an
  "unverified" note when installing for all users or as a boot splash.
- [x] **Detect read-only `/usr`** (Fedora Silverblue, NixOS, SteamOS) and disable the system-wide
  features cleanly, with an explanation.

### Cross-distro support
- [x] **Ubuntu/Debian login theme location.** GDM there uses `gdm-theme.gresource` via
  `update-alternatives` rather than `gnome-shell-theme.gresource` directly. Detect it and register
  an alternative instead of overwriting. *Shipped and tested against a simulated
  `update-alternatives`. Still to do: a test run on a real Ubuntu install.*
- [x] **Fedora:** test the dracut initramfs path and SELinux labels on files written to
  `/usr/share` (`restorecon`). *Shipped: the helper relabels everything it writes when SELinux is
  active. Still to do: a test run on a real Fedora install.*
- [x] **A distro check** that lists which features work on this system (GDM present, Plymouth
  present, User Themes extension installed, initramfs tool found). *Shipped as **System Check** in
  the main menu.*

### Quality
- [ ] **Add a `LICENSE` file.** GPL-3.0-or-later is declared in the PKGBUILD, metainfo and About
  window, but the repository has no license text.
- [x] **Unit tests for the root helper:** gresource rebuild and restore, `custom.conf` editing,
  `_check_tree` symlink and ownership rules, name validation.
- [ ] **Unit tests for archive handling:** wallpaper variant detection, theme detection, Plymouth
  `ImageDir` naming, light/dark pairing. *Theme detection and Plymouth naming are covered; wallpaper
  variants and light/dark pairing are still to do.*
- [ ] **CI** (GitHub Actions): run the tests, byte-compile, build the PKGBUILD in an Arch
  container, and take headless broadway screenshots of every page.
- [ ] **A `--debug` flag and a log file** (`~/.cache/orbits/orbits.log`), with a "Copy debug info"
  button in About for bug reports.
- [ ] **Accessibility:** tooltips/labels on every icon-only button, keyboard navigation through
  card grids, sensible focus order in dialogs.
- [ ] **Translations:** wrap strings in gettext and add a `po/` folder.

### Performance
- [ ] **Replace `Gtk.FlowBox` with `Gtk.GridView`/`ListView`** for long lists (infinite scroll,
  All Fonts, large theme lists). FlowBox keeps every card alive, so memory grows as you scroll.
- [ ] **Limit concurrent thumbnail downloads** and cancel ones that scroll off screen.
- [ ] **Cache OCS responses** for a few minutes so switching pages and sort orders is instant and
  works offline.

---

## Next — features that build on what's there

### Looks (desktop profiles)
- [x] **Save a complete "Look"** in one click: wallpaper (light and dark), GTK, libadwaita, Shell,
  icon and cursor themes, fonts, accent color and login screen settings.
- [x] **Switch, export and import Looks** as a single `.orbits` file (settings plus download links,
  so the file stays small).
- [x] **Undo history:** "Restore my desktop from yesterday".

### Wallpapers
- [ ] **Rotation and slideshows:** change the wallpaper every N minutes or daily from a library
  folder or a source. Bing Daily can update itself through a systemd user timer.
- [ ] **Time-of-day wallpapers**, using GNOME's XML slideshow format (morning, day, evening, night).
- [ ] **Accent color from the wallpaper:** pick the closest GNOME accent color from the image's
  dominant colors when applying.
- [ ] **More sources:** Unsplash and Pexels (free API keys), NASA Astronomy Picture of the Day,
  Reddit wallpaper communities, and DeviantArt or ArtStation where their terms allow.
- [ ] **Filters:** "fits my screen" (match the monitor's resolution and aspect ratio), plus
  search by color on Wallhaven.
- [ ] **Favorites and collections** in the Library, a duplicate finder, and a
  fit/zoom/center/span choice per wallpaper.

### Fonts
- [ ] **Install fonts from local files** (`.ttf`/`.otf`/`.zip`), including drag and drop.
- [ ] **Font pairing suggestions** (a heading font plus a body font) with a preview.
- [ ] **Sliders for variable fonts** (weight, width, optical size) in the font detail view.
- [ ] **Fontconfig settings:** default emoji font, per-font hinting and antialiasing, and
  substitution rules ("use Inter wherever Helvetica is requested").
- [ ] **Nerd Fonts previews** showing real icon glyphs (the cards currently use the system monospace
  font).

### Themes
- [x] **Updates view:** compare each store item's `changed` date with the install date, and offer
  one-click updates for wallpapers, themes and fonts.
- [ ] **GRUB themes** (gnome-look category 109). This needs `grub-mkconfig` in the helper and
  careful backups.
- [ ] **GDM themes** from gnome-look (category 131), imported into the Login Screen page.
- [ ] **Boot splash preview** using the Plymouth X11 renderer (`plymouthd --debug` +
  `plymouth show-splash`) in a sandboxed window.
- [ ] **Flatpak apps:** apply GTK themes to Flatpak apps (`flatpak override --filesystem` plus
  `GTK_THEME`, or install the matching `org.gtk.Gtk3theme.*` runtime).
- [x] **Libadwaita recoloring:** generate `~/.config/gtk-4.0/gtk.css` from a palette (accent,
  window, headerbar, card colors) with a live preview. A safer alternative to full GTK4 themes,
  which often break new libadwaita widgets.
- [ ] **Themes that ship only an `install.sh`:** build them in a sandbox (bubblewrap, no network,
  writable output folder only) instead of failing with "open its page for instructions".

### Login screen
- [ ] **Login and lock screen clock fonts and colors** beyond size and weight, plus panel
  transparency.
- [ ] **A per-monitor background** for multi-monitor login screens.
- [ ] **Light and dark login backgrounds** that follow the GDM color scheme.

---

## Later — bigger bets

- [ ] **Global search** across wallpapers, fonts and themes from one search box (Ctrl+K).
- [ ] **A community "Looks" gallery:** share and browse complete desktop setups, with
  screenshots.
- [ ] **A GNOME Shell search provider:** type "orbits nord" in the overview to apply a Look.
- [ ] **A background service for scheduled tasks** (rotation, Bing updates, update checks),
  separate from the UI, using the Background portal.
- [ ] **Other desktops:** Cinnamon, Budgie and XFCE use the same GTK, icon and cursor themes; only
  the apply step differs.
- [ ] **Build-system cleanup:** Meson, Blueprint `.ui` files for the larger pages, and a tagged
  AUR package that downloads its source from GitHub releases.

---

## Known issues and tech debt

- The Login Screen **Apply/Reset**, the **boot splash** change and **install for all users** have
  only been tested against temporary copies. They haven't run end-to-end against a real system.
- `gdm_page.py` and `themes_page.py` are large; split the row builders and card widgets into
  their own modules.
- The GDM page reads system icon and cursor themes once, so themes installed for all users
  afterwards don't show until Orbits restarts.
- Preview fonts registered with Pango stay loaded for the life of the process.
- Store items with several downloads install the last archive by default, which isn't always the
  variant the user wants. Remember the user's choice per item.
- The headless screenshot harness used during development (broadway) sometimes captures empty
  or stale frames. If it becomes the CI screenshot check, render each page into its own window.
