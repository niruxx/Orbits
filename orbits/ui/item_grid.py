"""Data-backed grids: only bound list items own cards and preview textures.

Keep this widget directly inside a ScrolledWindow so GTK receives a viewport
and can recycle offscreen rows. Headers and footers belong outside that scroller.
"""
from gi.repository import Gio, GObject, Gtk
from orbits.preview_jobs import cancel_pending


class GridItem(GObject.Object):
    def __init__(self, value):
        super().__init__()
        self.value = value


def release_previews(widget):
    """Invalidate pending callbacks and release paintables when a card is unbound."""
    cancel_pending(widget)
    widget._grid_released = True
    if hasattr(widget, '_texture_request'):
        widget._texture_request = None
    if hasattr(widget, '_font_generation'):
        widget._font_generation += 1
    if hasattr(widget, 'set_paintable'):
        widget.set_paintable(None)
    child = widget.get_first_child()
    while child is not None:
        release_previews(child)
        child = child.get_next_sibling()


class ItemGrid(Gtk.GridView):
    def __init__(self, create_card, activate=None, *, max_columns=8,
                 css_class='wall-grid', single_click=True):
        self.store = Gio.ListStore.new(GridItem)
        self.predicate = lambda value: True
        self.filter = Gtk.CustomFilter.new(lambda item: self.predicate(item.value))
        self.filtered = Gtk.FilterListModel.new(self.store, self.filter)
        self.bound = {}
        self.create_card = create_card
        factory = Gtk.SignalListItemFactory()
        factory.connect('bind', self._bind)
        factory.connect('unbind', self._unbind)
        super().__init__(model=Gtk.NoSelection.new(self.filtered), factory=factory,
                         min_columns=1, max_columns=max_columns,
                         single_click_activate=single_click, css_classes=[css_class],
                         margin_start=24, margin_end=24, margin_top=12, margin_bottom=24,
                         vexpand=True)
        if activate:
            self.connect('activate', lambda _grid, pos: activate(self.filtered.get_item(pos).value))

    def _bind(self, _factory, list_item):
        card = self.create_card(list_item.get_item().value)
        list_item.set_child(card)
        self.bound[list_item] = card

    def _unbind(self, _factory, list_item):
        card = self.bound.pop(list_item, None)
        if card is not None:
            release_previews(card)
        list_item.set_child(None)

    def set_items(self, values):
        self.store.splice(0, self.store.get_n_items(), [GridItem(value) for value in values])

    def append(self, value):
        self.store.append(GridItem(value))

    def remove_all(self):
        self.store.remove_all()

    def set_filter_func(self, predicate):
        self.predicate = predicate
        self.invalidate_filter()

    def invalidate_filter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def refresh(self, update):
        for card in list(self.bound.values()):
            update(card)

    def find_card(self, predicate):
        return next((card for card in self.bound.values() if predicate(card)), None)
