Name:           orbits
Version:        0.3.0
Release:        1%{?dist}
Summary:        Desktop appearance and GDM login screen manager
License:        GPL-3.0-or-later
URL:            https://github.com/niruxx/Orbits
Source0:        %{url}/releases/download/v%{version}/orbits-%{version}.tar.xz
BuildArch:      noarch
BuildRequires:  meson ninja-build gettext python3-devel python3-gobject python3-pillow python3-fonttools
BuildRequires:  gtk4 libadwaita glib2 fontconfig gsettings-desktop-schemas
Requires:       python3-gobject python3-pillow python3-fonttools gtk4 libadwaita >= 1.7
Requires:       glib2 polkit dconf fontconfig gsettings-desktop-schemas
Recommends:     gdm gnome-shell xdg-desktop-portal

%description
Discover wallpapers, fonts and themes, save desktop Looks and customize GDM.

%prep
%autosetup

%build
%meson
%meson_build

%install
%meson_install

%check
%meson_test

%preun
if [ "$1" -eq 0 ]; then
  /usr/bin/python3 -I /usr/lib/orbits/orbits/gdm/helper.py reset || exit 1
fi

%files
%license LICENSE
%doc README.md
%{_bindir}/orbits
/usr/lib/orbits/
%{_datadir}/applications/io.github.niruxx.Orbits.desktop
%{_datadir}/dbus-1/services/io.github.niruxx.Orbits.SearchProvider.service
%{_datadir}/gnome-shell/search-providers/io.github.niruxx.Orbits.search-provider.ini
%{_datadir}/polkit-1/actions/io.github.niruxx.Orbits.policy
%{_datadir}/metainfo/io.github.niruxx.Orbits.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.niruxx.Orbits.svg
%{_datadir}/doc/orbits/
%{_datadir}/locale/*/LC_MESSAGES/orbits.mo
