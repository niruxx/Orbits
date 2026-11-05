"""Font inspection, validated local imports and per-user fontconfig rules."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}
MAX_FONT = 128 << 20
MAX_ARCHIVE = 512 << 20


def font_class():
    try:
        from fontTools.ttLib import TTFont
    except ImportError as error:
        raise RuntimeError("Install python-fonttools for font import and inspection") from error
    return TTFont


def inspect_font(path, face=0):
    path = Path(path)
    if path.stat().st_size > MAX_FONT:
        raise ValueError("Font file exceeds 128 MiB")
    TTFont = font_class()
    try:
        with path.open("rb") as stream, TTFont(stream, fontNumber=face, lazy=True) as font:
            def name(number):
                table = font.get("name")
                return (table.getDebugName(number) or "")[:12000] if table else ""
            family = name(16) or name(1) or path.stem
            cmap = font.getBestCmap() or {}
            features = set()
            for tag in ("GSUB", "GPOS"):
                if tag in font:
                    listing = getattr(font[tag].table, "FeatureList", None)
                    if listing:
                        features.update(record.FeatureTag for record in listing.FeatureRecord)
            axes = []
            if "fvar" in font:
                for axis in font["fvar"].axes:
                    limits = (axis.minValue, axis.defaultValue, axis.maxValue)
                    if not all(math.isfinite(v) for v in limits) or not limits[0] <= limits[1] <= limits[2]:
                        continue
                    axes.append({"tag":axis.axisTag,"name":name(axis.axisNameID) or axis.axisTag,
                                 "min":axis.minValue,"default":axis.defaultValue,"max":axis.maxValue})
            return {"family":family[:200],"style":name(17) or name(2),"full_name":name(4),
                    "version":name(5),"designer":name(9),"license":name(13),"license_url":name(14),
                    "glyph_count":len(font.getGlyphOrder()),"codepoints":sorted(c for c in cmap if 0 <= c <= 0x10ffff),
                    "features":sorted(features),"axes":axes,"file":str(path),"face":face,
                    "monospace":bool(getattr(font.get("post"), "isFixedPitch", 0))}
    except Exception as error:
        raise ValueError(f"Could not read font {path.name}: {error}") from error


def glyph_page(codepoints, query="", page=0, page_size=128):
    query = query.strip().upper()
    matches = []
    for codepoint in codepoints:
        if unicodedata.category(chr(codepoint)) in ("Cc","Cs"):continue
        name = unicodedata.name(chr(codepoint), "UNNAMED")
        if not query or query in name or query in f"U+{codepoint:04X}" or query == chr(codepoint).upper():
            matches.append({"codepoint":codepoint,"character":chr(codepoint),"name":name})
    start = max(0, int(page)) * page_size
    return matches[start:start+page_size], len(matches)


def prepare_local(paths, destination):
    """Flatten ZIP font assets, validate real font tables and preserve adjacent licenses."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    candidates = []
    total = 0
    def copy(stream, name, size):
        nonlocal total
        total += size
        if total > MAX_ARCHIVE or size > MAX_FONT:
            raise ValueError("Font import exceeds its size limit")
        suffix = Path(name).suffix.lower()
        license_ = Path(name).name.upper().startswith(("OFL", "LICENSE", "COPYING"))
        if suffix not in FONT_EXTS and not license_:
            return
        target = destination / Path(name).name
        index = 2
        while target.exists():
            target = destination / f"{Path(name).stem}-{index}{Path(name).suffix}"
            index += 1
        with target.open("wb") as output:
            shutil.copyfileobj(stream, output)
        if suffix in FONT_EXTS:
            candidates.append(target)
    for source in map(Path, paths):
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Select a regular font or ZIP file: {source.name}")
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as archive:
                if len(archive.infolist()) > 10000:
                    raise ValueError("Font archive contains too many entries")
                for item in archive.infolist():
                    if item.is_dir():
                        continue
                    if Path(item.filename).suffix.lower() not in FONT_EXTS and not Path(item.filename).name.upper().startswith(("OFL","LICENSE","COPYING")):
                        continue
                    with archive.open(item) as stream:
                        copy(stream, item.filename, item.file_size)
        elif source.suffix.lower() in FONT_EXTS:
            with source.open("rb") as stream:
                copy(stream, source.name, source.stat().st_size)
            for pattern in ("OFL*", "LICENSE*", "COPYING*"):
                for license_ in source.parent.glob(pattern):
                    if license_.is_file() and not license_.is_symlink():
                        with license_.open("rb") as stream:
                            copy(stream, license_.name, license_.stat().st_size)
        else:
            raise ValueError(f"Unsupported font import: {source.name}")
    if not candidates:
        raise ValueError("No supported fonts found")
    metadata = [inspect_font(path) for path in candidates]
    families = list(dict.fromkeys(m["family"] for m in metadata))
    digest = hashlib.sha256()
    for path in sorted(candidates):
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return {"families":families,"metadata":metadata,"digest":digest.hexdigest(),"files":candidates}


def family_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ValueError("Use a valid font family name")
    return value.strip()


def fontconfig_xml(rules, disabled=()):
    root = ET.Element("fontconfig")
    if disabled:
        rejected = ET.SubElement(ET.SubElement(root,"selectfont"),"rejectfont")
        for path in sorted(set(map(str,disabled))):
            if not Path(path).is_absolute() or any(c in path for c in "\n\r\x00"):
                raise ValueError("Disabled font paths must be absolute")
            pattern=ET.SubElement(rejected,"pattern")
            ET.SubElement(ET.SubElement(pattern,"patelt",name="file"),"string").text=path
    emoji = rules.get("emoji", "")
    if emoji:
        alias = ET.SubElement(root,"alias",binding="strong")
        ET.SubElement(alias,"family").text = "emoji"
        ET.SubElement(ET.SubElement(alias,"prefer"),"family").text = family_name(emoji)
    substitutions = rules.get("substitutions", {})
    if not isinstance(substitutions, dict) or len(substitutions) > 100:
        raise ValueError("Use at most 100 font substitutions")
    for source,target in substitutions.items():
        alias = ET.SubElement(root,"alias",binding="strong")
        ET.SubElement(alias,"family").text = family_name(source)
        ET.SubElement(ET.SubElement(alias,"prefer"),"family").text = family_name(target)
    hinting = rules.get("hinting", {})
    if not isinstance(hinting, dict) or len(hinting) > 100:
        raise ValueError("Use at most 100 hinting rules")
    for family,style in hinting.items():
        if style not in ("none","slight","medium","full"):
            raise ValueError("Unknown hinting style")
        match = ET.SubElement(root,"match",target="font")
        ET.SubElement(ET.SubElement(match,"test",name="family",compare="eq"),"string").text = family_name(family)
        ET.SubElement(ET.SubElement(match,"edit",name="hinting",mode="assign"),"bool").text = "false" if style == "none" else "true"
        ET.SubElement(ET.SubElement(match,"edit",name="hintstyle",mode="assign"),"const").text = "hint" + style
    return '<?xml version="1.0"?>\n<!-- Generated by Orbits Font Preferences -->\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n' + ET.tostring(root,encoding="unicode") + "\n"


def write_fontconfig(path, rules, disabled=()):
    text = fontconfig_xml(rules, disabled)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and "Generated by Orbits Font Preferences" not in path.read_text(errors="ignore"):
        backup=path.with_name(path.name+".orbits-backup")
        if not backup.exists():shutil.copy2(path,backup,follow_symlinks=False)
    with tempfile.NamedTemporaryFile(mode="w",dir=path.parent,delete=False) as stream:
        stream.write(text)
        temp = Path(stream.name)
    try:
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


PAIRINGS = [("Playfair Display","Source Sans 3"),("Lora","Inter"),("Montserrat","Source Serif 4"),
            ("Merriweather","Open Sans"),("Space Grotesk","Noto Sans"),("DejaVu Serif","DejaVu Sans")]


def suggest_pairings(families):
    available = set(families)
    return [{"heading":heading,"body":body,"installed":heading in available and body in available}
            for heading,body in PAIRINGS]
