"""Theme pages: Installed (preview, apply, remove, install for all users) + Discover (gnome-look.org)."""
from orbits.i18n import _ as tr
from orbits.theme_sources import source_url
import os
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from orbits import net, themes
from orbits.privileged import run_helper
from orbits.ui.browse import BrowsePage
from orbits.ui.widgets import CoverPicture, load_texture_into, local_thumbnail

ICON_SAMPLES = [
    ["folder", "inode-directory"],
    ["user-home", "folder-home"],
    ["text-x-generic", "text-plain"],
    ["org.gnome.Nautilus", "system-file-manager", "file-manager"],
    ["utilities-terminal", "org.gnome.Console", "terminal"],
    ["web-browser", "firefox", "org.mozilla.firefox"],
    ["preferences-system", "org.gnome.Settings", "gnome-control-center"],
]
CURSOR_SAMPLES = [["left_ptr", "default", "arrow"], ["hand2", "pointer", "hand1"], ["xterm", "text", "ibeam"],
                  ["watch", "wait"], ["fleur", "move", "grabbing"], ["crosshair", "cross"]]


class ThemeCard(Gtk.Box):
    def __init__(self, page, theme):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["theme-card"], width_request=300)
        self.page, self.theme = page, theme
        self.preview = Gtk.Overlay(css_classes=["theme-preview"], overflow=Gtk.Overflow.HIDDEN, height_request=150)
        self.append(self.preview)
        self._build_preview()

        self.badge = Gtk.Label(label=tr("● Current"), css_classes=["badge", "accent"], halign=Gtk.Align.START,
                               valign=Gtk.Align.START, visible=False)
        self.preview.add_overlay(self.badge)

        info = Gtk.Box(spacing=8, margin_start=14, margin_end=10, margin_top=10, margin_bottom=10)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(Gtk.Label(label=theme.display, xalign=0, ellipsize=3, max_width_chars=24, css_classes=["heading"]))
        scope = {"user": "Just you", "system": "All users"}.get(theme.scope, theme.scope)
        sub = scope + (" · from Orbits" if theme.managed else "")
        if theme.display != theme.name:
            sub = f"{theme.name} · {sub}"
        text.append(Gtk.Label(label=sub, xalign=0, ellipsize=3, max_width_chars=30,
                              css_classes=["dim-label", "caption"]))
        info.append(text)
        self.apply_btn = Gtk.Button(label="Export…" if theme.kind == "terminal" else "Apply", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.apply_btn.connect("clicked", lambda *_: page.apply(theme))
        info.append(self.apply_btn)
        menu = self._menu()
        if menu:
            info.append(menu)
        self.append(info)

    # ---- preview -------------------------------------------------------
    def _build_preview(self):
        t, kind = self.theme, self.theme.kind
        if kind == "terminal" and t.path:
            from orbits.terminal_schemes import read_scheme
            try:
                scheme = read_scheme(t.path / "terminal.json")
                self.preview.set_child(self._swatches(scheme["colors"][:5]))
                return
            except (ValueError, OSError, KeyError):
                pass
        if kind == "icons" and t.path:
            self.preview.set_child(self._icon_preview())
            return
        if kind == "cursors" and t.path:
            self.preview.set_child(self._cursor_preview())
            return
        img = themes.preview_image(t) if t.path else None
        if img:
            info = GdkPixbuf.Pixbuf.get_file_info(str(img))
            small = info[0] is not None and info[1] < 260
            if small:  # tiny thumbnails (e.g. adw-gtk3) look best at their own size
                pic = Gtk.Picture(content_fit=Gtk.ContentFit.SCALE_DOWN, halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.CENTER, can_shrink=True)
                pic.set_filename(str(img))
            else:
                pic = CoverPicture(300, 150, hexpand=True, vexpand=True)
                net.run_async(local_thumbnail, lambda tex, err: tex and pic.set_paintable(tex), str(img), 600)
            self.preview.set_child(pic)
            return
        if t.meta.get("preview"):
            pic = CoverPicture(300, 150, hexpand=True, vexpand=True)
            load_texture_into(pic, url=t.meta["preview"])
            self.preview.set_child(pic)
            return
        if kind == "gtk" and t.path:
            colors = themes.gtk_colors(t.path)
            if colors:
                self.preview.set_child(self._swatches(colors))
                return
        icon = Gtk.Image(icon_name=themes.KINDS[kind].icon, pixel_size=48, css_classes=["dim-label"],
                         hexpand=True, vexpand=True)
        self.preview.set_child(icon)

    def _icon_preview(self):
        theme = Gtk.IconTheme()
        theme.set_theme_name(self.theme.name)
        box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        flow = Gtk.FlowBox(max_children_per_line=4, min_children_per_line=4, selection_mode=Gtk.SelectionMode.NONE,
                           column_spacing=10, row_spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                           can_target=False)
        for names in ICON_SAMPLES[:8]:
            paintable = theme.lookup_icon(names[0], names[1:], 44, 1, Gtk.TextDirection.NONE, 0)
            flow.append(Gtk.Image(paintable=paintable, pixel_size=44))
        box.append(flow)
        return box

    def _cursor_preview(self):
        box = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["cursor-strip"])
        files = [themes.cursor_file(self.theme.path, names) for names in CURSOR_SAMPLES]
        images = []
        for f in files:
            if f:
                img = Gtk.Picture(can_shrink=False)
                images.append((img, f))
                box.append(img)

        def work():
            out = []
            for _img, f in images:
                try:
                    out.append(themes.xcursor_image(f, 32))
                except (OSError, ValueError):
                    out.append(None)
            return out

        def done(decoded, err):
            for (img, _f), d in zip(images, decoded or []):
                if d:
                    w, h, px = d
                    img.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                                            GLib.Bytes.new(px), w * 4))

        net.run_async(work, done)
        if not images:
            box.append(Gtk.Image(icon_name="input-mouse-symbolic", pixel_size=48, css_classes=["dim-label"]))
        return box

    def _swatches(self, colors):
        box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        for c in colors:
            dot = Gtk.Box(css_classes=["theme-swatch"])
            prov = Gtk.CssProvider()
            prov.load_from_string(f"box {{ background-color: {c}; }}")
            dot.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            box.append(dot)
        aa = Gtk.Label(label=tr("Aa"), css_classes=["title-1"], margin_start=8)
        box.append(aa)
        return box

    # ---- actions -------------------------------------------------------
    def _menu(self):
        t, kind = self.theme, themes.KINDS[self.theme.kind]
        actions = []
        if t.path and t.kind == "plymouth":
            actions.append(("Preview Boot Splash…", lambda: self.page.preview_plymouth(t)))
        if t.path and t.kind == "gtk":
            actions.append(("Theme Health Check…", lambda: self.page.health_check(t)))
        if t.path and t.scope == "user" and kind.user_dir is not None:
            actions.append(("Install for All Users…", lambda: self.page.install_system(t)))
        if t.path and t.scope == "system" and t.managed:
            actions.append(("Remove for All Users…", lambda: self.page.remove_system(t)))
        if t.path and t.scope == "user":
            actions.append(("Uninstall", lambda: self.page.uninstall(t)))
        origin = source_url(t.meta)
        if origin:
            actions.append((tr("Open Source"), lambda: Gtk.UriLauncher.new(origin).launch(
                self.page.window, None, None)))
        if t.path:
            actions.append(("Show Files", lambda: Gtk.FileLauncher.new(Gio.File.new_for_path(str(t.path))).launch(
                self.page.window, None, None)))
        if not actions:
            return None
        btn = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=tr("Theme Actions"), css_classes=["flat"], valign=Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pop = Gtk.Popover(child=box)
        for label, cb in actions:
            b = Gtk.Button(label=label, css_classes=["flat"])
            b.get_child().set_xalign(0)
            if label == "Uninstall":
                b.add_css_class("error")
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
        btn.set_popover(pop)
        return btn

    def set_current(self, is_current):
        self.badge.set_visible(is_current)
        self.apply_btn.set_visible(not is_current)


class ThemesPage(Adw.Bin):
    def __init__(self, window, kind_id):
        super().__init__()
        self.window = window
        self.kind = themes.KINDS[kind_id]
        self.title = self.kind.title
        self.manager = themes.get_manager(kind_id)
        self.cards = []

        self.stack = Adw.ViewStack()
        installed_view = self._installed_view()
        self.stack.add_titled_with_icon(installed_view, "installed", tr("Installed"), "object-select-symbolic")
        self.source = themes.ThemeStoreSource(self.kind)
        self.browse = BrowsePage(
            window, self.source, lib=self.manager, apply_label="Install & Export" if kind_id == "terminal" else "Install & Apply", apply_icon="object-select-symbolic",
            on_apply=self._apply_entry, describe=self._describe,
            title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE),
            wallpaper_extras=False)
        self.stack.add_titled_with_icon(self.browse, "discover", tr("Discover"), "web-browser-symbolic")
        self.stack.connect("notify::visible-child-name", lambda *_: self._on_tab())
        self.set_child(self.stack)

        self.manager.connect("changed", lambda *_: self.reload())
        if kind_id in ("gtk", "icons", "cursors"):
            self._iface = themes._iface()
            key = {"gtk": "gtk-theme", "icons": "icon-theme", "cursors": "cursor-theme"}[kind_id]
            self._iface.connect(f"changed::{key}", lambda *_: self._mark_current())
        self._built = False

    # ---------------------------------------------------------------- installed tab
    def _installed_view(self):
        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Rescan"))
        refresh.connect("clicked", lambda *_: self.manager.refresh())
        header.pack_end(refresh)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Install from File…"))
        add.connect("clicked", lambda *_: self._install_file())
        header.pack_start(add)

        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.grid = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
                                max_children_per_line=6, column_spacing=16, row_spacing=16, valign=Gtk.Align.START,
                                css_classes=["font-grid"])
        self.grid.set_activate_on_single_click(False)
        self.grid.connect('child-activated', lambda _grid, child:self.apply(child.get_child().theme))
        self.count_label = Gtk.Label(xalign=0, css_classes=["dim-label"])
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=24, margin_end=24,
                       margin_top=12, margin_bottom=32)
        intro = Gtk.Box(spacing=12)
        intro.append(Gtk.Image(icon_name=self.kind.icon, pixel_size=32, css_classes=["accent"]))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titles.append(Gtk.Label(label=self.kind.title, xalign=0, css_classes=["title-2"]))
        titles.append(self.count_label)
        intro.append(titles)
        body.append(intro)
        body.append(self.info_box)
        body.append(self.grid)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(maximum_size=1800, child=body))
        self.busy_banner = Adw.Banner()
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.busy_banner)
        view.set_content(scroller)
        return view

    def on_shown(self):
        if not self._built:
            self._built = True
            self.reload()
        if self.stack.get_visible_child_name() == "discover":
            self.browse.on_shown()

    def _on_tab(self):
        if self.stack.get_visible_child_name() == "discover":
            self.browse.on_shown()

    def toggle_search(self):
        if self.stack.get_visible_child_name() == "discover":
            self.browse.toggle_search()

    def reload(self):
        if not self._built:
            return
        self._build_info()
        self.grid.remove_all()
        self.cards = []
        items = themes.installed(self.kind.id)
        if self.kind.id == "shell":
            items.insert(0, themes.InstalledTheme("shell", "", None, "system", False, "GNOME Default", {}))
        if self.kind.id == "gtk" and not any(t.name == "Adwaita" for t in items):
            items.insert(0, themes.InstalledTheme("gtk", "Adwaita", None, "system", False, "Adwaita (built in)", {}))
        cur = themes.current(self.kind.id)
        items.sort(key=lambda t: (t.name != cur, t.scope != "user", t.display.lower()))
        for t in items:
            card = ThemeCard(self, t)
            self.cards.append(card)
            self.grid.append(card)
        self.count_label.set_label(f"{len(items)} installed · export a palette for your terminal" if self.kind.id == "terminal"
                                   else f"{len(items)} installed · current: {cur or 'GNOME Default'}")
        self._mark_current()

    def _mark_current(self):
        cur = themes.current(self.kind.id)
        for c in self.cards:
            c.set_current(c.theme.name == cur)

    def _build_info(self):
        while (c := self.info_box.get_first_child()):
            self.info_box.remove(c)
        kid = self.kind.id
        group = Adw.PreferencesGroup()
        if kid in ("gtk1", "gtk2"):
            group.set_description(tr("Legacy themes use a separate GTK rc file; restart older apps after applying. Some themes require GTK engines supplied by your distribution."))
        elif kid == "sounds":
            group.set_description(tr("Sets GNOME's event-sound theme and enables event sounds. Applications must support sound themes to use it."))
        elif kid == "terminal":
            group.set_description(tr("Imports Konsole, Ptyxis, Kitty, Alacritty, iTerm and terminal.sexy palettes as data. Export for your terminal; downloaded scripts are never executed."))
        elif kid == "gtk":
            name, broken = themes.libadwaita_state()
            row = Adw.SwitchRow(title=tr("Also Theme Libadwaita Apps"),
                                subtitle=tr("Links the theme's GTK4 stylesheet into ~/.config/gtk-4.0. "
                                         "Restart apps to see it. Some themes may look off in newer apps."),
                                active=bool(name) and not broken)
            row.connect("notify::active", self._toggle_libadwaita)
            group.add(row)
            if broken:
                warn = Adw.ActionRow(title=tr("Broken libadwaita theme link"),
                                     subtitle=tr('~/.config/gtk-4.0 points to “{value0}”, which no longer exists').format(value0=name))
                warn.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                fix = Gtk.Button(label=tr("Clean Up"), valign=Gtk.Align.CENTER)
                fix.connect("clicked", lambda *_: (themes.remove_libadwaita(), self.reload(),
                                                   self.window.toast(tr("Removed broken links from ~/.config/gtk-4.0"))))
                warn.add_suffix(fix)
                group.add(warn)
            elif name:
                group.add(Adw.ActionRow(title=tr("Libadwaita theme"), subtitle=name))
        elif kid == "shell":
            ok, enabled, msg = themes.shell_status()
            row = Adw.ActionRow(title=tr("User Themes extension"),
                                subtitle=msg or ("Enabled" if enabled else "Will be enabled when you apply a theme"))
            row.add_prefix(Gtk.Image(icon_name="object-select-symbolic" if ok and not msg else "dialog-warning-symbolic"))
            group.add(row)
        elif kid == "plymouth":
            problems = themes.plymouth_status()
            for p in problems:
                r = Adw.ActionRow(title=p)
                r.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                group.add(r)
            group.set_description(tr("Boot splashes are system-wide. Applying one rebuilds your initramfs, which "
                                  "takes about a minute and asks for your password."))
        elif kid in ("icons", "cursors"):
            group.set_description(tr("Themes installed just for you don't show on the login screen; use "
                                  "“Install for All Users” for that."))
        if group.get_first_child() is not None:
            self.info_box.append(group)

    # ---------------------------------------------------------------- actions
    def apply(self, theme):
        kid = self.kind.id
        if kid == "terminal":
            self.export_terminal(theme)
            return
        if kid == "plymouth":
            self._confirm_plymouth(theme.name, theme.display)
            return
        try:
            themes.apply(kid, theme.name)
        except Exception as e:  # noqa: BLE001 — surface any failure to the user
            self.window.toast(str(e), timeout=6)
            return
        if kid == "gtk":
            name, broken = themes.libadwaita_state()
            if name and not broken:
                self._sync_libadwaita(theme)
        self._mark_current()
        self.count_label.set_label(tr('{value0} installed · current: {value1}').format(value0=len(self.cards), value1=theme.display))
        self.window.toast(tr('{value0} set to “{value1}”').format(value0=self.kind.noun.capitalize(), value1=theme.display))

    def _sync_libadwaita(self, theme):
        if theme.path and (theme.path / "gtk-4.0" / "gtk.css").exists():
            self._check_before_link(theme)
        else:
            themes.remove_libadwaita()
            self.window.toast(tr('“{value0}” has no GTK4 version — libadwaita apps use their default look').format(value0=theme.display))

    def _toggle_libadwaita(self, row, _p):
        name, broken = themes.libadwaita_state()
        if row.get_active():
            cur = themes.current("gtk")
            match = next((c.theme for c in self.cards if c.theme.name == cur and c.theme.path), None)
            if not match or not (match.path / "gtk-4.0" / "gtk.css").exists():
                self.window.toast(tr('The current theme ({value0}) has no GTK4 stylesheet').format(value0=cur))
                row.set_active(False)
                return
            self._check_before_link(match)
        elif name:
            themes.remove_libadwaita()
            self.window.toast(tr("Libadwaita apps restored to their default look"))

    def _health(self, theme):
        from orbits.theme_health import check
        adwaita = f"{Adw.get_major_version()}.{Adw.get_minor_version()}"
        gtk = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}"
        return check(theme.path, adwaita, gtk)

    def health_check(self, theme):
        def done(issues, error):
            if error:
                self.window.toast(str(error), timeout=6)
                return
            self._show_health(theme, issues)
        net.run_async(lambda: self._health(theme), done)

    def _show_health(self, theme, issues, apply=None):
        lines = []
        for issue in issues:
            location = issue["file"] + (f":{issue['line']}" if issue["line"] else "")
            lines.append((location + ": " if location else "") + issue["message"])
        dialog = Adw.AlertDialog(heading=tr('Theme Health: {value0}').format(value0=theme.display),
            body="\n\n".join(lines) if lines else "No known problems found by static checks. This is not a guarantee that every widget will render correctly.")
        dialog.add_response("close", "Cancel" if apply else "Close")
        if apply:
            dialog.add_response("apply", tr("Use Anyway"))
            dialog.connect("response", lambda _d,r: apply() if r == "apply" else self.reload())
        dialog.present(self.window)

    def _check_before_link(self, theme):
        def link():
            try:
                themes.apply_libadwaita(theme.path)
                self.window.toast(tr("Libadwaita apps will use the theme after they restart"))
                self.reload()
            except Exception as error:
                self.window.toast(str(error), timeout=6)
        def done(issues, error):
            if error:
                self.window.toast(str(error), timeout=6)
            elif issues:
                self._show_health(theme, issues, link)
            else:
                link()
        net.run_async(lambda: self._health(theme), done)

    def export_terminal(self, theme):
        from orbits.terminal_schemes import export_scheme, read_scheme
        dialog = Gtk.FileDialog(title=tr('Export {value0} for Your Terminal').format(value0=theme.display))
        def done(d, result):
            try:
                folder = d.select_folder_finish(result)
            except GLib.Error:
                return
            if not folder or not folder.get_path():
                return
            try:
                export_scheme(read_scheme(theme.path / "terminal.json"), folder.get_path())
                self.window.toast(tr("Exported Kitty, Alacritty and Ptyxis color files — import one in your terminal"), timeout=7)
            except Exception as error:
                self.window.toast(str(error), timeout=6)
        dialog.select_folder(self.window, None, done)

    def preview_plymouth(self, theme):
        display = os.environ.get("DISPLAY", "")
        authority = os.environ.get("XAUTHORITY") or str(Path.home() / ".Xauthority")
        if not display or not Path(authority).is_file():
            self.window.toast(tr("Plymouth preview needs X11/XWayland and a readable Xauthority file"), timeout=7)
            return
        self._busy("Previewing the boot splash for 10 seconds…")
        def done(ok, message):
            self._busy(None)
            self.window.toast("Preview finished; boot settings were not changed" if ok else message, timeout=7)
        run_helper(["plymouth-preview", theme.name, display, authority], done)

    def _confirm_plymouth(self, name, display):
        dlg = Adw.AlertDialog(heading=tr('Use “{value0}” at Boot?').format(value0=display),
                              body=tr("Orbits will set the Plymouth theme and rebuild your initramfs. "
                                   "This takes about a minute and requires your password."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("preview", tr("Preview First"))
        dlg.add_response("apply", tr("Set Boot Splash"))
        dlg.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        def response(_dialog, result):
            if result == "apply":
                self._set_plymouth(name, display)
            elif result == "preview":
                theme = next((t for t in themes.installed("plymouth") if t.name == name), None)
                if theme:
                    self.preview_plymouth(theme)
        dlg.connect("response", response)
        dlg.present(self.window)

    def _set_plymouth(self, name, display):
        self._busy(f"Setting boot splash to “{display}” and rebuilding initramfs…")

        def done(ok, msg):
            self._busy(None)
            self.window.toast(f"Boot splash set to “{display}” — you'll see it next boot" if ok else msg, timeout=6)
            self.reload()

        run_helper(["plymouth-set", name], done)

    def _busy(self, text):
        self.busy_banner.set_title(text or "")
        self.busy_banner.set_revealed(bool(text))

    def install_system(self, theme):
        self._busy(f"Installing “{theme.display}” for all users…")

        def done(ok, msg):
            self._busy(None)
            self.window.toast(f"“{theme.display}” is now available to all users" if ok else msg, timeout=5)
            self.manager.refresh()

        run_helper(["theme-install", self.kind.system_base, str(theme.path), theme.name], done)

    def remove_system(self, theme):
        def go():
            def done(ok, msg):
                self.window.toast(f"Removed “{theme.display}” for all users" if ok else msg, timeout=5)
                self.manager.refresh()

            run_helper(["theme-remove", self.kind.system_base, theme.name], done)

        self._confirm(tr('Remove “{value0}” for All Users?').format(value0=theme.display), tr("Requires your password."), tr("Remove"), go)

    def uninstall(self, theme):
        def go():
            try:
                self.manager.uninstall_user(theme.name)
                self.window.toast(tr('Uninstalled “{value0}”').format(value0=theme.display))
            except (RuntimeError, OSError) as e:
                self.window.toast(str(e), timeout=5)

        self._confirm(tr('Uninstall “{value0}”?').format(value0=theme.display), tr('Deletes {value0}').format(value0=theme.path), tr("Uninstall"), go)

    def _confirm(self, heading, body, verb, cb):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "ok" and cb())
        dlg.present(self.window)

    # ---------------------------------------------------------------- store integration
    def _describe(self, entry):
        names = entry.get("names", [])
        if len(names) > 1:
            return f"Installed {len(names)} {self.kind.noun}s from “{entry['title']}”"
        return f"Installed “{names[0] if names else entry['title']}”"

    def _apply_entry(self, entry):
        names = entry.get("names") or []
        if not names:
            return
        found = {t.name: t for t in themes.installed(self.kind.id)}
        theme = found.get(names[0])
        if theme is None:
            self.window.toast(tr("Installed theme not found — try Rescan"))
            return
        if len(names) > 1:
            self.window.toast(tr('Selected “{value0}” — {value1} more variants are in the Installed tab').format(value0=names[0], value1=len(names) - 1))
        self.apply(theme)

    def _install_file(self):
        filt = Gtk.FileFilter(name="Theme archives")
        for pat in (("*.palette", "*.colorscheme", "*.itermcolors", "*.json", "*.toml", "*.conf") if self.kind.id == "terminal" else ()) + ("*.zip", "*.tar", "*.tar.gz", "*.tgz", "*.tar.xz", "*.txz", "*.tar.bz2", "*.tar.zst"):
            filt.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg = Gtk.FileDialog(title=tr('Install {value0} from File').format(value0=self.kind.noun), filters=store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            path = f.get_path() if f else None
            if path:
                self._install_local(Path(path))

        dlg.open(self.window, None, done)

    def _install_local(self, path: Path):
        from orbits.sources.base import Download, Item

        item = Item(source="local", id=path.name, title=path.name)
        dl = Download(path.name, path.as_uri())
        self.manager.install(item, dl, lambda e, err: self._after_install(e, err))

    def _after_install(self, entry, err):
        if err:
            self.window.toast(str(err), timeout=6)
            return
        if entry:
            self.window.toast(self._describe(entry))
