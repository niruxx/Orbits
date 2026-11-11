"""Static community Looks catalogues. Importing never applies a remote Look."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

MAX_CATALOG = 2 << 20
MAX_LOOK = 512 << 20


def secure_url(value):
    parsed=urlparse(value)
    if parsed.scheme!="https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Community catalogues require HTTPS URLs without credentials")
    return value


def parse_catalog(data,location):
    if not isinstance(data,dict) or data.get("format")!=1 or not isinstance(data.get("looks"),list):
        raise ValueError("Not an Orbits community catalogue")
    remote=urlparse(str(location)).scheme=="https"
    base=str(location) if remote else Path(location).resolve().parent
    if len(data["looks"])>500:raise ValueError("Catalogue contains too many Looks")
    records=[];seen=set()
    def resolve(value):
        if not isinstance(value,str) or not value:raise ValueError("Missing catalogue asset")
        if remote:return secure_url(urljoin(base,value))
        path=(base/value).resolve()
        if not path.is_relative_to(base):raise ValueError("Catalogue asset escapes its folder")
        return str(path)
    for raw in data["looks"]:
        if not isinstance(raw,dict):raise ValueError("Invalid catalogue entry")
        id_=str(raw.get("id") or "")
        digest=raw.get("sha256","")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",id_) or id_ in seen:raise ValueError("Catalogue IDs must be unique")
        if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-fA-F]{64}",digest):raise ValueError("Every Look needs a SHA-256 checksum")
        seen.add(id_)
        records.append({"id":id_,"name":str(raw.get("name") or id_)[:100],"author":str(raw.get("author") or "")[:100],
                        "description":str(raw.get("description") or "")[:2000],"download":resolve(raw.get("download")),
                        "screenshot":resolve(raw["screenshot"]) if raw.get("screenshot") else "", "sha256":digest.lower()})
    return records


def load_catalog(location):
    from orbits import net
    if urlparse(str(location)).scheme:
        secure_url(str(location))
        with net._open(str(location)) as response:
            secure_url(response.geturl())
            raw=response.read(MAX_CATALOG+1)
    else:
        path=Path(location).expanduser()
        if path.stat().st_size>MAX_CATALOG:raise ValueError("Catalogue is too large")
        raw=path.read_bytes()
    if len(raw)>MAX_CATALOG:raise ValueError("Catalogue is too large")
    return parse_catalog(json.loads(raw),location)


def fetch_look(record,destination):
    from orbits import net
    location=record["download"]
    digest=hashlib.sha256();total=0
    source=net._open(secure_url(location),timeout=120) if urlparse(location).scheme else open(location,"rb")
    destination=Path(destination)
    try:
        with source, destination.open("wb") as output:
            if urlparse(location).scheme:secure_url(source.geturl())
            while chunk:=source.read(1<<16):
                total+=len(chunk)
                if total>MAX_LOOK:raise ValueError("Community Look exceeds 512 MiB")
                digest.update(chunk);output.write(chunk)
        if digest.hexdigest()!=record["sha256"]:raise ValueError("Community Look checksum did not match")
        if urlparse(location).scheme == 'https':
            from orbits.publishers import configured_verify
            configured_verify(destination, location)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def export_bundle(store,look,directory,author=""):
    import uuid
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    id_="look-"+uuid.uuid4().hex[:12]
    archive=directory/f"{id_}.orbits"
    store.export(look,archive)
    with archive.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
    record={"id":id_,"name":look.get("name","Look"),"author":author,"description":"",
            "download":archive.name,"sha256":digest}
    screenshot=look.get("screenshot")
    if screenshot and Path(screenshot).is_file():
        import shutil
        target=directory/(id_+Path(screenshot).suffix.lower())
        shutil.copyfile(screenshot,target);record["screenshot"]=target.name
    manifest=directory/"index.json"
    data={"format":1,"looks":[]}
    if manifest.exists():
        previous=json.loads(manifest.read_text())
        parse_catalog(previous,manifest)
        data=previous
    data["looks"].append(record)
    manifest.write_text(json.dumps(data,indent=2))
    return manifest
