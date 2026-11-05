"""Resolve a theme's public source page, including older rEFInd installations."""
import re
from urllib.parse import urlsplit


def web_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""


def source_url(metadata):
    if not isinstance(metadata, dict):
        return ""
    for field in ("page", "page_url"):
        url = web_url(metadata.get(field))
        if url:
            return url
    # Older boot-theme markers retained the GitHub owner/repo in their item key.
    key = metadata.get("key", "")
    if isinstance(key, str) and key.startswith("boot-refind:"):
        repository = key.removeprefix("boot-refind:")
        if re.fullmatch(r"[A-Za-z0-9_.-]+~[A-Za-z0-9_.-]+", repository):
            owner, name = repository.split("~")
            if owner not in (".", "..") and name not in (".", ".."):
                return f"https://github.com/{owner}/{name}"
    return ""
