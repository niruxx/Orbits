"""gettext bootstrap, also used by headless helpers and source checkouts."""
import gettext
import os
import sys
from pathlib import Path

_user_locale = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'locale'
LOCALE_DIR = Path(os.environ.get('ORBITS_LOCALEDIR', _user_locale if (_user_locale/'es/LC_MESSAGES/orbits.mo').exists() else Path(sys.prefix)/'share/locale'))
_translation = gettext.translation('orbits', localedir=str(LOCALE_DIR), fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext
