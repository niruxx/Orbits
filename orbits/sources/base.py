from dataclasses import dataclass, field


@dataclass
class Download:
    name: str
    url: str
    size: int = 0  # bytes, 0 if unknown


@dataclass
class Item:
    source: str
    id: str
    title: str
    author: str = ""
    thumb_url: str = ""
    preview_url: str = ""
    page_url: str = ""
    resolution: str = ""
    description: str = ""
    stats: str = ""
    downloads: list[Download] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    updated: str = ""  # ISO timestamp of the item's last change upstream, when the store reports one

    @property
    def key(self):
        return f"{self.source}:{self.id}"


class Source:
    id = ""
    name = ""
    icon = "image-x-generic-symbolic"
    tagline = ""
    supports_search = True
    sorts: list[tuple[str, str]] = []
    categories: list[tuple[str, str]] = []

    def fetch(self, query: str, sort: str, category: str, page: int) -> tuple[list[Item], bool]:
        """Return (items, has_more). Runs in a worker thread. page starts at 1."""
        raise NotImplementedError
