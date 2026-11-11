"""Wallpaper palette generation and portable palette imports/exports (no GTK required)."""
import colorsys
import json
import re
from pathlib import Path

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ROLES = ("accent", "window_bg", "window_fg", "view_bg", "headerbar_bg", "sidebar_bg", "card_bg", "popover_bg", "destructive")
ACCENTS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800",
           "orange": "#ed5b00", "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396"}


def rgb(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("Colors must use #RRGGBB notation")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def hex_color(value):
    return "#" + "".join(f"{max(0, min(255, round(channel * 255))):02x}" for channel in value)


def nearest_accent(value):
    hue, saturation, brightness = colorsys.rgb_to_hsv(*rgb(value))
    if saturation < 0.12:
        return "slate"
    def distance(name):
        h, s, v = colorsys.rgb_to_hsv(*rgb(ACCENTS[name]))
        delta = min(abs(hue - h), 1 - abs(hue - h))
        return 4 * delta ** 2 + 0.05 * (saturation - s) ** 2 + 0.02 * (brightness - v) ** 2
    return min(ACCENTS, key=distance)


def validate(palette):
    if not isinstance(palette, dict):
        raise ValueError("A palette must contain light and/or dark styles")
    out = {}
    for tone in ("light", "dark"):
        variant = palette.get(tone)
        if variant is None:
            out[tone] = None
            continue
        if not isinstance(variant, dict) or any(role not in variant for role in ROLES):
            raise ValueError(f"Incomplete {tone} palette")
        out[tone] = {role: hex_color(rgb(variant[role])) for role in ROLES}
    if not any(out.values()):
        raise ValueError("A palette needs at least one style")
    return out


def from_wallpaper(path):
    from orbits.wallpaper_tools import open_image
    image = open_image(path)
    image.thumbnail((160, 160))
    quantized = image.quantize(colors=12)
    table = quantized.getpalette()
    candidates = []
    for count, index in quantized.getcolors():
        color = tuple(v / 255 for v in table[index * 3:index * 3 + 3])
        h, saturation, value = colorsys.rgb_to_hsv(*color)
        candidates.append((count * (0.2 + saturation) * (0.2 + min(value, 1 - value)), color))
    accent = max(candidates, key=lambda pair: pair[0])[1]
    hue, saturation, _ = colorsys.rgb_to_hsv(*accent)

    def color(s, v):
        return hex_color(colorsys.hsv_to_rgb(hue, s, v))

    out = {}
    for tone in ("light", "dark"):
        dark = tone == "dark"
        out[tone] = dict(zip(ROLES, (
            color(max(0.45, saturation), 0.85 if dark else 0.65),
            color(0.18 if dark else 0.025, 0.14 if dark else 0.97),
            "#f4f4f5" if dark else "#242428",
            color(0.20 if dark else 0.01, 0.11 if dark else 1),
            color(0.16 if dark else 0.04, 0.19 if dark else 0.95),
            color(0.16 if dark else 0.045, 0.17 if dark else 0.93),
            color(0.15 if dark else 0.01, 0.22 if dark else 1),
            color(0.15 if dark else 0.01, 0.22 if dark else 1),
            "#f66151" if dark else "#c01c28")))
    return out


GRADIENCE_ROLES = {"accent": "accent_bg_color", "window_bg": "window_bg_color", "window_fg": "window_fg_color",
                   "view_bg": "view_bg_color", "headerbar_bg": "headerbar_bg_color", "sidebar_bg": "sidebar_bg_color",
                   "card_bg": "card_bg_color", "popover_bg": "popover_bg_color", "destructive": "destructive_bg_color"}


def import_palette(path, defaults):
    if Path(path).stat().st_size > 2 * 1024**2:
        raise ValueError("Palette metadata is too large")
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("Invalid palette file")
    if "variables" in data:  # Gradience preset (one style per file)
        variables = data["variables"]
        if not isinstance(variables, dict):
            raise ValueError("Invalid Gradience variables")
        # Resolve Gradience @color references; composite translucent colors over its window background.
        background = "#222226"
        def resolve(value, seen=()):
            if isinstance(value, str) and value.startswith("@"):
                name = value[1:]
                if name in seen or name not in variables:
                    raise ValueError("Invalid Gradience color reference")
                return resolve(variables[name], seen + (name,))
            if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{3}", value):
                value = "#" + "".join(c * 2 for c in value[1:])
            if isinstance(value, str):
                match = re.fullmatch(r"rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)(?:\s*,\s*([0-9.]+))?\s*\)", value)
                if match:
                    channels = tuple(float(v) for v in match.groups()[:3])
                    alpha = float(match.group(4)) if match.group(4) else 1
                    if any(v < 0 or v > 255 for v in channels) or not 0 <= alpha <= 1:
                        raise ValueError("Invalid Gradience RGB color")
                    return hex_color(tuple(channel / 255 * alpha + base * (1-alpha)
                                           for channel, base in zip(channels, rgb(background))))
            return hex_color(rgb(value))
        background = resolve(variables.get("window_bg_color", "#222226"))
        tone = "dark" if sum(rgb(background)) < 1.5 else "light"
        variant = dict(defaults[tone])
        for role, key in GRADIENCE_ROLES.items():
            if key in variables:
                variant[role] = resolve(variables[key])
        palette = {"light": None, "dark": None, tone: variant}
    else:
        palette = data.get("palette", data)
    return str(data.get("name") or Path(path).stem)[:100], validate(palette)


EXPORT_TARGETS = {"kitty": "Kitty", "alacritty": "Alacritty", "ptyxis": "Ptyxis",
                  "vscode": "VS Code", "firefox": "Firefox userChrome"}


def export_files(palette, tone):
    p = validate(palette).get(tone)
    if not p:
        raise ValueError(f"No {tone} palette to export")
    bg, fg, accent = p["window_bg"], p["window_fg"], p["accent"]
    ansi = [p["view_bg"], p["destructive"], "#57a96b", "#d9a441", accent, "#b87bd5", "#4fb3bc", fg]
    ansi += [hex_color(tuple(c + (1 - c) * 0.25 for c in rgb(v))) for v in ansi]
    kitty = f"# Orbits {tone}\nbackground {bg}\nforeground {fg}\ncursor {accent}\nselection_background {accent}\n"
    kitty += "".join(f"color{i} {color}\n" for i, color in enumerate(ansi))
    alacritty = f'[colors.primary]\nbackground = "{bg}"\nforeground = "{fg}"\n'
    names = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
    for group, offset in (("normal", 0), ("bright", 8)):
        alacritty += f"\n[colors.{group}]\n" + "".join(f'{name} = "{ansi[offset+i]}"\n' for i, name in enumerate(names))
    ptyxis = f"[Palette]\nName=Orbits {tone.title()}\nBackground={bg}\nForeground={fg}\nCursor={accent}\n"
    ptyxis += "".join(f"Color{i}={color}\n" for i, color in enumerate(ansi))
    vscode = {"name": f"Orbits {tone.title()}", "type": "dark" if tone == "dark" else "light", "colors": {
        "editor.background": p["view_bg"], "editor.foreground": fg, "sideBar.background": p["sidebar_bg"],
        "activityBar.background": bg, "statusBar.background": accent, "focusBorder": accent,
        "terminal.background": bg, "terminal.foreground": fg}}
    firefox = f"/* Orbits: copy to your Firefox profile's chrome/userChrome.css. */\n:root {{\n --toolbar-bgcolor: {bg} !important;\n --toolbar-color: {fg} !important;\n --lwt-accent-color: {bg} !important;\n --lwt-text-color: {fg} !important;\n --focus-outline-color: {accent} !important;\n}}\n"
    return {"kitty": ("orbits.conf", kitty), "alacritty": ("orbits.toml", alacritty),
            "ptyxis": ("orbits.palette", ptyxis), "vscode": ("orbits-color-theme.json", json.dumps(vscode, indent=2)),
            "firefox": ("userChrome.css", firefox)}


def export_selected(palette, tone, targets, directory):
    files = export_files(palette, tone)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for target in targets:
        if target not in files:
            raise ValueError(f"Unknown export target: {target}")
        name, content = files[target]
        folder = directory / target
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(content)
        written.append(folder / name)
    return written
