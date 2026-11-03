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


class KdeStoreSource(Source):
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
    def fetch(self, query, sort, category, page):
        self.page_size = int(settings.get("kde_page_size") or 24)
        params = {
            "categories": category or ALL_WALLPAPERS,
            "sortmode": sort or "down",
            "page": page - 1,
            "pagesize": self.page_size,
            "format": "json",
        }
        if query:
            params["search"] = query
        data = get_json(API, params)
        if data.get("status") != "ok":
            raise RuntimeError(data.get("message") or "KDE Store request failed")
        items = []
        for d in data.get("data") or []:
            downloads = []
            for n in range(1, 50):
                url = d.get(f"downloadlink{n}")
                if url is None:
                    break
                if url:
                    downloads.append(
                        Download(d.get(f"downloadname{n}") or f"file {n}", url,
                                 int(d.get(f"downloadsize{n}") or 0) * 1024)
                    )
            items.append(
                Item(
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
                )
            )
        total = int(data.get("totalitems") or 0)
        return items, page * self.page_size < total
