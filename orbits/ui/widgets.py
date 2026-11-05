"""Shared widgets: wallpaper card and local-file thumbnail loading."""
import hashlib
from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, Graphene, Gsk, Gtk

from orbits import net
from orbits.config import THUMB_CACHE


def local_thumbnail(path, size=640):
    """Scaled-down texture for a local image, cached as PNG. Worker-thread safe."""
    p = Path(path)
    key = hashlib.sha1(f"{p}:{p.stat().st_mtime}:{size}".encode()).hexdigest()
    cached = THUMB_CACHE / f"local-{key}.png"
    if not cached.exists():
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), size, size, True)
        pb.savev(str(cached), "png", [], [])
    return Gdk.Texture.new_from_filename(str(cached))


class CoverPicture(Gtk.Widget):
    """Draws a paintable scaled to cover its allocation. Unlike Gtk.Picture, its natural size is fixed,
    so grids lay out by the requested card size rather than the image's pixel size."""

    def __init__(self, width=300, height=188, **kw):
        super().__init__(**kw)
        self._w, self._h = width, height
        self._paintable = None
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def set_paintable(self, paintable):
        self._paintable = paintable
        self.queue_draw()

    def get_paintable(self):
        return self._paintable

    def do_measure(self, orientation, for_size):
        n = self._w if orientation == Gtk.Orientation.HORIZONTAL else self._h
        return (min(n, 120), n, -1, -1)

    def do_snapshot(self, snapshot):
        p = self._paintable
        if p is None:
            return
        w, h = self.get_width(), self.get_height()
        iw, ih = p.get_intrinsic_width() or w, p.get_intrinsic_height() or h
        scale = max(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
        snapshot.push_clip(Graphene.Rect().init(0, 0, w, h))
        snapshot.save()
        snapshot.translate(Graphene.Point().init((w - dw) / 2, (h - dh) / 2))
        if isinstance(p, Gdk.Texture):
            snapshot.append_scaled_texture(p, Gsk.ScalingFilter.TRILINEAR, Graphene.Rect().init(0, 0, dw, dh))
        else:
            p.snapshot(snapshot, dw, dh)
        snapshot.restore()
        snapshot.pop()


def load_texture_into(picture: Gtk.Picture, url=None, path=None, spinner=None, size=640,
                      fallback_url=None):
    """Load an image asynchronously, optionally showing a thumbnail while it loads."""
    request = object()
    picture._texture_request = request
    state = {"quality": -1, "pending": 1 + bool(fallback_url and fallback_url != url)}

    def start(image_url, quality):
        def work():
            return net.cached_texture(image_url) if image_url else local_thumbnail(path, size)

        def done(tex, err):
            if picture._texture_request is not request:
                return
            state["pending"] -= 1
            if spinner and quality == 1:
                spinner.set_visible(False)
            if tex is not None and quality > state["quality"]:
                state["quality"] = quality
                picture.remove_css_class("load-failed")
                picture.set_paintable(tex)
            elif not state["pending"] and state["quality"] < 0:
                picture.set_paintable(None)
                picture.add_css_class("load-failed")

        net.run_async(work, done)

    if spinner:
        spinner.set_visible(True)
    if fallback_url and fallback_url != url:
        start(fallback_url, 0)
    start(url, 1)


class WallCard(Gtk.Overlay):
    """Rounded image card with caption, badge and hover quick actions."""

    def __init__(self, title, subtitle="", width=300, height=188):
        super().__init__(css_classes=["wall-card"], overflow=Gtk.Overflow.HIDDEN)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.picture = CoverPicture(width, height, hexpand=True, vexpand=True)
        self.set_child(self.picture)

        from gi.repository import Adw

        self.spinner = Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                   width_request=28, height_request=28)
        self.add_overlay(self.spinner)

        caption = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, css_classes=["caption"])
        self.title = Gtk.Label(label=title, xalign=0, ellipsize=3, css_classes=["title"])
        caption.append(self.title)
        self.subtitle = Gtk.Label(label=subtitle, xalign=0, ellipsize=3, css_classes=["subtitle"],
                                  visible=bool(subtitle))
        caption.append(self.subtitle)
        self.progress = Gtk.ProgressBar(visible=False, margin_top=6)
        caption.append(self.progress)
        self.add_overlay(caption)

        self.badge = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.START, css_classes=["badge"], visible=False)
        self.add_overlay(self.badge)

        self.actions = Gtk.Box(spacing=6, css_classes=["quick-actions"])
        self.revealer = Gtk.Revealer(child=self.actions, halign=Gtk.Align.END, valign=Gtk.Align.START,
                                     transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=150)
        self.add_overlay(self.revealer)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.revealer.set_reveal_child(True))
        motion.connect("leave", lambda *_: self.revealer.set_reveal_child(False))
        self.add_controller(motion)

    def add_action_button(self, icon, tooltip, callback):
        b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, css_classes=["circular"])
        b.connect("clicked", lambda *_: callback())
        self.actions.append(b)
        return b

    def set_badge(self, text, accent=False):
        self.badge.set_visible(bool(text))
        self.badge.set_label(text or "")
        if accent:
            self.badge.add_css_class("accent")
        else:
            self.badge.remove_css_class("accent")

    def set_progress(self, frac):
        if frac is None:
            self.progress.set_visible(False)
        else:
            self.progress.set_visible(True)
            self.progress.set_fraction(frac)

    def load(self, url=None, path=None):
        load_texture_into(self.picture, url=url, path=path, spinner=self.spinner)


def make_grid():
    return Gtk.FlowBox(css_classes=["wall-grid"], homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                       min_children_per_line=1, max_children_per_line=8, column_spacing=18, row_spacing=18,
                       valign=Gtk.Align.START, activate_on_single_click=True,
                       margin_start=24, margin_end=24, margin_top=12, margin_bottom=32)


def status_page(icon, title, description="", button=None, callback=None):
    from gi.repository import Adw

    page = Adw.StatusPage(icon_name=icon, title=title, description=description, vexpand=True)
    if button:
        b = Gtk.Button(label=button, css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: callback())
        page.set_child(b)
    return page


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_uri(widget, uri):
    Gtk.UriLauncher.new(uri).launch(widget.get_root(), None, None)

