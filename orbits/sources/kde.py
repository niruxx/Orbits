"""KDE Store (store.kde.org / pling) via the OCS API — the same backend Plasma's "Get New Wallpapers" uses."""
import html
import re

from orbits.config import settings
from orbits.net import get_json
from orbits.sources.base import Download, Item, Source

API = "https://api.kde-look.org/ocs/v1/content/data"
ALL_WALLPAPERS = "299x296x297x307x298x360x616"


def _clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class OcsSource(Source):
    """Any Pling-network store (store.kde.org, gnome-look.org, …) speaking the OCS v1 API."""

    api = API
    store_name = "KDE Store"
    default_category = ALL_WALLPAPERS
    page_size = 24

    def fetch(self, query, sort, category, page):
        self.page_size = int(settings.get("kde_page_size") or 24)
        params = {
            "categories": category or self.default_category,
            "sortmode": sort or "down",
            "page": page - 1,
            "pagesize": self.page_size,
            "format": "json",
        }
        if query:
            params["search"] = query
        data = get_json(self.api, params)
        if data.get("status") != "ok":
            raise RuntimeError(data.get("message") or f"{self.store_name} request failed")
        items = [self._parse(d) for d in data.get("data") or []]
        total = int(data.get("totalitems") or 0)
        return items, page * self.page_size < total


    def fetch_details(self, item_id):
        from urllib.parse import quote
        from orbits.ocs_details import details
        return details(get_json(f"{self.api}/{quote(str(item_id), safe='')}", {"format":"json"}))

    def fetch_comments(self, item_id, page=0):
        from urllib.parse import quote
        from orbits.ocs_details import comments
        base = self.api.rsplit("/content/data", 1)[0]
        result = get_json(f"{base}/comments/data/1/{quote(str(item_id), safe='')}/0",
                          {"format":"json", "page":page, "pagesize":10})
        return comments(result)

    def _parse(self, d):
        downloads = []
        for n in range(1, 50):
            url = d.get(f"downloadlink{n}")
            if url is None:
                break
            if url:
                downloads.append(Download(d.get(f"downloadname{n}") or f"file {n}", url,
                                          int(d.get(f"downloadsize{n}") or 0) * 1024,
                                          str(d.get(f"downloadmd5sum{n}") or "").strip()))
        return Item(
            source=self.id,
            id=str(d["id"]),
            title=html.unescape(d.get("name") or "Untitled"),
            author=d.get("personid") or "",
            thumb_url=d.get("smallpreviewpic1") or d.get("previewpic1") or "",
            preview_url=d.get("previewpic1") or "",
            page_url=d.get("detailpage") or "",
            description=_clean(d.get("description") or d.get("summary")),
            stats=f"{int(d.get('downloads') or 0):,} downloads · score {d.get('score', '–')}",
            downloads=downloads,
            updated=d.get("changed") or d.get("created") or "",
        )

    def get_item(self, item_id):
        """Fetch one item by id (worker thread)."""
        data = get_json(f"{self.api}/{item_id}", {"format": "json"})
        if data.get("status") != "ok" or not data.get("data"):
            raise RuntimeError(data.get("message") or "Item not found")
        return self._parse(data["data"][0])


class KdeStoreSource(OcsSource):
    id = "kde"
    name = "KDE Store"
    icon = "application-x-addon-symbolic"
    tagline = "Community wallpapers from store.kde.org"
    sorts = [("down", "Most Downloaded"), ("new", "Newest"), ("high", "Top Rated"), ("alpha", "A–Z")]
    categories = [
        (ALL_WALLPAPERS, "All Wallpapers"),
        ("299", "Plasma"),
        ("296", "Abstract"),
        ("297", "Animals"),
        ("298", "Nature"),
        ("307", "Beaches & Oceans"),
        ("360", "Linux / Tux"),
        ("616", "Mobile"),
    ]
