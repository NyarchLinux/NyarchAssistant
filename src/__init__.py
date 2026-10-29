import builtins
import gettext

# The launcher installs the translation for the active locale. Keep a fallback
# so modules imported outside the launcher still have gettext helpers available.
if not hasattr(builtins, "_"):
    gettext.install("nyarchassistant")
