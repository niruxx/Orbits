"""Current-session transfer queue with pause/resume and cancellation."""
from gi.repository import Adw, GLib, Gtk
from orbits.downloads import get_manager, subscribe
from orbits.i18n import _


class DownloadsPage(Adw.Bin):
    title = _("Downloads")

    def __init__(self, window):
        super().__init__()
        self.manager = get_manager()
        self.pending = False
        self.list = Gtk.ListBox(css_classes=['boxed-list'], selection_mode=Gtk.SelectionMode.NONE)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=24, margin_end=24, margin_top=18, margin_bottom=24)
        box.append(Gtk.Label(label=_("Pause to resume later. Cancel keeps partial data for the next Install attempt."),
                             wrap=True, xalign=0))
        box.append(self.list)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=self.title))
        clear = Gtk.Button(label=_("Clear Finished"), tooltip_text=_("Remove finished transfer history and cached archives; keep partial files"))
        def clear_finished(*_args):
            try:
                self.manager.clear_finished()
            except Exception as error:
                window.toast(str(error), timeout=6)
        clear.connect('clicked', clear_finished)
        header.pack_end(clear)
        view.add_top_bar(header)
        self.set_child(view)
        self.unsubscribe = subscribe(self.schedule_refresh)
        window.connect('close-request', lambda *_: self.unsubscribe())
        self.refresh()

    def schedule_refresh(self):
        if not self.pending:
            self.pending = True
            GLib.timeout_add(150, self.refresh)

    def refresh(self):
        self.pending = False
        self.list.remove_all()
        for job in list(self.manager.jobs.values())[::-1]:
            percent = f' · {round(100*job.received/job.total)}%' if job.total else ''
            states = {'queued':_('queued'), 'downloading':_('downloading'), 'paused':_('paused'),
                      'cancelled':_('cancelled'), 'completed':_('completed'), 'failed':_('failed')}
            row = Adw.ActionRow(title=job.label, subtitle=states[job.state]+percent+(' · '+job.error if job.error else ''),
                                use_markup=False)
            if job.state in ('queued','downloading','paused'):
                paused = job.state == 'paused'
                button = Gtk.Button(icon_name='media-playback-start-symbolic' if paused else 'media-playback-pause-symbolic',
                                    tooltip_text=_("Resume") if paused else _("Pause"), valign=Gtk.Align.CENTER)
                button.connect('clicked', lambda _b, item=job: self.manager.resume(item) if item.state=='paused' else self.manager.pause(item))
                row.add_suffix(button)
                cancel = Gtk.Button(icon_name='process-stop-symbolic', tooltip_text=_("Cancel"), valign=Gtk.Align.CENTER)
                cancel.connect('clicked', lambda _b, item=job:self.manager.cancel(item))
                row.add_suffix(cancel)
            self.list.append(row)
        return False
