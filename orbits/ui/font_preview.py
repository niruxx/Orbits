"""Raster font previews with request ordering and no GUI font registration."""
from gi.repository import Gdk, GLib
from orbits import net
from orbits.font_safety import render_font


def load(picture, path, text, size, variations=None, features=None):
    generation = getattr(picture, '_font_generation', 0) + 1
    picture._font_generation = generation
    def done(data, error):
        if picture._font_generation != generation:
            return
        if error:
            picture.set_paintable(None)
            picture.set_tooltip_text(str(error))
        else:
            picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
            picture.set_tooltip_text(None)
    def start():
        if picture._font_generation == generation:
            net.run_async(lambda: render_font(path, text[:2048], size, variations=variations, features=features), done)
        return False
    GLib.timeout_add(150, start)
