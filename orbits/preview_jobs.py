"""Cancel queued previews when a widget is rebound or leaves a virtualized grid."""


def cancel_pending(widget):
    futures = getattr(widget, '_preview_futures', ())
    if isinstance(futures, (list, tuple)):
        for future in futures:
            future.cancel()
    widget._preview_futures = []


def track(widget, future):
    if future is None:
        return
    futures = getattr(widget, '_preview_futures', None)
    if not isinstance(futures, list):
        futures = []
        widget._preview_futures = futures
    futures[:] = [existing for existing in futures if not existing.done()]
    futures.append(future)
