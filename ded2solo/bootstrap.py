"""Put the patched palworld-save-tools on sys.path and point it at libooz.

Both live under ``vendor/``, which ``setup_env.py`` populates. Nothing is
installed into the user's global site-packages.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")
OOZ_DLL = os.path.join(VENDOR, "ooz", "libooz.dll")


def _fail(message: str) -> "None":
    raise SystemExit(
        f"{message}\n\nRun the one-time setup first:\n    python setup_env.py\n"
    )


def load() -> None:
    """Make ``palworld_save_tools`` importable, with the extensions applied."""
    if not os.path.isdir(os.path.join(VENDOR, "palworld_save_tools")):
        _fail(f"Patched palworld-save-tools not found in {VENDOR}.")
    if not os.path.exists(OOZ_DLL):
        _fail(f"libooz.dll not found at {OOZ_DLL}.")

    if VENDOR not in sys.path:
        sys.path.insert(0, VENDOR)
    os.environ.setdefault("PALWORLD_OOZ_DLL_PATH", OOZ_DLL)

    from . import archive_ext

    archive_ext.apply()
