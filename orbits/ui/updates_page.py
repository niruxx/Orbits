"""Updates page: store-installed wallpapers, themes and fonts with newer versions upstream."""
from orbits.i18n import _ as tr
import time

from gi.repository import Adw, GLib, Gtk

from orbits.config import settings
from orbits.ui.widgets import CoverPicture, load_texture_into
from orbits.updates import _ts, get_checker


def _ago(ts):
    if not ts:
        return "never"
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} minutes ago"
    if d < 86400:
        return f"{int(d // 3600)} hours ago"
    return f"{int(d // 86400)} days ago"


def _date(ts):
    return time.strftime("%b %-d, %Y", time.localtime(ts)) if ts else "unknown"


class UpdatesPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "Updates"
        self.checker = get_checker()
        self.running = set()

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("Updates"), subtitle=tr("Wallpapers, themes & fonts")))
        self.check_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Check for Updates"))
        self.check_btn.connect("clicked", lambda *_: self.checker.check())
        header.pack_start(self.check_btn)
        self.all_btn = Gtk.Button(label=tr("Update All"), css_classes=["suggested-action"])
        self.all_btn.connect("clicked", lambda *_: self.update_all())
        header.pack_end(self.all_btn)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.empty = Adw.StatusPage(icon_name="object-select-symbolic", title=tr("Everything Is Up to Date"),
                                    vexpand=True)
        check = Gtk.Button(label=tr("Check Now"), css_classes=["pill"], halign=Gtk.Align.CENTER)
        check.connect("clicked", lambda *_: self.checker.check())
        self.empty.set_child(check)
        self.stack.add_named(self.empty, "empty")
        spinner_page = Adw.StatusPage(title=tr("Checking for Updates…"), vexpand=True,
                                      child=Adw.Spinner(width_request=40, height_request=40,
                                                        halign=Gtk.Align.CENTER))
        self.stack.add_named(spinner_page, "checking")

        self.page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup()
        self.page.add(self.group)
        opts = Adw.PreferencesGroup()
        auto = Adw.SwitchRow(title=tr("Check Automatically"), subtitle=tr("Once a day, a few seconds after Orbits starts"),
                             active=bool(settings.get("auto_check_updates", True)))
        auto.connect("notify::active", lambda r, _p: settings.set("auto_check_updates", r.get_active()))
        opts.add(auto)
        self.page.add(opts)
        self.stack.add_named(self.page, "list")

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.stack)
        self.set_child(view)
        self._rows = []
        self.checker.connect("changed", lambda *_: self.refresh())
        self.refresh()

    def on_shown(self):
        if not self.checker.last_checked and not self.checker.checking:
            self.checker.check()

    def refresh(self):
        c = self.checker
        self.check_btn.set_sensitive(not c.checking)
        for r in self._rows:
            self.group.remove(r)
        self._rows = []
        self.all_btn.set_visible(bool(c.updates))
        if c.checking and not c.updates:
            self.stack.set_visible_child_name("checking")
            return
        desc = f"Last checked {_ago(c.last_checked)}"
        if c.errors:
            desc += f" · {len(c.errors)} source(s) couldn't be reached"
        if not c.updates:
            self.empty.set_description(desc)
            self.stack.set_visible_child_name("empty")
            return
        self.group.set_title(tr('{value0} update{value1} available').format(value0=len(c.updates), value1='s' if len(c.updates) != 1 else ''))
        self.group.set_description(desc)
        for u in sorted(c.updates, key=lambda u: (u.kind, u.title.lower())):
            row = Adw.ActionRow(title=GLib.markup_escape_text(u.title),
                                subtitle=tr('{value0} · updated {value1} · installed {value2}').format(value0=u.kind, value1=_date(_ts(u.remote_changed)), value2=_date(u.installed_at)))
            if u.preview:
                pic = CoverPicture(64, 40, css_classes=["card"], valign=Gtk.Align.CENTER)
                load_texture_into(pic, url=u.preview)
                row.add_prefix(pic)
            else:
                row.add_prefix(Gtk.Image(icon_name=u.icon, pixel_size=24))
            if u.key in self.running:
                row.add_suffix(Adw.Spinner(valign=Gtk.Align.CENTER))
            else:
                b = Gtk.Button(label=tr("Update"), valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda _b, u=u: self.update(u))
                row.add_suffix(b)
            self.group.add(row)
            self._rows.append(row)
        self.stack.set_visible_child_name("list")

    def update(self, u, on_done=None):
        self.running.add(u.key)
        self.refresh()

        def done(ok, msg):
            self.running.discard(u.key)
            self.window.toast(f"Updated “{u.title}”" if ok else f"Couldn't update “{u.title}”: {msg}",
                              timeout=3 if ok else 6)
            self.refresh()
            if on_done:
                on_done()

        self.checker.apply(u, done)

    def update_all(self):
        queue = [u for u in self.checker.updates if u.key not in self.running]

        def next_one():
            if queue:
                self.update(queue.pop(0), next_one)

        next_one()
