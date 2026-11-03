from orbits.sources.bing import BingSource
from orbits.sources.kde import KdeStoreSource
from orbits.sources.wallhaven import WallhavenSource

SOURCES = [KdeStoreSource(), WallhavenSource(), BingSource()]
BY_ID = {s.id: s for s in SOURCES}
