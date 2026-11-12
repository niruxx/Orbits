"""Theme apply, health, export and privileged actions."""
import os
from pathlib import Path
from gi.repository import Adw, GLib, Gtk
from orbits.i18n import _ as tr
from orbits import net, themes
from orbits.privileged import run_helper

class ThemeActions:
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
        self.count_label.set_label(tr('{value0} installed · current: {value1}').format(value0=len(self.installed_items), value1=theme.display))
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
            match = next((theme for theme in self.installed_items if theme.name == cur and theme.path), None)
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


