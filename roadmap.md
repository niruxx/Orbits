# Orbits Roadmap

This roadmap tracks pending features, verification and technical debt.

## Pending features

- **Unsplash and Pexels:** pending provider permission; their published API guidelines exclude
  wallpaper applications. No keys or integration are exposed until this is resolved.
- **GNOME Console custom terminal colors:** pending an arbitrary-palette API from Console.

## Desktop and system verification

- Verify live namespace isolation and sandbox access restrictions on a host permitting namespaces,
  and run the security CI profile.
- Verify Shell D-Bus activation, screen readers and Cinnamon/Budgie/XFCE settings on real desktops.
- Verify devkit greeter rendering, GDM monitor geometry and package-manager update integration
  on real GNOME installations. Per-monitor backgrounds must be reapplied after display layout
  changes; the user-session lock background remains GNOME-managed.
- Test wallpaper and color GTK UI, systemd session behavior, mixed-DPI spanned layouts and source
  credentials on a real GNOME desktop.
- Verify screenshot portal behavior, font-manager dialogs and scheduled Looks in a real GNOME
  desktop session.
- Verify the font collection face selector in live GTK. Local-import family summaries currently
  use the first face.
- Test Plymouth X11 rendering and theme GTK UI behavior on a desktop.
- Verify GRUB regeneration and rEFInd rendering end to end on real installations, including
  rEFInd path repairs after reapplying older themes.
- Test login screen Apply/Reset, boot splash changes and installing for all users end to end
  on a real system.
- Test update-alternatives support on Ubuntu and SELinux relabeling on Fedora installations.
- Verify live scrolling, keyboard navigation and memory use in the large wallpaper, font-store,
  installed-theme and Looks grids.

## Remaining limitations and technical debt

- Older installs do not always have recorded cache ownership. Unowned legacy archives require
  Downloads → Clear Finished; shared variant caches remain until the last installed copy is removed.
- User undo is session-local and capped at twenty records; privileged undo retains ten snapshots.
- Cancelled downloads retain partial data; retry Install to resume after cancellation or app restart.
- Font enable/disable and fontconfig preferences are per-user. Already-running applications may
  need restarting.
- Applied desktop theme/font engines remain outside Orbits isolation.

## Distribution and translations

- Build and validate the Debian and RPM recipes on their target distributions.
- Publish a release and submit the AUR package.
- Expand Spanish translation coverage beyond the initial catalogue.
