"""Boot mount failures remain visible and never produce a success notification."""
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

import orbits

ROOT=Path(__file__).resolve().parents[1]


class BootErrorTests(unittest.TestCase):
    def setUp(self):
        self.adw=types.SimpleNamespace(Bin=object,AlertDialog=Mock(),ResponseAppearance=Mock())
        self.helper=Mock()
        net=types.SimpleNamespace()
        boot=types.SimpleNamespace()
        repository=types.SimpleNamespace(Adw=self.adw,Gio=Mock(),GLib=Mock(),Gtk=Mock(),Gdk=Mock(),GObject=Mock())
        stubs={'gi.repository':repository,'orbits.boot':boot,'orbits.net':net,
               'orbits.config':types.SimpleNamespace(settings={}),
               'orbits.library':types.SimpleNamespace(get_library=Mock()),
               'orbits.privileged':types.SimpleNamespace(run_helper=self.helper),
               'orbits.ui.widgets':types.SimpleNamespace(**{name:Mock() for name in
                   ('WallCard','CoverPicture','load_texture_into','status_page','open_uri')})}
        with patch.dict('sys.modules',stubs),patch.object(orbits,'boot',boot,create=True),patch.object(orbits,'net',net,create=True):
            spec=importlib.util.spec_from_file_location('test_boot_browse',ROOT/'orbits/ui/browse.py')
            self.browse=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.browse)
            with patch.dict('sys.modules',{'orbits.ui.browse':self.browse}):
                spec=importlib.util.spec_from_file_location('test_boot_error_page',ROOT/'orbits/ui/boot_page.py')
                self.module=importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.module)
        self.page=object.__new__(self.module.LoaderView)
        self.page.window=Mock()
        self.page.loader=types.SimpleNamespace(id='refind',name='rEFInd')
        self.page.manager=Mock()
        self.page._busy=Mock()

    def test_read_only_failure_presents_reason_in_a_popup(self):
        error='/boot/EFI/refind/themes is on a read-only filesystem; changes blocked'
        self.page.apply('refind-macchiato')
        self.helper.call_args.args[1](False,error)
        popup=self.adw.AlertDialog
        self.assertEqual(popup.call_args.kwargs['heading'],'Boot Partition Is Read-Only')
        self.assertIn(error,popup.call_args.kwargs['body'])
        self.assertIn('mounted writable',popup.call_args.kwargs['body'])
        popup.return_value.present.assert_called_once_with(self.page.window)
        self.page.window.toast.assert_not_called()
        self.page.manager.scan.assert_not_called()
        self.page._busy.assert_called_with(None)

    def test_erofs_os_error_also_gets_a_popup(self):
        self.page._report_error("[Errno 30] Read-only file system: '/boot/EFI/refind/refind.conf'")
        self.adw.AlertDialog.return_value.present.assert_called_once_with(self.page.window)

    def test_authentication_failure_is_not_misreported_as_a_read_only_mount(self):
        self.page._report_error('Authentication was cancelled')
        self.adw.AlertDialog.assert_not_called()
        self.page.window.toast.assert_called_once_with('Authentication was cancelled',timeout=6)

    def test_success_keeps_toast_and_refreshes_installed_state(self):
        self.page.apply('refind-macchiato')
        self.helper.call_args.args[1](True,'applied')
        self.adw.AlertDialog.assert_not_called()
        self.page.manager.scan.assert_called_once()
        self.page.window.toast.assert_called_once()

    def test_refind_default_confirmation_calls_builtin_default_operation(self):
        self.page.reset()
        dialog=self.adw.AlertDialog.return_value
        response=dialog.connect.call_args.args[1]
        response(dialog,'reset')
        self.assertEqual(self.helper.call_args.args[0],['boot-theme-default','refind'])
        self.assertIn('built-in appearance',self.adw.AlertDialog.call_args.kwargs['body'])

    def test_store_install_failure_uses_the_boot_error_popup(self):
        page=object.__new__(self.browse.BrowsePage)
        page.lib=Mock()
        page.lib.is_installed.return_value=False
        page.grid=Mock()
        page.window=self.page.window
        page._on_install_error=self.page._report_error
        item=types.SimpleNamespace(key='theme:example')
        page.install(item)
        done=page.lib.install.call_args.args[2]
        done(None,RuntimeError('/efi is on a read-only filesystem'))
        self.adw.AlertDialog.return_value.present.assert_called_once_with(self.page.window)
        page.window.toast.assert_not_called()


if __name__=='__main__':unittest.main()
