#!/bin/sh
# Regenerate po/POTFILES and the translation template from current sources.
# Uses only standard tools (grep, xgettext, msgmerge).
cd "$(dirname "$0")" || exit 1

# List Python files that actually contain gettext literal calls ("_("…")"),
# so xgettext noise (e.g. __init__) does not end up in POTFILES.
grep -rl --include='*.py' -e '_("' -e "_('" -e '_n(' src | sort >po/POTFILES
ls data/*.xml.in data/*.desktop.in data/*.gschema.xml 2>/dev/null >>po/POTFILES

# xgettext picks the language from each extension: Python for .py and C for
# the XML/desktop templates. Keep the data files in this pass so msgmerge does
# not obsolete their translations.
xgettext --from-code=UTF-8 --add-comments \
	--keyword=_ --keyword=N_ --keyword=gettext \
	--keyword=ngettext --keyword=_n:1,2 \
	--files-from=po/POTFILES -o po/nyarchassistant.pot

for file in po/*.po; do
	[ -e "$file" ] || break
	msgmerge --update "$file" po/nyarchassistant.pot
done
rm -f po/*~
