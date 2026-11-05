"""Wallpaper editing and library tools exposed from the Library page."""
from orbits.i18n import _ as tr
from pathlib import Path
import tempfile

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from orbits import net, rotation
from orbits.wallpaper_tools import compose_monitors, duplicate_groups, edit_image
from orbits.ui.widgets import load_texture_into


def action(group, title, callback, subtitle=""):
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    button = Gtk.Button(label=tr("Open"), valign=Gtk.Align.CENTER)
    button.connect("clicked", lambda *_: callback())
    row.add_suffix(button)
    group.add(row)
    return row


def entry(group, title, text=""):
    row = Adw.EntryRow(title=title, text=str(text))
    group.add(row)
    return row


def spin(group, title, value, lower, upper, step=1, digits=0):
    row = Adw.SpinRow(title=title, adjustment=Gtk.Adjustment(value=value, lower=lower, upper=upper,
                                                          step_increment=step, page_increment=step * 10), digits=digits)
    group.add(row)
    return row


def shell(dialog, body, save=None, label="Save"):
    view = Adw.ToolbarView()
    header = Adw.HeaderBar()
    if save:
        button = Gtk.Button(label=label, css_classes=["suggested-action"])
        button.connect("clicked", lambda *_: save())
        header.pack_end(button)
    view.add_top_bar(header)
    view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       child=Adw.Clamp(maximum_size=800, child=body)))
    dialog.set_child(view)


def body_box():
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=24, margin_end=24,
                   margin_top=12, margin_bottom=24)


class WallpaperToolsDialog(Adw.Dialog):
    def __init__(self, page):
        super().__init__(title=tr("Wallpaper Tools"), content_width=720, content_height=600)
        self.page = page
        body = body_box()
        group = Adw.PreferencesGroup(title=tr("Library Tools"))
        action(group, "Wallpaper Rotation", lambda: RotationDialog(page).present(page.window),
               "Rotate a collection, store search or Bing Daily while Orbits is closed")
        action(group, "Time-of-Day Wallpaper", lambda: SlideshowDialog(page).present(page.window),
               "Choose images and the time each starts; import HEIC files with the Library Import button")
        action(group, "Per-Monitor Wallpapers", lambda: MonitorDialog(page).present(page.window),
               "Compose one spanned wallpaper using monitor positions")
        action(group, "Find Exact Duplicates", self.duplicates, "Compare image contents; files are never deleted automatically")
        body.append(group)
        shell(self, body)

    def duplicates(self):
        self.page.window.toast(tr("Checking wallpaper contents…"))
        snapshot = list(self.page.lib.items.values())
        def done(groups, error):
            if error:
                self.page.window.toast(str(error), timeout=6)
                return
            lines = []
            for group in groups:
                lines.append("\n".join(f"{self.page.lib.items.get(key, {}).get('title', key)}: {path}" for key, path in group))
            dialog = Adw.AlertDialog(heading=tr('{value0} Duplicate Groups').format(value0=len(groups)), body="\n\n".join(lines) or "No exact duplicate images found.")
            dialog.add_response("close", tr("Close"))
            dialog.present(self)
        net.run_async(lambda: duplicate_groups(snapshot), done)


class RotationDialog(Adw.Dialog):
    def __init__(self, page):
        super().__init__(title=tr("Wallpaper Rotation"), content_width=640, content_height=650)
        self.page, self.config = page, rotation.load_config()
        body = body_box()
        group = Adw.PreferencesGroup(title=tr("Schedule"), description=tr("Requires systemd user services. Rotation runs while Orbits is closed."))
        self.enabled = Adw.SwitchRow(title=tr("Enable Rotation"), active=bool(self.config.get("enabled")))
        group.add(self.enabled)
        self.daily = Adw.SwitchRow(title=tr("Daily at Midnight"), active=bool(self.config.get("daily")))
        group.add(self.daily)
        self.minutes = spin(group, "Interval in Minutes", self.config.get("minutes", 30), 1, 10080)
        self.modes = ["library", "bing", "search"]
        self.mode = Adw.ComboRow(title=tr("Wallpaper Selection"), model=Gtk.StringList.new(["Library", "Bing Daily", "Store Search"]))
        self.mode.set_selected(self.modes.index(self.config.get("mode", "library")))
        group.add(self.mode)
        body.append(group)
        selection = Adw.PreferencesGroup(title=tr("Selection"))
        self.collection = entry(selection, "Collection (blank for all)", self.config.get("collection", ""))
        self.favorite = Adw.SwitchRow(title=tr("Favorites Only"), active=bool(self.config.get("favorites")))
        selection.add(self.favorite)
        from orbits.sources import SOURCES
        self.sources = SOURCES
        self.source = Adw.ComboRow(title=tr("Search Source"), model=Gtk.StringList.new([s.name for s in SOURCES]))
        self.source.set_selected(next((i for i,s in enumerate(SOURCES) if s.id == self.config.get("source", "wallhaven")), 0))
        selection.add(self.source)
        self.query = entry(selection, "Store Search", self.config.get("query", ""))
        self.lock = Adw.SwitchRow(title=tr("Also Set Lock Screen"), active=bool(self.config.get("lockscreen")))
        selection.add(self.lock)
        body.append(selection)
        action(selection, "Rotate Now", self.rotate)
        shell(self, body, self.save)

    def values(self):
        return {"enabled": self.enabled.get_active(), "daily": self.daily.get_active(),
                "minutes": int(self.minutes.get_value()), "mode": self.modes[self.mode.get_selected()],
                "collection": self.collection.get_text().strip(), "favorites": self.favorite.get_active(),
                "source": self.sources[self.source.get_selected()].id, "query": self.query.get_text().strip(),
                "lockscreen": self.lock.get_active()}

    def save(self):
        config = self.values()
        def done(_result, error):
            self.page.window.toast(str(error) if error else "Wallpaper schedule saved", timeout=6)
            if not error:
                self.close()
        net.run_async(lambda: rotation.configure(config), done)

    def rotate(self):
        # Gio.Settings and library changes belong on the main loop; only downloads run in workers.
        config = self.values()
        if config["mode"] == "library":
            import random
            files = rotation.candidates(self.page.lib.items.values(), config)
            if not files:
                self.page.window.toast(tr("No wallpapers match this selection"))
                return
            current = self.page.lib.current_uri()
            files = [record for record in files if Path(record["light"]).as_uri() != current] or files
            self.page.lib.apply(random.choice(files), lockscreen=config["lockscreen"])
            self.page.window.toast(tr("Wallpaper rotated"))
        else:
            source = next(s for s in self.sources if s.id == ("bing" if config["mode"] == "bing" else config["source"]))
            def fetched(result, error):
                if error or not result[0]:
                    self.page.window.toast(str(error) if error else "No wallpapers found", timeout=6)
                    return
                import random
                item = result[0][0] if config["mode"] == "bing" else random.choice(result[0])
                def installed(record, err):
                    if err:
                        self.page.window.toast(str(err), timeout=6)
                    else:
                        self.page.lib.apply(record["files"][0], lockscreen=config["lockscreen"])
                if self.page.lib.is_installed(item.key):
                    installed(self.page.lib.items[item.key], None)
                else:
                    self.page.lib.install(item, None, installed)
            net.run_async(source.fetch, fetched, config["query"], "", "", 1)


class SlideshowDialog(Adw.Dialog):
    def __init__(self, page):
        super().__init__(title=tr("Time-of-Day Wallpaper"), content_width=640, content_height=650)
        self.page = page
        self.files = [(e["title"] + " · " + rec.get("name", ""), rec["light"]) for e in page.lib.sorted_items()
                      for rec in e["files"] if Path(rec["light"]).suffix != ".xml"]
        body = body_box()
        self.group = Adw.PreferencesGroup(title=tr("Frames"), description=tr("Start with 00:00. Each frame lasts until the next; the schedule repeats daily."))
        self.title_entry = entry(self.group, "Name", "Daily Wallpaper")
        self.fade = spin(self.group, "Transition Seconds", 5, 0, 300)
        self.frames = []
        for hour in (0, 6, 12, 18):
            self.add_frame(hour)
        action(self.group, "Add Frame", lambda: self.add_frame(0))
        body.append(self.group)
        shell(self, body, self.save, "Create")

    def add_frame(self, hour):
        if not self.files:
            return
        row = Adw.ComboRow(title=tr('Frame {value0}').format(value0=len(self.frames) + 1), model=Gtk.StringList.new([name for name,_ in self.files]))
        time = Gtk.Entry(text=f"{hour:02d}:00", width_chars=5, max_width_chars=5, valign=Gtk.Align.CENTER)
        row.add_suffix(time)
        row.set_selected(min(len(self.frames), len(self.files)-1))
        self.frames.append((row, time))
        self.group.add(row)

    def save(self):
        try:
            frames = []
            for row, time in self.frames:
                hour, minute = map(int, time.get_text().split(":"))
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError("Use times from 00:00 to 23:59")
                frames.append((hour * 3600 + minute * 60, self.files[row.get_selected()][1]))
            entry_ = self.page.lib.create_slideshow(frames, self.title_entry.get_text().strip() or "Daily Wallpaper", self.fade.get_value())
            self.page.apply(entry_, entry_["files"][0])
            self.close()
        except Exception as error:
            self.page.window.toast(str(error), timeout=6)


class MonitorDialog(Adw.Dialog):
    def __init__(self, page):
        super().__init__(title=tr("Per-Monitor Wallpapers"), content_width=650, content_height=650)
        self.page = page
        self.files = [(e["title"], rec.get("preview") or rec["light"]) for e in page.lib.sorted_items()
                      for rec in e["files"] if Path(rec.get("preview") or rec["light"]).suffix != ".xml"]
        body = body_box()
        self.rows = []
        group = Adw.PreferencesGroup(title=tr("Monitor Layout"), description=tr("Use logical positions from Displays settings. Mirrored monitors share the same wallpaper."))
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            geometry = monitor.get_geometry()
            row = Adw.ComboRow(title=monitor.get_model() or f"Monitor {i+1}", model=Gtk.StringList.new([name for name,_ in self.files]))
            group.add(row)
            coords = []
            for title, value, lower, upper in (("X", geometry.x, -32768, 32768), ("Y", geometry.y, -32768, 32768),
                                               ("Width", geometry.width, 1, 16384), ("Height", geometry.height, 1, 16384)):
                coords.append(spin(group, f"Monitor {i+1} · {title}", value, lower, upper))
            self.rows.append((row, coords))
        body.append(group)
        shell(self, body, self.save, "Compose & Apply")

    def save(self):
        if not self.files:
            self.page.window.toast(tr("Import some wallpapers first"))
            return
        assignments = []
        for row, coords in self.rows:
            a = dict(zip(("x", "y", "width", "height"), (int(c.get_value()) for c in coords)))
            a["path"] = self.files[row.get_selected()][1]
            assignments.append(a)
        def work():
            with tempfile.TemporaryDirectory(prefix="orbits-monitors-") as tmp:
                path = compose_monitors(assignments, Path(tmp) / "monitors.png")
                return path.read_bytes()
        def done(data, error):
            if error:
                self.page.window.toast(str(error), timeout=6)
                return
            with tempfile.TemporaryDirectory(prefix="orbits-monitors-") as tmp:
                path = Path(tmp) / "monitors.png"
                path.write_bytes(data)
                entry_ = self.page.lib.add_generated(path, "Per-Monitor Wallpaper", options="spanned")
            self.page.apply(entry_, entry_["files"][0])
            self.close()
        net.run_async(work, done)


class WallpaperEditor(Adw.Dialog):
    def __init__(self, page, record):
        super().__init__(title=tr("Edit Wallpaper"), content_width=760, content_height=760)
        self.page, self.record = page, record
        self.path = record.get("preview") or record["light"]
        self._tmp = tempfile.TemporaryDirectory(prefix="orbits-edit-")
        self._generation = 0
        self._closed = False
        self.connect("closed", self.closed)
        body = body_box()
        self.picture = Gtk.Picture(width_request=320, height_request=320, hexpand=True,
                                   content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        body.append(self.picture)
        load_texture_into(self.picture, path=self.path)
        group = Adw.PreferencesGroup(title=tr("Effects"), description=tr("A new image is saved to your Library; the original stays available."))
        self.blur = spin(group, "Blur (Pixels)", 0, 0, 100)
        self.dim = spin(group, "Dim (%)", 0, 0, 100)
        self.tint_amount = spin(group, "Tint (%)", 0, 0, 100)
        row = Adw.ActionRow(title=tr("Tint Color"))
        self.tint = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), rgba=Gdk.RGBA(red=0,green=0,blue=0,alpha=1), valign=Gtk.Align.CENTER)
        row.add_suffix(self.tint)
        group.add(row)
        self.crops = [spin(group, label, value, 0, 100) for label,value in (("Crop Left (%)",0),("Crop Top (%)",0),("Crop Right (%)",100),("Crop Bottom (%)",100))]
        for control in [self.blur, self.dim, self.tint_amount, *self.crops]:
            control.connect("notify::value", lambda *_: self.preview())
        self.tint.connect("notify::rgba", lambda *_: self.preview())
        body.append(group)
        shell(self, body, self.save, "Save & Apply")

    def closed(self, *_):
        self._closed = True
        self._tmp.cleanup()

    def options(self):
        c = self.tint.get_rgba()
        return {"crop": tuple(r.get_value()/100 for r in self.crops), "blur": self.blur.get_value(),
                "dim": self.dim.get_value()/100, "tint_amount": self.tint_amount.get_value()/100,
                "tint": "#{:02x}{:02x}{:02x}".format(*(round(v*255) for v in (c.red,c.green,c.blue)))}

    def preview(self):
        self._generation += 1
        generation, options = self._generation, self.options()
        def start():
            if self._closed or generation != self._generation:
                return False
            def work():
                with tempfile.TemporaryDirectory(prefix="orbits-preview-") as tmp:
                    path = edit_image(self.path, Path(tmp)/"preview.png", **options)
                    return path.read_bytes()
            def done(data, error):
                if self._closed or generation != self._generation or error:
                    return
                path = Path(self._tmp.name)/f"preview-{generation}.png"
                path.write_bytes(data)
                load_texture_into(self.picture, path=str(path))
            net.run_async(work, done)
            return False
        GLib.timeout_add(200, start)

    def save(self):
        options = self.options()
        def work():
            with tempfile.TemporaryDirectory(prefix="orbits-edited-") as tmp:
                path = edit_image(self.path, Path(tmp)/"edited.png", **options)
                return path.read_bytes()
        def done(data, error):
            if error:
                self.page.window.toast(str(error), timeout=6)
                return
            with tempfile.TemporaryDirectory(prefix="orbits-edited-") as tmp:
                path = Path(tmp)/"edited.png"
                path.write_bytes(data)
                entry_ = self.page.lib.add_generated(path, "Edited Wallpaper")
            self.page.apply(entry_, entry_["files"][0])
            self.close()
        net.run_async(work, done)
