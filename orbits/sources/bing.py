"""Bing's daily wallpaper archive (last ~2 weeks), in UHD."""
from orbits.net import get_json
from orbits.sources.base import Download, Item, Source

API = "https://www.bing.com/HPImageArchive.aspx"
BASE = "https://www.bing.com"


class BingSource(Source):
    id = "bing"
    name = "Bing Daily"
    icon = "weather-clear-symbolic"
    tagline = "A stunning new photo every day"
    supports_search = False
    sorts = []
    categories = [("en-US", "United States"), ("en-GB", "United Kingdom"), ("de-DE", "Germany"),
                  ("fr-FR", "France"), ("ja-JP", "Japan"), ("zh-CN", "China"), ("en-IN", "India"),
                  ("pt-BR", "Brazil"), ("en-CA", "Canada"), ("es-ES", "Spain"), ("it-IT", "Italy")]

    def fetch(self, query, sort, category, page):
        if page > 2:
            return [], False
        data = get_json(API, {"format": "js", "idx": (page - 1) * 8, "n": 8,
                              "mkt": category or "en-US", "uhd": 1})
        items = []
        for d in data.get("images") or []:
            base = BASE + d["urlbase"]
            copyright_ = d.get("copyright", "")
            title, _, credit = copyright_.partition(" (©")
            items.append(
                Item(
                    source=self.id,
                    id=d.get("hsh") or d["startdate"],
                    title=d.get("title") or title,
                    author=("©" + credit.rstrip(")")) if credit else "",
                    thumb_url=base + "_800x480.jpg",
                    preview_url=base + "_1920x1080.jpg",
                    page_url=d.get("copyrightlink") or BASE,
                    resolution="3840x2160",
                    description=title,
                    stats=f"Featured {d['startdate'][:4]}-{d['startdate'][4:6]}-{d['startdate'][6:]}",
                    downloads=[Download(f"bing-{d['startdate']}.jpg", base + "_UHD.jpg")],
                )
            )
        return items, page == 1 and len(items) == 8
