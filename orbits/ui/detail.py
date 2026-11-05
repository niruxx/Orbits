"""Detail dialog for an online wallpaper."""
from orbits.i18n import _ as tr
from gi.repository import Adw, Gtk

from orbits import net
from orbits.theme_sources import web_url

from orbits.ui.widgets import CoverPicture, human_size, load_texture_into, open_uri


class DetailDialog(Adw.Dialog):
    def __init__(self, page, item):
        super().__init__(title=item.title, content_width=940, content_height=760)
        self.page, self.item, self.lib = page, item, page.lib
        self._closed = False
        self._comment_page = 0
        self._comments_busy = False
        self._seen_comments = set()

        hero = Gtk.Overlay(css_classes=["detail-hero"], overflow=Gtk.Overflow.HIDDEN, height_request=440)
        pic = CoverPicture(860, 440, hexpand=True)
        hero.set_child(pic)
        spinner = Adw.Spinner(width_request=40, height_request=40, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        hero.add_overlay(spinner)
        load_texture_into(pic, url=item.preview_url or item.thumb_url,
                          fallback_url=item.thumb_url, spinner=spinner)

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
        if callable(getattr(page.source, "fetch_details", None)):
            self._store_content(content)
        if len(item.downloads) > 1:
            files = Adw.PreferencesGroup(title=tr("Files"), margin_top=18,
                                         description=tr("This item offers several downloads; install the one you want."))
            for d in item.downloads:
                row = Adw.ActionRow(title=d.name, subtitle=human_size(d.size) if d.size else "", use_markup=False)
                b = Gtk.Button(icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER,
                               css_classes=["flat"], tooltip_text=tr("Install this file"))
                b.connect("clicked", lambda _b, d=d: self._install(d))
                row.add_suffix(b)
                files.add(row)
            content.append(files)

        header = Adw.HeaderBar()
        source_url = web_url(item.page_url)
        if source_url:
            if not getattr(page, "wallpaper_extras", True):
                web = Gtk.Button(child=Adw.ButtonContent(label=tr("Open Source"), icon_name="external-link-symbolic"),
                                 tooltip_text=source_url)
            else:
                web = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text=tr("Open in Browser"))
            web.connect("clicked", lambda *_: open_uri(self, source_url))
            header.pack_start(web)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=content,
                                            propagate_natural_height=True))
        self.set_child(view)

        self._handler = self.lib.connect("changed", lambda *_: self._sync())
        self.connect("closed", self._on_closed)
        self._sync()

    def _on_closed(self, *_):
        self._closed = True
        self.lib.disconnect(self._handler)

    def _store_content(self, content):
        self.store_group = Adw.PreferencesGroup(title=tr("Store Details"), margin_top=18)
        self.store_status = Adw.ActionRow(title=tr("Loading ratings and changelog…"))
        self.store_group.add(self.store_status)
        content.append(self.store_group)
        self.comment_group = Adw.PreferencesGroup(title=tr("Comments"), margin_top=18)
        self.comment_status = Adw.ActionRow(title=tr("Loading comments…"))
        self.comment_group.add(self.comment_status)
        self.comment_more = Gtk.Button(label=tr("Load More Comments"), halign=Gtk.Align.CENTER, visible=False)
        self.comment_more.connect("clicked", lambda *_: self._load_comments())
        content.append(self.comment_group)
        content.append(self.comment_more)
        def done(data, error):
            if self._closed:
                return
            if error:
                self.store_status.set_title(tr("Store details unavailable"))
                self.store_status.set_subtitle(str(error))
                if not hasattr(self, "_store_retry"):
                    self._store_retry = Gtk.Button(label=tr("Retry"), valign=Gtk.Align.CENTER)
                    self._store_retry.connect("clicked", lambda *_: net.run_async(self.page.source.fetch_details, done, self.item.id))
                    self.store_status.add_suffix(self._store_retry)
                self._store_retry.set_visible(True)
                return
            if hasattr(self, "_store_retry"):
                self._store_retry.set_visible(False)
            self.store_status.set_title(f"Rating: {data['score']:g}/100" if data["score"] is not None else "No rating available")
            self.store_status.set_subtitle(" · ".join(part for part in (data["version"], data["changed"]) if part))
            row = Adw.ExpanderRow(title=tr("Changelog"), subtitle=tr("Expand to read version notes"))
            row.add_row(Adw.ActionRow(title=tr("Release notes"), subtitle=data["changelog"] or "The author has not supplied a changelog.",
                                     subtitle_lines=0, subtitle_selectable=True, use_markup=False))
            self.store_group.add(row)
        net.run_async(self.page.source.fetch_details, done, self.item.id)
        self._load_comments()

    def _load_comments(self):
        if self._comments_busy or self._closed:
            return
        self._comments_busy = True
        self.comment_more.set_sensitive(False)
        def done(result, error):
            self._comments_busy = False
            if self._closed:
                return
            self.comment_more.set_sensitive(True)
            if error:
                self.comment_status.set_title(tr("Comments unavailable"))
                self.comment_status.set_subtitle(str(error))
                self.comment_more.set_label(tr("Retry Comments"))
                self.comment_more.set_visible(True)
                return
            records, more = result
            self.comment_status.set_visible(False)
            if not records and self._comment_page == 0:
                self.comment_status.set_title(tr("No comments yet"))
                self.comment_status.set_visible(True)
            for record in records:
                identity = record["id"] or (record["author"],record["date"],record["text"])
                if identity in self._seen_comments:
                    continue
                self._seen_comments.add(identity)
                title = ("↳ " if record["depth"] else "") + record["author"]
                if record["subject"]:
                    title += " · " + record["subject"]
                row = Adw.ActionRow(title=title, subtitle=record["text"], subtitle_lines=0,
                                    subtitle_selectable=True, use_markup=False)
                row.set_tooltip_text(record["date"])
                self.comment_group.add(row)
            self._comment_page += 1
            self.comment_more.set_label(tr("Load More Comments"))
            self.comment_more.set_visible(more)
        net.run_async(self.page.source.fetch_comments, done, self.item.id, self._comment_page)

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
                                  tooltip_text=tr("More"))
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


