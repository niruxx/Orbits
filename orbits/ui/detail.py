"""Detail dialog for an online wallpaper."""
from gi.repository import Adw, Gtk

from orbits.ui.widgets import CoverPicture, human_size, load_texture_into, open_uri


class DetailDialog(Adw.Dialog):
    def __init__(self, page, item):
        super().__init__(title=item.title, content_width=940, content_height=760)
        self.page, self.item, self.lib = page, item, page.lib

        hero = Gtk.Overlay(css_classes=["detail-hero"], overflow=Gtk.Overflow.HIDDEN, height_request=440)
        pic = CoverPicture(860, 440, hexpand=True)
        hero.set_child(pic)
        spinner = Adw.Spinner(width_request=40, height_request=40, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        hero.add_overlay(spinner)
        # show the thumbnail immediately, then upgrade to the full preview
        load_texture_into(pic, url=item.thumb_url or item.preview_url)
        if item.preview_url and item.preview_url != item.thumb_url:
            load_texture_into(pic, url=item.preview_url, spinner=spinner)
        else:
            spinner.set_visible(False)

        title = Gtk.Label(label=item.title, xalign=0, wrap=True, css_classes=["detail-title"])
        meta = " · ".join(x for x in [item.author and f"by {item.author}", item.resolution, item.stats] if x)
        meta_lbl = Gtk.Label(label=meta, xalign=0, wrap=True, css_classes=["dim-label"])

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        info.append(title)
        info.append(meta_lbl)
        if item.colors:
            sw = Gtk.Box(spacing=6, margin_top=8)
            for c in item.colors[:6]:
                dot = Gtk.Box(css_classes=["swatch"], tooltip_text=c)
                prov = Gtk.CssProvider()
                prov.load_from_string(f"box {{ background-color: {c}; }}")
                dot.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
                sw.append(dot)
            info.append(sw)

        self.buttons = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        top = Gtk.Box(spacing=18, margin_top=18)
        top.append(info)
        top.append(self.buttons)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=24, margin_end=24,
                          margin_bottom=24)
        content.append(hero)
        content.append(top)
        if item.description:
            content.append(Gtk.Label(label=item.description[:900], xalign=0, wrap=True, margin_top=10,
                                     selectable=True))
        if len(item.downloads) > 1:
            files = Adw.PreferencesGroup(title="Files", margin_top=18,
                                         description="This item offers several downloads; install the one you want.")
            for d in item.downloads:
                row = Adw.ActionRow(title=d.name, subtitle=human_size(d.size) if d.size else "")
                b = Gtk.Button(icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER,
                               css_classes=["flat"], tooltip_text="Install this file")
                b.connect("clicked", lambda _b, d=d: self._install(d))
                row.add_suffix(b)
                files.add(row)
            content.append(files)

        header = Adw.HeaderBar()
        if item.page_url:
            web = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open in Browser")
            web.connect("clicked", lambda *_: open_uri(self, item.page_url))
            header.pack_start(web)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=content,
                                            propagate_natural_height=True))
        self.set_child(view)

        self._handler = self.lib.connect("changed", lambda *_: self._sync())
        self.connect("closed", lambda *_: self.lib.disconnect(self._handler))
        self._sync()

    def _btn(self, label, css, cb, icon=None):
        b = Gtk.Button(css_classes=["pill", *css])
        if icon:
            b.set_child(Adw.ButtonContent(label=label, icon_name=icon))
        else:
            b.set_label(label)
        b.connect("clicked", lambda *_: cb())
        self.buttons.append(b)
        return b

    def _sync(self):
        while (c := self.buttons.get_first_child()):
            self.buttons.remove(c)
        key = self.item.key
        if key in self.lib.busy:
            bar = Gtk.ProgressBar(fraction=self.lib.busy[key], width_request=180, valign=Gtk.Align.CENTER,
                                  show_text=True, text="Installing…")
            self.buttons.append(bar)
            return
        if self.lib.is_installed(key):
            entry = self.lib.items[key]
            self._btn("Uninstall", ["destructive-action"], lambda: self.lib.uninstall(key), "user-trash-symbolic")
            if not self.page.wallpaper_extras:
                self._btn(self.page.apply_label, ["suggested-action"], lambda: self.page._apply(entry),
                          self.page.apply_icon)
                return
            menu = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["circular"], valign=Gtk.Align.CENTER,
                                  tooltip_text="More")
            pop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for label, cb in [
                ("Set as Lock Screen", lambda: self.lib.apply(entry["files"][0], desktop=False, lockscreen=True)),
                ("Use for Login Screen", lambda: (self.close(),
                                                  self.page.window.use_for_login_screen(entry["files"][0]["light"]))),
            ]:
                mb = Gtk.Button(label=label, css_classes=["flat"])
                mb.connect("clicked", lambda _b, cb=cb: (menu.popdown(), cb()))
                pop.append(mb)
            menu.set_popover(Gtk.Popover(child=pop))
            self.buttons.append(menu)
            self._btn("Set as Wallpaper", ["suggested-action"], lambda: self.page._apply(entry),
                      "preferences-desktop-wallpaper-symbolic")
        else:
            self._btn("Install", [], lambda: self._install(None), "folder-download-symbolic")
            self._btn(self.page.apply_label, ["suggested-action"], lambda: self.page.install(self.item, apply=True),
                      self.page.apply_icon)

    def _install(self, download):
        if self.lib.is_installed(self.item.key):
            self.lib.uninstall(self.item.key)
        self.page.install(self.item, download)


