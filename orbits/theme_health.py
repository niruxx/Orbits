"""Evidence-based GTK4 theme checks; heuristics are warnings, never compatibility guarantees."""
import configparser
from pathlib import Path
import re

GTK_MIGRATION = "https://docs.gtk.org/gtk4/migrating-3to4.html"
ADW_COLORS = "https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1-latest/css-variables.html"


def version(value):
    match = re.match(r"^(\d+)\.(\d+)", str(value))
    return tuple(map(int, match.groups())) if match else None


def check(theme_dir, adwaita_version=None, gtk_version=None):
    root = Path(theme_dir).resolve()
    gtk = root / "gtk-4.0"
    issues = []
    def report(code, message, severity="warning", file=None, line=None, reference=""):
        issues.append({"code":code,"message":message,"severity":severity,
                       "file":str(file.relative_to(root)) if file else "", "line":line, "reference":reference})
    if not (gtk / "gtk.css").is_file():
        report("no-gtk4", "This theme has no GTK4 stylesheet; it cannot style libadwaita apps.")
        return issues
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(root / "index.theme")
        metadata = parser["Orbits"] if "Orbits" in parser else {}
        for library, running in (("Libadwaita", adwaita_version),("GTK",gtk_version)):
            current = version(running)
            if not current:
                continue
            for suffix, compare, reason in (("Min",lambda a,b:a<b,"requires at least"),("Max",lambda a,b:a>b,"was tested only up to")):
                declared = version(metadata.get(library+suffix,""))
                if declared and compare(current,declared):
                    report("version-range", f"The author declares this theme {reason} {library} {declared[0]}.{declared[1]}; installed: {running}.")
    except (OSError, configparser.Error):
        report("metadata", "The theme's index.theme metadata could not be read.")
    pending, visited, texts = [gtk/"gtk.css"], set(), []
    while pending and len(visited) < 128:
        path = pending.pop()
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            report("external-import", "A stylesheet link/import points outside this theme and was not inspected.", file=path)
            continue
        if resolved in visited:
            continue
        visited.add(resolved)
        try:
            if path.stat().st_size > 4 << 20:
                report("large-css", "A stylesheet is too large to inspect automatically.",file=path)
                continue
            text = path.read_text(errors="replace")
        except OSError:
            report("missing-import", "A referenced stylesheet is missing or unreadable.","error",path)
            continue
        text = re.sub(r"/\*.*?\*/",lambda m:"\n"*m.group().count("\n"),text,flags=re.S)
        texts.append(text)
        # GTK3 widget style properties no longer exist in GTK4.
        for match in re.finditer(r"(?m)(?:^|[;{])\s*(-Gtk[A-Za-z]+-[\w-]+)\s*:",text):
            report("gtk3-style-property", f"{match.group(1)} is a GTK3 widget style property, which GTK4 removed.",
                   "error",path,text.count("\n",0,match.start(1))+1,GTK_MIGRATION)
        for match in re.finditer(r'@import\s+(?:url\(\s*)?["\']([^"\']+)["\']\s*\)?',text):
            target = match.group(1)
            if target.startswith(("resource:","data:")):
                continue
            if "://" in target:
                report("remote-import", "A non-local CSS import cannot be checked offline.",file=path)
                continue
            pending.append(path.parent / target)
    if pending:
        report("scan-limit", "Only the first 128 imported stylesheets were inspected.")
    combined = "\n".join(texts)
    if (version(adwaita_version) or (0,0)) >= (1,6) and "@define-color" in combined and not re.search(r"--(?:window-bg|view-bg|accent-bg)-color\s*:",combined):
        report("legacy-colors", "This stylesheet defines legacy named colors without modern libadwaita color variables. New widgets may not follow its palette (heuristic).",reference=ADW_COLORS)
    if re.search(r"(?<![\w-])(?:leaflet|flap|squeezer)(?![\w-])",combined) and not re.search(r"navigation-(?:view|split-view)|toolbarview|dialog",combined):
        report("legacy-widgets", "Selectors target older adaptive widgets but do not mention newer navigation/dialog widgets; check those widgets visually (heuristic).")
    return issues[:100]
