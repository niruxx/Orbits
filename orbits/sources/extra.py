"""NASA APOD, Openverse and authenticated Reddit wallpaper discovery."""
from datetime import date, timedelta
import html
import re
from urllib.parse import urlparse

from orbits.config import settings
from orbits.net import get_json
from orbits.sources.base import Download, Item, Source


class NasaSource(Source):
    id, name, icon = "nasa", "NASA APOD", "weather-clear-night-symbolic"
    tagline = "Astronomy Picture of the Day · NASA"
    supports_search = False
    sorts, categories = [], []

    def fetch(self, query, sort, category, page):
        end = date.today() - timedelta(days=(page - 1) * 12)
        start = max(date(1995, 6, 16), end - timedelta(days=11))
        if end < start:
            return [], False
        data = get_json("https://api.nasa.gov/planetary/apod", {
            "api_key": settings.get("nasa_api_key") or "DEMO_KEY", "start_date": start.isoformat(), "end_date": end.isoformat()})
        if not isinstance(data, list):
            raise RuntimeError(data.get("msg") or "NASA returned an invalid response")
        items = []
        for rec in reversed(data):
            if rec.get("media_type") != "image":
                continue
            url = rec.get("hdurl") or rec["url"]
            suffix = urlparse(url).path.rsplit(".", 1)[-1].lower()
            if suffix not in ("jpg", "jpeg", "png", "webp"):
                suffix = "jpg"
            items.append(Item(source=self.id, id=rec["date"], title=rec.get("title", rec["date"]),
                              author=rec.get("copyright") or "NASA / APOD", thumb_url=rec["url"], preview_url=url,
                              page_url="https://apod.nasa.gov/apod/ap" + rec["date"].replace("-", "")[2:] + ".html",
                              description=rec.get("explanation", ""), stats="APOD · " + rec["date"],
                              downloads=[Download(f"nasa-{rec['date']}.{suffix}", url)]))
        return items, start > date(1995, 6, 16)


class OpenverseSource(Source):
    id, name, icon = "openverse", "Openverse", "image-x-generic-symbolic"
    tagline = "Openly licensed images · check each image's license"
    sorts, categories = [], []

    def fetch(self, query, sort, category, page):
        data = get_json("https://api.openverse.org/v1/images/", {
            "q": query or "landscape", "page": page, "page_size": 24, "mature": "false"})
        items = []
        for rec in data.get("results", []):
            url = rec.get("url", "")
            if not url.startswith("https://"):
                continue
            suffix = urlparse(url).path.rsplit(".", 1)[-1].lower()
            if suffix not in ("jpg", "jpeg", "png", "webp", "avif"):
                suffix = "jpg"
            license_ = (rec.get("license", "") + " " + rec.get("license_version", "")).strip()
            width, height = rec.get("width"), rec.get("height")
            items.append(Item(source=self.id, id=rec["id"], title=html.unescape(rec.get("title") or "Untitled"),
                              author=rec.get("creator") or "Unknown creator", thumb_url=rec.get("thumbnail") or url,
                              preview_url=url, page_url=rec.get("foreign_landing_url") or url,
                              resolution=f"{width}x{height}" if width and height else "",
                              description=f"License: {license_}\n{rec.get('license_url', '')}\n{rec.get('attribution', '')}",
                              stats=license_.upper(), downloads=[Download(f"openverse-{rec['id']}.{suffix}", url)]))
        return items, page < min(data.get("page_count", page), 100)


class RedditSource(Source):
    id, name, icon = "reddit", "Reddit", "system-users-symbolic"
    tagline = "Wallpaper communities · requires your Reddit OAuth token"
    sorts = [("hot", "Hot"), ("new", "New"), ("top", "Top this Week")]
    categories = [("wallpapers", "r/wallpapers"), ("wallpaper", "r/wallpaper"), ("EarthPorn", "r/EarthPorn")]

    def __init__(self):
        self._after = {}

    def fetch(self, query, sort, category, page):
        token = settings.get("reddit_token")
        if not token:
            raise RuntimeError("Add a Reddit OAuth access token in Preferences → Sources")
        subreddit = category if category in dict(self.categories) else "wallpapers"
        params = {"limit": 24, "raw_json": 1, "t": "week"}
        key = (query, sort, subreddit)
        if page > 1:
            if not self._after.get(key):
                return [], False
            params["after"] = self._after[key]
        route = "search" if query else sort or "hot"
        if query:
            params.update(q=query, restrict_sr="on", sort="relevance")
        data = get_json(f"https://oauth.reddit.com/r/{subreddit}/{route}", params,
                        headers={"Authorization": f"Bearer {token}"})["data"]
        self._after[key] = data.get("after")
        items = []
        for child in data.get("children", []):
            rec = child["data"]
            if rec.get("over_18") or rec.get("is_video"):
                continue
            url = rec.get("url_overridden_by_dest") or rec.get("url", "")
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in ("i.redd.it", "i.imgur.com") or not re.search(r"\.(jpg|jpeg|png|webp)$", parsed.path, re.I):
                continue
            suffix = parsed.path.rsplit(".", 1)[-1]
            previews = ((rec.get("preview") or {}).get("images") or [{}])[0].get("source", {})
            items.append(Item(source=self.id, id=rec["id"], title=rec.get("title", "Wallpaper"), author=rec.get("author", ""),
                              thumb_url=url, preview_url=url, page_url="https://www.reddit.com" + rec["permalink"],
                              resolution=f"{previews['width']}x{previews['height']}" if previews.get("width") else "",
                              stats=f"r/{subreddit} · {rec.get('score', 0)} points",
                              downloads=[Download(f"reddit-{rec['id']}.{suffix}", url)]))
        return items, bool(data.get("after"))
