"""Community Looks browsing and schedule configuration."""
from orbits.i18n import _ as tr
import json
from pathlib import Path
import shutil
import tempfile

from gi.repository import Adw, Gio, GLib, Gtk

from orbits import look_gallery, look_schedule, net
from orbits.config import CONFIG_DIR
from orbits.ui.widgets import CoverPicture, load_texture_into


def body_box():
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=18,margin_start=24,margin_end=24,margin_top=12,margin_bottom=24)


def shell(dialog,body,save=None):
    view=Adw.ToolbarView();header=Adw.HeaderBar()
    if save:
        button=Gtk.Button(label=tr("Save Schedule"),css_classes=["suggested-action"])
        button.connect("clicked",lambda *_:save());header.pack_end(button)
    view.add_top_bar(header)
    view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,child=body))
    dialog.set_child(view)


class LookSchedule(Adw.Dialog):
    def __init__(self,page):
        super().__init__(title=tr("Schedule Looks"),content_width=680,content_height=760)
        self.page=page;self.config=look_schedule.load_config()
        saved=page.store.looks()
        self.ids=[""]+[Path(look["_dir"]).name for look in saved]
        self.names=["No Change"]+[look["name"] for look in saved]
        body=body_box()
        group=Adw.PreferencesGroup(title=tr("Schedule"),description=tr("Uses systemd user services, including while Orbits is closed. Applies desktop settings only; login changes always require the login editor."))
        self.enabled=Adw.SwitchRow(title=tr("Enable Scheduled Looks"),active=bool(self.config.get("enabled")))
        group.add(self.enabled)
        self.mode=Adw.ComboRow(title=tr("Schedule Type"),model=Gtk.StringList.new(["Day / Night","By Weekday"]))
        self.mode.set_selected(1 if self.config.get("mode")=="weekday" else 0)
        group.add(self.mode);body.append(group)
        self.daynight=Adw.PreferencesGroup(title=tr("Daily Looks"),description=tr("Uses your desktop's local time. Start times may cross midnight."))
        self.day=self.choice(self.daynight,"Day Look",self.config.get("day"))
        self.day_start=Adw.EntryRow(title=tr("Day Starts (HH:MM)"),text=self.config.get("day_start","07:00"));self.daynight.add(self.day_start)
        self.night=self.choice(self.daynight,"Night Look",self.config.get("night"))
        self.night_start=Adw.EntryRow(title=tr("Night Starts (HH:MM)"),text=self.config.get("night_start","19:00"));self.daynight.add(self.night_start)
        body.append(self.daynight)
        self.weekday_group=Adw.PreferencesGroup(title=tr("Weekday Looks"))
        self.weekdays={}
        for day,label in enumerate(("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")):
            self.weekdays[str(day)]=self.choice(self.weekday_group,label,self.config.get("weekdays",{}).get(str(day)))
        body.append(self.weekday_group)
        self.mode.connect("notify::selected",lambda *_:self.visibility());self.visibility()
        shell(self,body,self.save)

    def choice(self,group,title,id_):
        row=Adw.ComboRow(title=title,model=Gtk.StringList.new(self.names))
        row.set_selected(self.ids.index(id_) if id_ in self.ids else 0)
        group.add(row);return row

    def visibility(self):
        self.daynight.set_visible(self.mode.get_selected()==0)
        self.weekday_group.set_visible(self.mode.get_selected()==1)

    def save(self):
        config={"enabled":self.enabled.get_active(),"mode":"daynight" if self.mode.get_selected()==0 else "weekday",
                "day":self.ids[self.day.get_selected()],"night":self.ids[self.night.get_selected()],
                "day_start":self.day_start.get_text(),"night_start":self.night_start.get_text(),
                "weekdays":{day:self.ids[row.get_selected()] for day,row in self.weekdays.items()}}
        def done(_result,error):
            self.page.window.toast(str(error) if error else "Look schedule saved",timeout=6)
            if not error:self.close()
        net.run_async(lambda:look_schedule.configure(config),done)


class LooksGallery(Adw.Dialog):
    def __init__(self,page):
        super().__init__(title=tr("Community Looks"),content_width=980,content_height=800)
        self.page=page;self.generation=0;self.closed=False
        self.connect("closed",lambda *_:setattr(self,"closed",True))
        body=body_box()
        group=Adw.PreferencesGroup(title=tr("Community Catalogue"),description=tr("Open a shared index.json or enter its HTTPS URL. Preview and import Looks here; apply them separately from your saved collection."))
        try:location=json.loads((CONFIG_DIR/"looks-gallery.json").read_text()).get("location","")
        except (OSError,ValueError):location=""
        self.location=Adw.EntryRow(title=tr("Catalogue URL or Local File"),text=location)
        group.add(self.location)
        controls=Gtk.Box(spacing=8)
        load=Gtk.Button(label=tr("Load Catalogue"),css_classes=["suggested-action"])
        load.connect("clicked",lambda *_:self.load())
        choose=Gtk.Button(label=tr("Open File…"));choose.connect("clicked",lambda *_:self.choose())
        controls.append(load);controls.append(choose);group.add(controls)
        body.append(group)
        self.status=Gtk.Label(label=tr("No public catalogue is configured. Open a catalogue shared by another Orbits user."),wrap=True,xalign=0)
        body.append(self.status)
        self.grid=Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,max_children_per_line=3,column_spacing=16,row_spacing=16,homogeneous=True)
        body.append(self.grid);shell(self,body)
        if location:self.load()

    def choose(self):
        filter_=Gtk.FileFilter(name="Orbits Catalogue JSON");filter_.add_pattern("*.json")
        filters=Gio.ListStore.new(Gtk.FileFilter);filters.append(filter_)
        dialog=Gtk.FileDialog(title=tr("Open Community Catalogue"),filters=filters)
        def done(d,result):
            try:file=d.open_finish(result)
            except GLib.Error:return
            if file and file.get_path():self.location.set_text(file.get_path());self.load()
        dialog.open(self.page.window,None,done)

    def load(self):
        location=self.location.get_text().strip()
        if not location:return
        self.generation+=1;generation=self.generation
        self.status.set_label(tr("Loading community Looks…"))
        def done(records,error):
            if self.closed or generation!=self.generation:return
            self.grid.remove_all()
            if error:self.status.set_label(str(error));return
            (CONFIG_DIR/"looks-gallery.json").write_text(json.dumps({"location":location}))
            self.status.set_label(f"{len(records)} Looks available" if records else "This catalogue has no Looks yet")
            for record in records:self.grid.append(self.card(record))
        net.run_async(lambda:look_gallery.load_catalog(location),done)

    def card(self,record):
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8,css_classes=["theme-card"],width_request=280)
        picture=CoverPicture(280,170,hexpand=True)
        box.append(picture)
        screenshot=record["screenshot"]
        if screenshot:load_texture_into(picture,**({"url":screenshot} if screenshot.startswith("https://") else {"path":screenshot}))
        box.append(Gtk.Label(label=record["name"],wrap=True,css_classes=["heading"]))
        box.append(Gtk.Label(label=record["author"],wrap=True,css_classes=["dim-label"]))
        controls=Gtk.Box(spacing=8,halign=Gtk.Align.CENTER,margin_bottom=12)
        preview=Gtk.Button(label=tr("Preview"))
        preview.connect("clicked",lambda *_:self.preview(record));controls.append(preview)
        install=Gtk.Button(label=tr("Import"),css_classes=["suggested-action"])
        install.connect("clicked",lambda *_:self.import_look(record,install));controls.append(install)
        box.append(controls);return box

    def preview(self,record):
        dialog=Adw.Dialog(title=record["name"],content_width=900,content_height=700)
        body=body_box()
        picture=Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN,height_request=460,can_shrink=True)
        body.append(picture)
        screenshot=record["screenshot"]
        if screenshot:load_texture_into(picture,**({"url":screenshot} if screenshot.startswith("https://") else {"path":screenshot}),size=1600)
        body.append(Gtk.Label(label=record["description"] or "No description supplied",wrap=True,xalign=0,selectable=True))
        shell(dialog,body);dialog.present(self)

    def import_look(self,record,button):
        button.set_sensitive(False)
        folder=Path(tempfile.mkdtemp(prefix="orbits-gallery-"))
        def work():return look_gallery.fetch_look(record,folder/"look.orbits")
        def done(path,error):
            try:
                if error:self.page.window.toast(str(error),timeout=6)
                else:self.page.import_path(path)
            finally:
                shutil.rmtree(folder,ignore_errors=True)
                if not self.closed:button.set_sensitive(True)
        net.run_async(work,done)
