"""Shared font actions: apply menu, system-wide install/remove, login screen hand-off."""
import subprocess
from pathlib import Path

from gi.repository import Adw, Gtk, Pango, PangoCairo

from orbits.fonts import USER_FONT_DIR, get_font_library
from orbits.privileged import run_helper

ROLES = [("interface", "Interface"), ("document", "Documents"), ("monospace", "Monospace")]


def font_attrs(family, size_pt=None, weight=None):
    attrs = Pango.AttrList()
    desc = Pango.FontDescription()
    desc.set_family(family)
    if weight:
        desc.set_weight(weight)
    attrs.insert(Pango.attr_font_desc_new(desc))
    if size_pt:
        attrs.insert(Pango.attr_size_new(int(size_pt * Pango.SCALE)))
    return attrs


def system_families():
    fm = PangoCairo.FontMap.get_default()
    return sorted({f.get_name() for f in fm.list_families()}, key=str.lower)


def is_system_wide(family):
    """True if fontconfig finds this family outside the user's home (so GDM can use it)."""
    try:
        out = subprocess.run(["fc-list", family, "file"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    home = str(Path.home())
    return any(line.strip() and not line.startswith(home) for line in out.splitlines())


def install_system_wide(window, entry, on_done=None):
    lib = get_font_library()
    src = USER_FONT_DIR / entry["slug"]
    if not src.is_dir():
        window.toast("Install the font for your user first")
        return

    def done(ok, msg):
        if ok:
            lib.mark_system(entry["key"], True)
            window.toast(f"“{entry['family']}” installed for all users")
        else:
            window.toast(msg, timeout=6)
        if on_done:
            on_done(ok)

    run_helper(["font-install", str(src), entry["slug"]], done)


def remove_system_wide(window, entry):
    lib = get_font_library()

    def done(ok, msg):
        if ok:
            lib.mark_system(entry["key"], False)
            window.toast(f"Removed system-wide copy of “{entry['family']}”")
        else:
            window.toast(msg, timeout=6)

    run_helper(["font-remove", entry["slug"]], done)


def use_for_login(window, family, entry=None):
    """Hand the family to the Login Screen page, installing system-wide first if needed."""
    def go():
        gdm = window.pages["gdm"]
        cur = gdm.model.get("font_name") or "Sans 11"
        size = cur.rsplit(" ", 1)[-1] if cur.rsplit(" ", 1)[-1].replace(".", "").isdigit() else "11"
        gdm.model.set("font_name", f"{family} {size}")
        gdm._sync_rows()
        gdm._changed()
        window.navigate("gdm")
        gdm.stack.set_visible_child_name("style")
        window.toast("Font set for the login screen — press Apply to save")

    if is_system_wide(family):
        go()
        return
    if entry is None:
        window.toast("GDM can only use fonts installed system-wide; this one is only in your home folder", timeout=6)
        return
    dlg = Adw.AlertDialog(heading="Install System-Wide?",
                          body=f"The login screen runs as a separate user and can't see fonts in your home "
                               f"folder. Install “{family}” for all users? This requires your password.")
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("install", "Install System-Wide")
    dlg.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
    dlg.connect("response", lambda _d, r: r == "install" and install_system_wide(
        window, entry, lambda ok: ok and go()))
    dlg.present(window)


def apply_menu_button(window, family_getter, entry_getter=lambda: None, label=None):
    """A menu button offering: set as interface / document / monospace / login screen font."""
    btn = Gtk.MenuButton(valign=Gtk.Align.CENTER, tooltip_text="Apply Font")
    if label:
        btn.set_child(Adw.ButtonContent(label=label, icon_name="object-select-symbolic"))
        btn.add_css_class("suggested-action")
    else:
        btn.set_icon_name("object-select-symbolic")
        btn.add_css_class("flat")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    pop = Gtk.Popover(child=box)

    def add(text, cb):
        b = Gtk.Button(label=text, css_classes=["flat"])
        b.get_child().set_xalign(0)
        b.connect("clicked", lambda *_: (pop.popdown(), cb()))
        box.append(b)

    lib = get_font_library()
    for role, title in ROLES:
        add(f"Use as {title} Font",
            lambda r=role, t=title: (lib.apply(family_getter(), r),
                                     window.toast(f"{t} font set to “{family_getter()}”")))
    add("Use for Everything", lambda: ([lib.apply(family_getter(), r) for r, _ in ROLES[:2]],
                                       window.toast(f"Interface & document fonts set to “{family_getter()}”")))
    box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
    add("Use for Login Screen…", lambda: use_for_login(window, family_getter(), entry_getter()))
    btn.set_popover(pop)
    return btn


