"""Portable assets for Looks, including GNOME XML slideshow frames."""
import math
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

IMAGE_EXTS = {".jpg",".jpeg",".png",".webp",".avif",".jxl",".svg",".bmp",".tif",".tiff",".heic",".heif"}


def rewrite_slideshow(text, resolve):
    if len(text)>2<<20 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Invalid or oversized slideshow XML")
    root=ET.fromstring(text)
    allowed={"background","starttime","year","month","day","hour","minute","second","static","transition","duration","file","from","to","size"}
    nodes=list(root.iter())
    if root.tag!="background" or len(nodes)>3000 or any(n.tag not in allowed for n in nodes):
        raise ValueError("Unsupported slideshow XML")
    total=0
    for node in nodes:
        if node.tag=="duration":
            value=float(node.text or "0")
            if not math.isfinite(value) or value<=0:raise ValueError("Invalid slideshow duration")
            total+=value
        if node.tag in ("file","from","to","size") and len(node)==0:
            value=(node.text or "").strip()
            if not value:raise ValueError("Empty slideshow image reference")
            node.text=str(resolve(value))
    if not 0<total<=604800:raise ValueError("Slideshow must last no more than one week")
    return ET.tostring(root,encoding="unicode",xml_declaration=True)


def copy_background(source, directory, stem):
    source=Path(source);directory=Path(directory)
    destination=directory/(stem+source.suffix.lower())
    if source.suffix.lower()!=".xml":
        if source.resolve()!=destination.resolve():shutil.copy2(source,destination)
        return str(destination)
    copied={}
    def resolve(value):
        path=Path(value)
        if not path.is_absolute():path=source.parent/path
        path=path.resolve()
        if path.suffix.lower() not in IMAGE_EXTS or not path.is_file():raise ValueError("Slideshow references an unsupported or missing image")
        if path not in copied:
            target=directory/f"{stem}-frame-{len(copied):03d}{path.suffix.lower()}"
            if path!=target.resolve():shutil.copyfile(path,target)
            copied[path]=str(target)
        return copied[path]
    destination.write_text(rewrite_slideshow(source.read_text(),resolve))
    return str(destination)


def archive_background(archive,source,stem):
    source=Path(source)
    name=stem+source.suffix.lower()
    if source.suffix.lower()!=".xml":
        archive.write(source,name)
        return name
    copied={}
    def resolve(value):
        path=Path(value)
        if not path.is_absolute():path=source.parent/path
        path=path.resolve()
        if path.suffix.lower() not in IMAGE_EXTS or not path.is_file():raise ValueError("Missing slideshow image")
        if path not in copied:
            target=f"{stem}-frame-{len(copied):03d}{path.suffix.lower()}"
            archive.write(path,target);copied[path]=target
        return copied[path]
    archive.writestr(name,rewrite_slideshow(source.read_text(),resolve))
    return name
