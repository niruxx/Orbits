"""Boot archive layout handling and data-only staging regressions."""
from pathlib import Path
import tempfile
import unittest

from orbits import boot


class BootImportTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='orbits-test-boot-import-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.source=self.root/'archive'
        self.source.mkdir()

    def theme(self, relative='repo-main/themes/Dark', config='theme.conf'):
        directory=self.source/relative
        directory.mkdir(parents=True,exist_ok=True)
        (directory/config).write_text('timeout 5\n')
        return directory

    def test_nested_case_varied_configs_are_discovered_and_canonicalized(self):
        dark=self.theme(config='Theme.conf')
        light=self.theme('repo-main/themes/Light')
        found=boot.find_boot_themes(self.source,'refind','archive')
        self.assertEqual(found,[(dark,'Dark'),(light,'Light')])
        staged=self.root/'staged'
        boot.stage_boot_theme(dark,staged,'refind')
        self.assertTrue((staged/'theme.conf').is_file())
        self.assertFalse((staged/'Theme.conf').exists())

    def test_repository_metadata_is_discarded_instead_of_installed(self):
        theme=self.theme()
        for name in ('.gitattributes','.gittattribute','.gitignore','.gitmodules','.gitkeep'):
            (theme/name).write_bytes(b'not a theme asset\x00')
        (theme/'.github/workflows').mkdir(parents=True)
        (theme/'.github/workflows/build.yml').write_text('irrelevant CI configuration')
        staged=self.root/'staged'
        boot.stage_boot_theme(theme,staged,'refind')
        self.assertEqual([path.name for path in staged.iterdir()],['theme.conf'])

    def test_scripts_and_links_are_still_rejected_as_theme_assets(self):
        theme=self.theme()
        (theme/'install.sh').write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError,'Executable'):
            boot.stage_boot_theme(theme,self.root/'staged-script','refind')
        (theme/'install.sh').unlink()
        (theme/'asset.png').symlink_to(self.root/'elsewhere')
        with self.assertRaisesRegex(ValueError,'links'):
            boot.stage_boot_theme(theme,self.root/'staged-link','refind')

    def test_ambiguous_config_names_are_rejected(self):
        theme=self.theme()
        (theme/'Theme.conf').write_text('timeout 10\n')
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            boot.find_boot_themes(self.source,'refind','archive')
        with self.assertRaisesRegex(ValueError,'one regular'):
            boot.stage_boot_theme(theme,self.root/'staged','refind')

    def test_repository_metadata_is_not_searched_for_themes(self):
        self.theme('.github/theme')
        self.assertEqual(boot.find_boot_themes(self.source,'refind','archive'),[])

    def test_missing_config_in_generator_with_submodule_has_useful_error(self):
        repository=self.source/'ghoul-cyber-theme-studio-main'
        repository.mkdir()
        (repository/'theme_studio.py').write_text('not executed')
        (repository/'.gitmodules').write_text('[submodule "theme"]\npath = theme\n')
        (repository/'theme').mkdir()
        self.assertEqual(boot.find_boot_themes(self.source,'refind','studio'),[])
        message=boot.missing_boot_theme_message(self.source,'refind','studio.zip')
        self.assertIn('editor or generator',message)
        self.assertIn('Git submodules',message)
        self.assertIn('all nested folders',message.lower())
        self.assertIn('theme.conf',message)

    def test_installer_only_and_template_archives_explain_missing_config(self):
        (self.source/'install.sh').write_text('not executed')
        (self.source/'theme.conf.template').write_text('template data')
        message=boot.missing_boot_theme_message(self.source,'refind','theme.zip')
        self.assertIn('configuration template',message)
        self.assertIn('does not execute downloaded scripts',message)

    def test_named_refind_flavours_share_assets_and_get_individual_theme_configs(self):
        repository=self.source/'refind-main'
        repository.mkdir()
        for flavour in ('latte','frappe','macchiato','mocha'):
            (repository/(flavour+'.conf')).write_text('hideui hints\nbanner_scale fillscreen\n')
        (repository/'assets').mkdir()
        found=boot.find_boot_themes(self.source,'refind','catppuccin')
        self.assertEqual([name for _,name in found],
                         ['refind-frappe','refind-latte','refind-macchiato','refind-mocha'])
        for source,name in found:
            staged=self.root/name
            boot.stage_boot_theme(source,staged,'refind')
            self.assertEqual((staged/'theme.conf').read_text(),source.read_text())
            self.assertTrue((staged/'assets').is_dir())

    def test_main_configs_includes_and_boot_entries_are_not_discovered_as_flavours(self):
        for name,text in [('refind.conf','hideui hints\n'),
                          ('nested.conf','include other.conf\nhideui hints\n'),
                          ('boot.conf','menuentry Linux {\nloader linux.efi\n}\nhideui hints\n'),
                          ('unrelated.conf','setting arbitrary-value\n')]:
            (self.source/name).write_text(text)
        self.assertEqual(boot.find_boot_themes(self.source,'refind','archive'),[])

    def test_canonical_config_takes_precedence_over_named_alternatives(self):
        theme=self.theme()
        (theme/'flavour.conf').write_text('hideui hints\n')
        self.assertEqual(boot.find_boot_themes(self.source,'refind','archive'),[(theme,'Dark')])

    def test_grub_config_is_found_and_normalized_without_including_metadata(self):
        theme=self.theme(config='Theme.txt')
        (theme/'.gitattributes').write_text('* text=auto\n')
        found=boot.find_boot_themes(self.source,'grub','grub')
        self.assertEqual(found,[(theme,'Dark')])
        boot.stage_boot_theme(theme,self.root/'grub-staged','grub')
        self.assertTrue((self.root/'grub-staged/theme.txt').is_file())


if __name__=='__main__':unittest.main()
