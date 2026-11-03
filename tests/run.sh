#!/bin/sh
# Run the test suite with an isolated XDG environment (never touches your real settings).
cd "$(dirname "$0")/.." || exit 1
T=$(mktemp -d -t orbits-test-XXXXXX)
trap 'rm -rf "$T"' EXIT
XDG_CONFIG_HOME="$T/config" XDG_DATA_HOME="$T/data" XDG_CACHE_HOME="$T/cache" GSETTINGS_BACKEND=memory \
    python3 -m unittest discover -s tests -t . "$@"
