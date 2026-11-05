from orbits.sources.bing import BingSource
from orbits.sources.kde import KdeStoreSource
from orbits.sources.wallhaven import WallhavenSource

from orbits.sources.extra import NasaSource, OpenverseSource, RedditSource

SOURCES = [KdeStoreSource(), WallhavenSource(), BingSource(), NasaSource(), OpenverseSource(), RedditSource()]
BY_ID = {s.id: s for s in SOURCES}
