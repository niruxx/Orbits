"""Scheduling and preview-size regressions without network requests or timing benchmarks."""
from concurrent.futures import CancelledError
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class PreviewPerformanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='orbits-test-preview-performance-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repository = types.SimpleNamespace(Gdk=Mock(), GLib=Mock())
        config = types.SimpleNamespace(THUMB_CACHE=self.root)
        with patch.dict('sys.modules', {'gi.repository':repository, 'orbits.config':config}):
            spec = importlib.util.spec_from_file_location('performance_net',ROOT/'orbits/net.py')
            self.net = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.net)
        self.addCleanup(lambda:self.net._pool.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(lambda:self.net._preview_pool.shutdown(wait=True, cancel_futures=True))

    def occupy_preview_workers(self):
        ready = threading.Barrier(5)
        release = threading.Event()
        # Release blocked tasks before the executor cleanup joins its workers.
        self.addCleanup(release.set)
        def blocked():
            ready.wait(timeout=5)
            release.wait(timeout=5)
        futures = [self.net.run_preview(blocked) for _ in range(4)]
        ready.wait(timeout=5)
        return release, futures

    def test_install_work_starts_while_all_preview_workers_are_busy(self):
        release, previews = self.occupy_preview_workers()
        try:
            foreground = self.net.run_async(lambda:'install can start')
            self.assertEqual(foreground.result(timeout=2), 'install can start')
            self.assertTrue(all(not future.done() for future in previews))
        finally:
            release.set()

    def test_cancelled_preview_never_runs_or_posts_a_callback(self):
        release, _ = self.occupy_preview_workers()
        work, callback = Mock(), Mock()
        try:
            queued = self.net.run_preview(work, callback)
            self.assertTrue(queued.cancel())
            with self.assertRaises(CancelledError):queued.result()
            work.assert_not_called()
            callback.assert_not_called()
            self.net.GLib.idle_add.assert_not_called()
        finally:
            release.set()

    def test_remote_thumbnail_uses_requested_protected_decode_size(self):
        url = 'https://example.test/preview.png'
        cached = self.root/hashlib.sha1(url.encode()).hexdigest()
        cached.write_bytes(b'original encoded image')
        normalized = self.root/'normalized.png'
        normalized.write_bytes(b'generated PNG')
        with patch('orbits.image_safety.normalize_images',return_value=[normalized]) as decode:
            self.net.cached_texture(url, size=320)
        self.assertEqual(decode.call_args.args[0], cached)
        self.assertEqual(decode.call_args.kwargs, {'size':320})
        self.net.Gdk.Texture.new_from_bytes.assert_called_once()

    def test_invalid_preview_size_is_rejected_before_fetching(self):
        with patch.object(self.net,'get_bytes') as fetch:
            for size in (0, 4097, True, 1.5):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    self.net.cached_texture('https://example.test/preview.png', size=size)
            fetch.assert_not_called()
