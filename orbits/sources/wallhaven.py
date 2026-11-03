"""Wallhaven.cc public API (SFW only, no key required)."""
from orbits.config import settings
from orbits.net import get_json
from orbits.sources.base import Download, Item, Source

API = "https://wallhaven.cc/api/v1/search"


class WallhavenSource(Source):
    id = "wallhaven"
    name = "Wallhaven"
    icon = "camera-photo-symbolic"
    tagline = "High-resolution wallpapers from wallhaven.cc"
    sorts = [("toplist", "Top"), ("date_added", "Latest"), ("favorites", "Favorites"),
             ("views", "Most Viewed"), ("random", "Random")]
    categories = [("100", "General"), ("111", "Everything"), ("010", "Anime"), ("001", "People")]

    def __init__(self):
        self._seed = None

    def fetch(self, query, sort, category, page):
        params = {
            "categories": category or "100",
            "purity": "100",
            "sorting": sort or "toplist",
            "order": "desc",
            "page": page,
        }
        if settings.get("wallhaven_min_res"):
            params["atleast"] = settings.get("wallhaven_min_res")
        if settings.get("wallhaven_api_key"):
            params["apikey"] = settings.get("wallhaven_api_key")
        if sort == "toplist":
            params["topRange"] = settings.get("wallhaven_top_range") or "1y"
        if query:
            params["q"] = query
        if sort == "random":
            if page == 1:
                self._seed = None
            if self._seed:
                params["seed"] = self._seed
        data = get_json(API, params)
        meta = data.get("meta") or {}
        self._seed = meta.get("seed") or self._seed
        items = []
        for d in data.get("data") or []:
            ext = d["path"].rsplit(".", 1)[-1]
            items.append(
                Item(
                    source=self.id,
                    id=d["id"],
                    title=f"{d.get('category', 'wallpaper').title()} · {d['resolution']}",
                    author="",
                    thumb_url=d["thumbs"]["large"],
                    preview_url=d["thumbs"]["original"],
                    page_url=d["url"],
                    resolution=d["resolution"],
                    stats=f"{d.get('views', 0):,} views · {d.get('favorites', 0):,} favorites",
                    downloads=[Download(f"wallhaven-{d['id']}.{ext}", d["path"], d.get("file_size", 0))],
                    colors=d.get("colors") or [],
                )
            )
        return items, meta.get("current_page", page) < meta.get("last_page", page)
