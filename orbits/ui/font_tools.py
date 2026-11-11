"""Font inspection, comparison and per-user fontconfig preferences."""
from orbits.i18n import _ as tr
import copy
from pathlib import Path

from gi.repository import Adw, Gdk, Gtk, Pango, PangoCairo

from orbits import font_tools, net
from orbits.fonts import get_font_library, system_families
from orbits.ui.font_preview import load as load_font_preview
from orbits.ui.font_actions import font_attrs


def body_box():
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16,margin_start=24,margin_end=24,margin_top=12,margin_bottom=24)


def shell(dialog, body, save=None):
    view=Adw.ToolbarView()
    header=Adw.HeaderBar()
    if save:
        button=Gtk.Button(label=tr("Save"),css_classes=["suggested-action"])
        button.connect("clicked",lambda *_:save())
        header.pack_end(button)
    view.add_top_bar(header)
    view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,child=body))
    dialog.set_child(view)


class FontInspector(Adw.Dialog):
    def __init__(self, window, entry):
        super().__init__(title=entry["family"],content_width=850,content_height=800)
        self.window,self.entry=window,entry
        self.metadata=None
        self.generation=0
        self.closed=False
        self.connect("closed",lambda *_:setattr(self,"closed",True))
        body=body_box()
        self.files=entry["files"]
        self.file_choice=Gtk.DropDown.new_from_strings([Path(file).name for file in self.files])
        self.file_choice.connect("notify::selected",lambda *_:self.load())
        body.append(self.file_choice)
        self.sample_text=Gtk.Entry(text="The quick brown fox jumps over the lazy dog. 0123456789")
        self.sample_text.connect("changed",lambda *_:self.preview())
        body.append(self.sample_text)
        self.sample=Gtk.Picture(height_request=100,margin_top=10,margin_bottom=10)
        body.append(self.sample)
        self.axis_group=Adw.PreferencesGroup(title=tr("Variable Font Axes"))
        body.append(self.axis_group)
        self.feature_group=Adw.PreferencesGroup(title=tr("OpenType Features"))
        body.append(self.feature_group)
        self.axis_rows=[]
        self.feature_rows=[]
        self.axes={}
        self.features={}
        apply_box=Gtk.Box(spacing=8)
        for role,label in (("interface","Use for Interface"),("document","Use for Documents"),("monospace","Use for Monospace")):
            button=Gtk.Button(label=label)
            button.connect("clicked",lambda _button,r=role:self.apply(r))
            apply_box.append(button)
        body.append(apply_box)
        self.info=Gtk.Label(label=tr("Reading font tables…"),xalign=0,wrap=True,selectable=True)
        body.append(self.info)
        self.license=Gtk.Label(xalign=0,wrap=True,selectable=True)
        license_group=Adw.ExpanderRow(title=tr("License"))
        license_group.add_row(self.license)
        body.append(license_group)
        glyphs=Adw.PreferencesGroup(title=tr("Glyph Map"),description=tr("Click a character to copy it. Only characters present in this font are listed."))
        self.search=Gtk.SearchEntry(placeholder_text=tr("Character, Unicode name or U+ codepoint"))
        self.search.connect("search-changed",lambda *_:self.glyph_page(0))
        glyphs.add(self.search)
        self.grid=Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,max_children_per_line=12,column_spacing=6,row_spacing=6)
        glyphs.add(self.grid)
        self.glyph_status=Gtk.Label(xalign=0)
        glyphs.add(self.glyph_status)
        nav=Gtk.Box(spacing=8)
        self.page=0
        self.previous=Gtk.Button(label=tr("Previous"))
        self.previous.connect("clicked",lambda *_:self.glyph_page(self.page-1))
        self.next=Gtk.Button(label=tr("Next"))
        self.next.connect("clicked",lambda *_:self.glyph_page(self.page+1))
        nav.append(self.previous);nav.append(self.next)
        glyphs.add(nav)
        body.append(glyphs)
        shell(self,body)
        self.load()

    def load(self):
        if not self.files:return
        self.generation+=1
        generation=self.generation
        path=self.files[self.file_choice.get_selected()]
        def done(metadata,error):
            if self.closed or generation!=self.generation:return
            if error:
                self.info.set_label(str(error));return
            self.metadata=metadata
            self.preview_path = path
            self.info.set_label(" · ".join(filter(None,[metadata["full_name"],metadata["style"],metadata["version"],
                f"{metadata['glyph_count']} glyphs",f"{len(metadata['codepoints'])} Unicode characters",metadata["designer"]])))
            self.license.set_label((metadata["license"] or self.entry.get("license") or "No embedded license information") + ("\n"+metadata["license_url"] if metadata["license_url"] else ""))
            for row in self.axis_rows:self.axis_group.remove(row)
            for row in self.feature_rows:self.feature_group.remove(row)
            self.axis_rows=[];self.feature_rows=[];self.axes={};self.features={}
            for axis in metadata["axes"]:
                row=Adw.SpinRow(title=tr('{value0} ({value1})').format(value0=axis['name'], value1=axis['tag']),digits=2,
                    adjustment=Gtk.Adjustment(lower=axis["min"],upper=axis["max"],value=axis["default"],step_increment=max(.01,(axis["max"]-axis["min"])/100)))
                row.connect("notify::value",lambda *_:self.preview())
                self.axes[axis["tag"]]=row
                self.axis_group.add(row);self.axis_rows.append(row)
            self.axis_group.set_visible(bool(self.axes))
            for feature in metadata["features"][:100]:
                row=Adw.ComboRow(title=feature,model=Gtk.StringList.new(["Font Default","On","Off"]))
                row.connect("notify::selected",lambda *_:self.preview())
                self.features[feature]=row
                self.feature_group.add(row);self.feature_rows.append(row)
            self.feature_group.set_visible(bool(self.features))
            self.preview();self.glyph_page(0)
        net.run_async(lambda:font_tools.inspect_font(path),done)

    def variations(self):
        return ",".join(f"{tag}={row.get_value():g}" for tag,row in self.axes.items())

    def preview(self):
        if not self.metadata:return
        features = [tag if row.get_selected() == 1 else '-' + tag for tag, row in self.features.items() if row.get_selected()]
        load_font_preview(self.sample, self.preview_path, self.sample_text.get_text(), 24,
                          variations={tag: row.get_value() for tag, row in self.axes.items()}, features=features)

    def glyph_page(self,page):
        if not self.metadata:return
        records,total=font_tools.glyph_page(self.metadata["codepoints"],self.search.get_text(),max(0,page))
        self.page=max(0,page)
        self.grid.remove_all()
        for record in records:
            label=Gtk.Label(label=record["character"])
            label.set_attributes(font_attrs("Sans",22))
            button=Gtk.Button(child=label,tooltip_text=tr('U+{value0:04X} · {value1}').format(value0=record['codepoint'], value1=record['name']))
            button.connect("clicked",lambda _b,char=record["character"]:Gdk.Display.get_default().get_clipboard().set(char))
            self.grid.append(button)
        self.glyph_status.set_label(tr('{value0} characters · page {value1}').format(value0=total, value1=self.page + 1))
        self.previous.set_sensitive(self.page>0)
        self.next.set_sensitive((self.page+1)*128<total)

    def apply(self,role):
        if not self.metadata:return
        try:
            get_font_library().apply(self.metadata["family"],role,variations=self.variations())
            self.window.toast(tr("Font applied; axis settings are included"))
        except Exception as error:self.window.toast(str(error),timeout=6)


class FontCompare(Adw.Dialog):
    def __init__(self,window):
        super().__init__(title=tr("Compare Fonts & Pairings"),content_width=900,content_height=680)
        self.window=window
        families=system_families()
        self.pairs=font_tools.suggest_pairings(families)
        self.families=sorted(set(families)|{p[k] for p in self.pairs for k in ("heading","body")}) or ["Sans"]
        body=body_box()
        self.text=Gtk.Entry(text="Beautiful typography — The quick brown fox jumps over the lazy dog. 0123456789")
        self.text.connect("changed",lambda *_:self.update())
        body.append(self.text)
        self.size=Gtk.SpinButton.new_with_range(8,96,1)
        self.size.set_value(24)
        self.size.connect("value-changed",lambda *_:self.update())
        body.append(self.size)
        columns=Gtk.Box(spacing=24,homogeneous=True)
        self.choices=[];self.samples=[]
        for i in range(2):
            column=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16,hexpand=True)
            choice=Gtk.DropDown.new_from_strings(self.families)
            choice.set_selected(min(i,len(self.families)-1))
            choice.connect("notify::selected",lambda *_:self.update())
            self.choices.append(choice);column.append(choice)
            sample=Gtk.Picture(height_request=150)
            self.samples.append(sample);column.append(sample)
            columns.append(column)
        body.append(columns)
        pairs=Adw.PreferencesGroup(title=tr("Heading + Body Pairings"),description=tr("Suggested pairs are examples; missing fonts use fallback until installed from the font browser."))
        for pair in sorted(self.pairs,key=lambda p:not p["installed"]):
            row=Adw.ActionRow(title=tr('{value0} + {value1}').format(value0=pair['heading'], value1=pair['body']),subtitle="Both installed" if pair["installed"] else "Install missing families to see the intended pairing",use_markup=False)
            button=Gtk.Button(label=tr("Compare"),valign=Gtk.Align.CENTER)
            button.connect("clicked",lambda _b,p=pair:self.choose(p))
            row.add_suffix(button);pairs.add(row)
        body.append(pairs)
        shell(self,body)
        self.update()

    def choose(self,pair):
        for choice,key in zip(self.choices,("heading","body")):choice.set_selected(self.families.index(pair[key]))
        self.update()

    def update(self):
        for choice,sample in zip(self.choices,self.samples):
            family = self.families[choice.get_selected()]
            def resolve(family=family):
                import subprocess
                result = subprocess.run(['fc-match', '--format', '%{file}', family], capture_output=True, text=True, timeout=10, check=True)
                return result.stdout.strip()
            def ready(path, error, picture=sample, text=self.text.get_text(), size=self.size.get_value()):
                if error:
                    picture.set_tooltip_text(str(error))
                elif path:
                    load_font_preview(picture, path, text, size)
            net.run_async(resolve, ready)


class FontRules(Adw.Dialog):
    def __init__(self,window):
        super().__init__(title=tr("Font Preferences"),content_width=680,content_height=700)
        self.window=window;self.lib=get_font_library();self.rules=copy.deepcopy(self.lib.load_rules())
        body=body_box()
        fallback=Adw.PreferencesGroup(title=tr("Emoji Fallback"),description=tr("Per-user fontconfig preferences apply to applications that use fontconfig. Restart running apps after changes."))
        self.emoji=Adw.EntryRow(title=tr("Emoji Family (blank for system default)"),text=self.rules.get("emoji",""))
        fallback.add(self.emoji);body.append(fallback)
        self.hints=Adw.PreferencesGroup(title=tr("Per-Family Hinting"))
        self.hint_family=Adw.EntryRow(title=tr("Font Family"))
        self.hint_style=Adw.ComboRow(title=tr("Hinting"),model=Gtk.StringList.new(["None","Slight","Medium","Full"]))
        self.hints.add(self.hint_family);self.hints.add(self.hint_style)
        add=Adw.ButtonRow(title=tr("Add Hinting Rule"))
        add.connect("activated",lambda *_:self.add_hint());self.hints.add(add)
        self.hint_rows=[];body.append(self.hints)
        self.subs=Adw.PreferencesGroup(title=tr("Font Substitutions"))
        self.source=Adw.EntryRow(title=tr("Requested Family"))
        self.target=Adw.EntryRow(title=tr("Preferred Replacement"))
        self.subs.add(self.source);self.subs.add(self.target)
        add=Adw.ButtonRow(title=tr("Add Substitution"))
        add.connect("activated",lambda *_:self.add_sub());self.subs.add(add)
        self.sub_rows=[];body.append(self.subs)
        self.rebuild();shell(self,body,self.save)

    def add_hint(self):
        try:
            name=font_tools.family_name(self.hint_family.get_text())
            self.rules.setdefault("hinting",{})[name]=["none","slight","medium","full"][self.hint_style.get_selected()]
            self.rebuild()
        except ValueError as error:self.window.toast(str(error),timeout=6)

    def add_sub(self):
        try:
            source=font_tools.family_name(self.source.get_text());target=font_tools.family_name(self.target.get_text())
            self.rules.setdefault("substitutions",{})[source]=target
            self.rebuild()
        except ValueError as error:self.window.toast(str(error),timeout=6)

    def rebuild(self):
        for group,rows,key in ((self.hints,self.hint_rows,"hinting"),(self.subs,self.sub_rows,"substitutions")):
            for row in rows:group.remove(row)
            rows.clear()
            for family,value in self.rules.get(key,{}).items():
                row=Adw.ActionRow(title=family,subtitle=value,use_markup=False)
                button=Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=tr("Remove Font Rule"),valign=Gtk.Align.CENTER)
                button.connect("clicked",lambda _b,k=key,f=family:(self.rules[k].pop(f),self.rebuild()))
                row.add_suffix(button);group.add(row);rows.append(row)

    def save(self):
        self.rules["emoji"]=self.emoji.get_text().strip()
        try:
            self.lib.save_rules(self.rules)
            self.window.toast(tr("Font preferences saved — restart apps to refresh font choices"),timeout=6)
            self.close()
        except Exception as error:self.window.toast(str(error),timeout=6)
