# Building packages

Run `meson setup build --prefix=/usr`, `meson compile -C build`, and
`meson test -C build --print-errorlogs`. For inspection without installing on the
host, use `DESTDIR=/tmp/orbits-stage meson install -C build`.

The core test target requires Pillow and FontTools. GTK, PyGObject, libadwaita,
fontconfig, dconf and polkit are runtime dependencies; the complete desktop suite
is also available through `sh tests/run.sh` on a suitably equipped machine.

## Releases and AUR

`python3 packaging/build-release.py` creates a deterministic
`dist/orbits-<version>.tar.xz`, its SHA256 file, and `dist/aur/` containing PKGBUILD,
.SRCINFO and install hooks. The recipe points to a GitHub release asset and pins
the exact generated archive hash; it does not use SKIP checksums. Generate it
from the final release tree. Publish the archive under a matching `v<version>`
tag before building the AUR package. Tag pushes run the source-release workflow;
this does not submit anything to AUR. The generated AUR files can then be
reviewed and submitted separately. `packaging/arch/PKGBUILD` remains the local
checkout build for development. Release metadata is versioned at 0.3.0.

## Debian / Ubuntu

On a distribution with GTK >= 4.14 and libadwaita >= 1.7:

1. Extract the release archive and copy `packaging/debian/` to `debian/` in it.
2. Place the upstream archive beside the source directory as
   `orbits_0.3.0.orig.tar.xz`.
3. Install the Build-Depends from debian/control, then run
   `dpkg-buildpackage -us -uc`.

The maintainer field is a placeholder for a distribution maintainer to replace.
The removal script restores Orbits' GDM settings before removing the helper.

## Fedora

Place the release archive in `~/rpmbuild/SOURCES/` and run
`rpmbuild -ba packaging/fedora/orbits.spec` with the listed BuildRequires.
The RPM includes the launcher, policy, translations and Shell search provider.

Meson builds and staged installation are checked in this environment. Debian and
RPM recipes still require actual distribution builds and desktop integration
checks; no packages or releases are published by local build commands.
