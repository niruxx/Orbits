project('orbits', version: '0.3.0', meson_version: '>= 0.63.0', license: 'GPL-3.0-or-later')
python = import('python').find_installation('python3')
i18n = import('i18n')
app_root = get_option('prefix') / 'lib' / 'orbits'
configuration = configuration_data()
configuration.set('APP_ROOT', app_root)
configuration.set('EXEC', get_option('prefix') / get_option('bindir') / 'orbits')
install_subdir('orbits', install_dir: app_root,
  exclude_directories: ['__pycache__', 'ui/__pycache__', 'gdm/__pycache__', 'sources/__pycache__'])
configure_file(input: 'packaging/orbits.in', output: 'orbits', configuration: configuration,
  install: true, install_dir: get_option('bindir'), install_mode: 'rwxr-xr-x')
# The helper must be executable for the dedicated polkit action.
install_data('orbits/gdm/helper.py', install_dir: app_root / 'orbits/gdm', install_mode: 'rwxr-xr-x')
configure_file(input: 'data/io.github.niruxx.Orbits.desktop.in', output: 'io.github.niruxx.Orbits.desktop',
  configuration: configuration, install_dir: get_option('datadir') / 'applications')
configure_file(input: 'data/io.github.niruxx.Orbits.SearchProvider.service.in',
  output: 'io.github.niruxx.Orbits.SearchProvider.service', configuration: configuration,
  install_dir: get_option('datadir') / 'dbus-1/services')
install_data('data/io.github.niruxx.Orbits.search-provider.ini',
  install_dir: get_option('datadir') / 'gnome-shell/search-providers')
configure_file(input: 'data/io.github.niruxx.Orbits.policy.in', output: 'io.github.niruxx.Orbits.policy',
  configuration: configuration, install_dir: get_option('datadir') / 'polkit-1/actions')
install_data('data/io.github.niruxx.Orbits.metainfo.xml', install_dir: get_option('datadir') / 'metainfo')
install_data('data/icons/hicolor/scalable/apps/io.github.niruxx.Orbits.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps')
install_data('README.md', 'LICENSE', install_dir: get_option('datadir') / 'doc/orbits')
subdir('po')
test('core', python, args: ['-m', 'unittest', 'tests.test_good_to_haves', 'tests.test_gdm_features',
  'tests.test_fonts_looks', 'tests.test_theme_features', 'tests.test_wallpaper_tools',
  'tests.test_download_security', 'tests.test_image_safety', 'tests.test_remaining_security', 'tests.test_final_security', 'tests.test_wallpaper_state', 'tests.test_preview_loading', 'tests.test_ui_models', 'tests.test_font_collections', 'tests.test_theme_preview_security', 'tests.test_preferences', 'tests.test_preview_performance', 'tests.test_clamav', 'tests.test_boot_imports', 'tests.test_boot_errors', 'tests.test_theme_cleanup', 'tests.test_theme_defaults', 'tests.test_malware_rejection'], workdir: meson.current_source_dir(), timeout: 120)
