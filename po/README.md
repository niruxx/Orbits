Orbits uses the `orbits` gettext domain. UI strings use `_()` or `tr()`; formatted
messages use named placeholders so translators can reorder them. Configuration
keys, source IDs, filenames and user-provided names must not be translated.

Update the template from the repository root:

    xgettext --language=Python --from-code=UTF-8 --keyword=_ --keyword=tr \
      --keyword=ngettext:1,2 --files-from=po/POTFILES --output=po/orbits.pot

Add a language to LINGUAS, create its .po file with msginit, and merge updates with
msgmerge. Validate with `msgfmt --check`. Meson compiles and installs catalogues;
install.sh installs Spanish when msgfmt is available. ORBITS_LOCALEDIR can select
a compiled catalogue directory during development. The Spanish catalogue is an
initial translation, with English fallback for untranslated messages.
