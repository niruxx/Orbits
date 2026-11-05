"""Read terminal color schemes as data. Downloaded scripts are never executed."""
import configparser
import json
from pathlib import Path
import plistlib
import re
import tomllib

SUFFIXES = {".palette", ".colorscheme", ".itermcolors", ".json", ".toml", ".conf"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")


def color(value):
    if not isinstance(value, str):
        raise ValueError("Invalid terminal color")
    value = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    if value.startswith("0x"):
        value = "#" + value[2:]
    if not HEX.fullmatch(value):
        raise ValueError(f"Unsupported terminal color: {value[:60]}")
    return value.lower()


def normalize(data, name="Terminal Scheme"):
    if not isinstance(data, dict):
        raise ValueError("Invalid terminal scheme")
    colors = data.get("colors") or data.get("color")
    if not isinstance(colors, list) or len(colors) != 16:
        raise ValueError("A terminal scheme needs exactly 16 ANSI colors")
    return {"name": " ".join(str(data.get("name") or name).split())[:100], "background": color(data["background"]),
            "foreground": color(data["foreground"]), "cursor": color(data.get("cursor") or data["foreground"]),
            "colors": [color(c) for c in colors]}


def read_scheme(path):
    path = Path(path)
    if path.stat().st_size > 2 << 20:
        raise ValueError("Terminal scheme is too large")
    if path.suffix == ".json":
        return normalize(json.loads(path.read_text()), path.stem)
    if path.suffix == ".toml":
        data = tomllib.loads(path.read_text())["colors"]
        primary = data["primary"]
        return normalize({"background": primary["background"], "foreground": primary["foreground"],
                          "colors": [data[group][key] for group in ("normal", "bright") for key in NAMES]}, path.stem)
    if path.suffix == ".conf":
        values = {}
        for line in path.read_text().splitlines():
            match = re.match(r"^\s*(background|foreground|cursor|color\d+)\s+(#[0-9a-fA-F]{3,6})\s*(?:#.*)?$", line)
            if match:
                values[match.group(1)] = match.group(2)
        return normalize({**values, "colors": [values[f"color{i}"] for i in range(16)]}, path.stem)
    if path.suffix == ".itermcolors":
        data = plistlib.loads(path.read_bytes())
        def rgb(key):
            channels = [float(data[key][f"{c} Component"]) for c in ("Red", "Green", "Blue")]
            if any(not 0 <= c <= 1 for c in channels):
                raise ValueError("Invalid iTerm color component")
            return "#" + "".join(f"{round(c*255):02x}" for c in channels)
        return normalize({"background":rgb("Background Color"), "foreground":rgb("Foreground Color"),
                          "colors":[rgb(f"Ansi {i} Color") for i in range(16)]}, path.stem)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(path.read_text())
    if path.suffix == ".palette":
        group = parser["Dark"] if "Dark" in parser else parser["Light"] if "Light" in parser else parser["Palette"]
        return normalize({"name":parser["Palette"].get("Name",path.stem), "background":group["Background"],
                          "foreground":group["Foreground"], "cursor":group.get("Cursor",group["Foreground"]),
                          "colors":[group[f"Color{i}"] for i in range(16)]}, path.stem)
    if path.suffix == ".colorscheme":
        def rgb(group):
            values = [int(v) for v in parser[group]["Color"].split(",")]
            if len(values) < 3 or any(not 0 <= v <= 255 for v in values):
                raise ValueError("Invalid Konsole color")
            return "#" + "".join(f"{v:02x}" for v in values[:3])
        return normalize({"name":parser["General"].get("Description",path.stem),"background":rgb("Background"),
                          "foreground":rgb("Foreground"),"colors":[rgb(f"Color{i}{suffix}")
                          for suffix in ("", "Intense") for i in range(8)]},path.stem)
    raise ValueError("Unsupported terminal scheme format")


def find_schemes(root):
    result = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.is_symlink() or path.suffix not in SUFFIXES:
            continue
        try:
            scheme = read_scheme(path)
        except (ValueError, KeyError, OSError, configparser.Error, TypeError):
            continue
        result.append((path, scheme))
    return result


def export_scheme(scheme, directory):
    p = normalize(scheme)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    kitty = f"background {p['background']}\nforeground {p['foreground']}\ncursor {p['cursor']}\n"
    kitty += "".join(f"color{i} {c}\n" for i,c in enumerate(p["colors"]))
    alacritty = f'[colors.primary]\nbackground = "{p["background"]}"\nforeground = "{p["foreground"]}"\n'
    for group,offset in (("normal",0),("bright",8)):
        alacritty += f"\n[colors.{group}]\n" + "".join(f'{name} = "{p["colors"][i+offset]}"\n' for i,name in enumerate(NAMES))
    ptyxis = f"[Palette]\nName={p['name'].replace(chr(10),' ')}\nBackground={p['background']}\nForeground={p['foreground']}\nCursor={p['cursor']}\n"
    ptyxis += "".join(f"Color{i}={c}\n" for i,c in enumerate(p["colors"]))
    for name,text in (("orbits.conf",kitty),("orbits.toml",alacritty),("orbits.palette",ptyxis),
                      ("terminal.json",json.dumps(p,indent=2))):
        (directory/name).write_text(text)
    return directory
