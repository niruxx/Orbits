"""Bootloader Themes page: auto-detects the bootloader, then Installed / Discover for GRUB or rEFInd."""
from orbits.i18n import _ as tr
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from orbits import boot, net
from orbits.privileged import run_helper
from orbits.ui.browse import BrowsePage
from orbits.ui.widgets import CoverPicture, load_texture_into, open_uri
from orbits.theme_sources import source_url

UNSUPPORTED = {
    "systemd-boot": "systemd-boot shows a plain text menu and has no theme support.",
    "limine": "Limine theming (wallpaper and colors in limine.conf) isn't supported yet.",
}


class LoaderView(Adw.Bin):
    """Installed + Discover for one bootloader."""

    def __init__(self, page, loader: boot.Loader, chooser):
        super().__init__()
        self.page, self.window, self.loader = page, page.window, loader
        self.manager = boot.get_manager(loader.id)
        self.stack = Adw.ViewStack()

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        scan = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Scan Installed Themes (needs password)"))
        scan.connect("clicked", lambda *_: self.scan())
        header.pack_end(scan)
        import_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=tr("Install Theme from File"))
        import_btn.connect("clicked", lambda *_: self.install_file())
        header.pack_end(import_btn)

        self.banner = Adw.Banner()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=24, margin_end=24,
                       margin_top=12, margin_bottom=32)
        body.append(page.detected_group(loader, chooser))
        self.themes_group = Adw.PreferencesGroup(title=tr('{value0} Themes').format(value0=loader.name))
        reset = Gtk.Button(label=tr("Restore Default Theme"), valign=Gtk.Align.CENTER)
        reset.connect("clicked", lambda *_: self.reset())
        header.pack_end(reset)
        body.append(self.themes_group)
        self._rows = []
        installed = Adw.ToolbarView()
        installed.add_top_bar(header)
        installed.add_top_bar(self.banner)
        installed.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                                 child=Adw.Clamp(maximum_size=900, child=body)))
        self.stack.add_titled_with_icon(installed, "installed", tr("Installed"), "object-select-symbolic")

        self.browse = BrowsePage(
            self.window, boot.SOURCES[loader.id], lib=self.manager, apply_label="Install & Apply",
            apply_icon="object-select-symbolic", on_apply=lambda e: self.apply(e["names"][0]),
            describe=lambda e: f"Installed “{', '.join(e['names'])}” for {loader.name}",
            title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE),
            wallpaper_extras=False, on_install_error=self._report_error)
        self.stack.add_titled_with_icon(self.browse, "discover", tr("Discover"), "web-browser-symbolic")
        self.stack.connect("notify::visible-child-name",
                           lambda *_: self.stack.get_visible_child_name() == "discover" and self.browse.on_shown())
        self.set_child(self.stack)
        self.manager.connect("changed", lambda *_: self.reload())
        self.reload()

    # ---------------------------------------------------------------- list
    def reload(self):
        for r in self._rows:
            self.themes_group.remove(r)
        self._rows = []
        system = self.manager.system
        current = (system or {}).get("current", "")
        names = {}
        for e in self.manager.items.values():
            for n in e["names"]:
                names[n] = {"managed": True, "entry": e}
        for t in (system or {}).get("themes", []):
            info = names.setdefault(t["name"], {"managed": t["managed"], "entry": None})
            info["metadata"] = t.get("metadata", {})
        if system is None:
            self.themes_group.set_description(tr("Your boot files are only readable by administrators. Press ↻ to scan "
                                              "them; themes installed with Orbits are listed below either way."))
        else:
            self.themes_group.set_description(tr('{value0} installed in {value1} · current: {value2}').format(value0=len(names), value1=system.get('themes_dir', ''), value2=current or 'default'))
        if self.loader.id == 'refind':
            default = Adw.ActionRow(title=tr("rEFInd Default Theme"),
                subtitle=tr("Built-in appearance. Installed themes stay available."))
            use_default = Gtk.Button(label=tr("Use Default"), valign=Gtk.Align.CENTER)
            use_default.connect('clicked', lambda *_: self.reset())
            default.add_suffix(use_default)
            self.themes_group.add(default)
            self._rows.append(default)
        if not names:
            row = Adw.ActionRow(title=tr("No themes yet"), subtitle=tr("Find one in the Discover tab"))
            self.themes_group.add(row)
            self._rows.append(row)
            return
        for name, info in sorted(names.items(), key=lambda kv: (kv[0] != current, kv[0].lower())):
            row = Adw.ActionRow(title=GLib.markup_escape_text(name),
                                subtitle=("Current · " if name == current else "") +
                                ("installed with Orbits" if info["managed"] else "installed outside Orbits"))
            e = info["entry"]
            if e and e.get("preview"):
                pic = CoverPicture(96, 54, css_classes=["card"], valign=Gtk.Align.CENTER)
                load_texture_into(pic, url=e["preview"])
                row.add_prefix(pic)
            else:
                row.add_prefix(Gtk.Image(icon_name="drive-harddisk-symbolic", pixel_size=32))
            if name != current or self.loader.id == "refind":
                b = Gtk.Button(label=tr("Reapply") if name == current else tr("Apply"), valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
                b.connect("clicked", lambda _b, n=name: self.apply(n))
                row.add_suffix(b)
            if name == current:
                row.add_suffix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["accent"]))
            origin = source_url(e or {}) or source_url(info.get("metadata", {}))
            if origin:
                source = Gtk.Button(child=Adw.ButtonContent(label=tr("Source"), icon_name="external-link-symbolic"),
                                    valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text=origin)
                source.connect("clicked", lambda _button, url=origin: open_uri(self.window, url))
                row.add_suffix(source)
            if info["managed"]:
                rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                                tooltip_text=tr("Remove"))
                rm.connect("clicked", lambda _b, n=name: self.remove(n))
                row.add_suffix(rm)
            self.themes_group.add(row)
            self._rows.append(row)

    # ---------------------------------------------------------------- actions
    def _busy(self, text):
        self.banner.set_title(text or "")
        self.banner.set_revealed(bool(text))

    def _report_error(self, message):
        message = str(message or tr("Boot theme operation failed"))
        if any(token in message.lower() for token in ('read-only', 'read only', 'erofs')):
            dialog = Adw.AlertDialog(
                heading=tr("Boot Partition Is Read-Only"),
                body=tr("The filesystem containing /boot or your EFI system partition is mounted read-only. "
                        "Orbits could not complete the boot theme change. "
                        "Check that the correct partition is mounted writable, then try again.")
                     + "\n\n" + tr("Reason: {reason}").format(reason=message))
            dialog.add_response("close", tr("Close"))
            dialog.set_default_response("close")
            dialog.set_close_response("close")
            dialog.present(self.window)
        else:
            self.window.toast(message, timeout=6)

    def install_file(self):
        from orbits.sources.base import Download, Item

        filt = Gtk.FileFilter(name="Theme archives")
        for pattern in ("*.zip", "*.tar", "*.tar.gz", "*.tgz", "*.tar.xz", "*.txz", "*.tar.bz2", "*.tar.zst"):
            filt.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog = Gtk.FileDialog(title=tr('Install {value0} Theme').format(value0=self.loader.name), filters=filters)

        def selected(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            path = file.get_path() if file else None
            if not path:
                return
            path = Path(path)
            item = Item(source=f"local-{self.loader.id}", id=str(path), title=path.name)
            self._busy(f"Installing {path.name}…")

            def done(entry, error):
                self._busy(None)
                if error:
                    self._report_error(error)
                else:
                    self.window.toast(f"Installed {', '.join(entry['names'])}", timeout=6)

            self.manager.install(item, Download(path.name, path.as_uri()), done)

        dialog.open(self.window, None, selected)

    def scan(self):
        self._busy("Reading boot files…")
        self.manager.scan(lambda ok, msg: (self._busy(None), ok or self.window.toast(msg, timeout=6)))

    def apply(self, name):
        extra = " and regenerating grub.cfg" if self.loader.id == "grub" else ""
        self._busy(f"Setting {self.loader.name} theme to “{name}”{extra}…")

        def done(ok, msg):
            self._busy(None)
            if ok:
                self.window.toast(f"{self.loader.name} will use “{name}” next boot", timeout=6)
            else:
                self._report_error(msg)
            if ok:
                self.manager.scan(lambda *_: None)

        run_helper(["boot-theme-apply", self.loader.id, name], done)

    def reset(self):
        dlg = Adw.AlertDialog(heading=tr("Restore {value0}'s Default Look?").format(value0=self.loader.name),
                              body=(tr("Restores rEFInd’s built-in appearance by disabling custom theme includes and appearance overrides. Boot entries and installed themes are kept.")
                                    if self.loader.id == "refind" else tr("Removes the theme Orbits set. Installed themes stay available.")))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("reset", tr("Use Default"))
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def go():
            self._busy("Restoring default…")
            run_helper(["boot-theme-default" if self.loader.id == "refind" else "boot-theme-reset", self.loader.id],
                       lambda ok, msg: (self._busy(None), self.window.toast("Default look restored") if ok else self._report_error(msg),
                                        ok and self.manager.scan(lambda *_: None)))

        dlg.connect("response", lambda _d, r: r == "reset" and go())
        dlg.present(self.window)

    def remove(self, name):
        dlg = Adw.AlertDialog(heading=tr('Remove “{value0}”?').format(value0=name), body=tr("Permanently removes this theme and its unshared cache data. If active, the default is restored. This cannot be undone."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("remove", tr("Remove"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def go():
            def removed(ok,message):
                if not ok:
                    self._report_error(message)
                    return
                try:self.manager.forget(name)
                except (OSError,RuntimeError) as error:
                    self._report_error(error)
                    return
                self.window.toast(f"Removed “{name}”")
                self.manager.scan(lambda *_: None)
            run_helper(["boot-theme-remove", self.loader.id, name],removed)

        dlg.connect("response", lambda _d, r: r == "remove" and go())
        dlg.present(self.window)


class BootPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "Bootloader"
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.Spinner(width_request=40, height_request=40, halign=Gtk.Align.CENTER,
                                         valign=Gtk.Align.CENTER), "loading")
        self.set_child(self.stack)
        self._built = False

    def on_shown(self):
        if not self._built:
            self._built = True
            net.run_async(boot.detect, self._detected)

    def toggle_search(self):
        child = self.stack.get_visible_child()
        if isinstance(child, LoaderView) and child.stack.get_visible_child_name() == "discover":
            child.browse.toggle_search()

    def _detected(self, loaders, err):
        if err:
            self.window.toast(tr('Bootloader detection failed: {value0}').format(value0=err), timeout=6)
        self.loaders = loaders or []
        supported = [lo for lo in self.loaders if lo.supported]
        if not supported:
            booted = next((lo for lo in self.loaders if lo.booted), None)
            msg = UNSUPPORTED.get(booted.id, "") if booted else ""
            status = Adw.StatusPage(
                icon_name="drive-harddisk-symbolic",
                title=f"{booted.name} Detected" if booted else "No Supported Bootloader Found",
                description=(msg + "\n\n" if msg else "") + "Orbits can theme GRUB and rEFInd.")
            view = Adw.ToolbarView()
            view.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("Bootloader"))))
            view.set_content(status)
            self.stack.add_named(view, "none")
            self.stack.set_visible_child_name("none")
            return
        for lo in supported:
            self.stack.add_named(LoaderView(self, lo, len(supported) > 1), lo.id)
        self.stack.set_visible_child_name(supported[0].id)

    def detected_group(self, loader, chooser):
        g = Adw.PreferencesGroup(title=tr("Detected"))
        for lo in self.loaders:
            row = Adw.ActionRow(title=lo.name, subtitle=lo.detail + ("" if lo.supported else
                                                                    " · " + UNSUPPORTED.get(lo.id, "no theme support")))
            row.add_prefix(Gtk.Image(icon_name="drive-harddisk-symbolic"))
            if lo.id == loader.id:
                row.add_suffix(Gtk.Label(label=tr("Managing"), css_classes=["badge-count"], valign=Gtk.Align.CENTER))
            elif lo.supported and chooser:
                b = Gtk.Button(label=tr("Manage"), valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda _b, i=lo.id: self.stack.set_visible_child_name(i))
                row.add_suffix(b)
            g.add(row)
        if loader.id == "grub":
            g.set_description(tr("Applying a theme edits GRUB_THEME in /etc/default/grub (backed up first) and "
                              "regenerates grub.cfg."))
        else:
            g.set_description(tr("Themes are copied to EFI/refind/themes on your EFI partition and enabled with an "
                              "Orbits-managed include at the end of refind.conf (backed up first)."))
        return g
