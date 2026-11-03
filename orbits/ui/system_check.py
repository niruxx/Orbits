"""System Check dialog: which Orbits features work on this machine."""
from gi.repository import Adw, Gtk

from orbits import net
from orbits.system import run_checks

AREA_ICONS = {"General": "computer-symbolic", "Login Screen": "system-lock-screen-symbolic",
              "Boot Splash": "system-reboot-symbolic", "Themes": "applications-graphics-symbolic",
              "Fonts": "preferences-desktop-font-symbolic"}


class SystemCheckDialog(Adw.Dialog):
    def __init__(self):
        super().__init__(title="System Check", content_width=620, content_height=680)
        self.page = Adw.PreferencesPage()
        spinner = Adw.Spinner(width_request=40, height_request=40, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, vexpand=True)
        self.stack = Gtk.Stack()
        self.stack.add_named(spinner, "loading")
        self.stack.add_named(self.page, "results")
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(self.stack)
        self.set_child(view)
        net.run_async(run_checks, self._show)

    def _show(self, checks, err):
        if err:
            self.page.add(Adw.PreferencesGroup(title="Couldn't check this system", description=str(err)))
            self.stack.set_visible_child_name("results")
            return
        problems = [c for c in checks if not c.ok and c.level != "info"]
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=6)
        icon = Gtk.Image(icon_name="object-select-symbolic" if not problems else "dialog-warning-symbolic",
                         pixel_size=48, css_classes=["success" if not problems else "warning"])
        head.append(icon)
        n = len(problems)
        head.append(Gtk.Label(label="Everything Works" if not n else f"{n} thing{'s' if n > 1 else ''} to look at",
                              css_classes=["title-2"]))
        head.append(Gtk.Label(label="All Orbits features are available on this system." if not n
                              else "Some features are limited. Details below.", css_classes=["dim-label"],
                              wrap=True, justify=Gtk.Justification.CENTER))
        summary = Adw.PreferencesGroup()
        summary.add(head)
        self.page.add(summary)
        groups = {}
        for c in checks:
            if c.area not in groups:
                groups[c.area] = Adw.PreferencesGroup(title=c.area)
                self.page.add(groups[c.area])
            row = Adw.ActionRow(title=c.title, subtitle=c.detail, subtitle_selectable=True)
            icon = "object-select-symbolic" if c.ok else ("dialog-information-symbolic" if c.level == "info"
                                                      else "dialog-warning-symbolic")
            img = Gtk.Image(icon_name=icon)
            img.add_css_class("success" if c.ok else ("dim-label" if c.level == "info" else "warning"))
            row.add_suffix(img)
            groups[c.area].add(row)
        self.stack.set_visible_child_name("results")
