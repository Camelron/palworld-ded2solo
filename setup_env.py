"""One-time setup: build ./vendor with a patched palworld-save-tools and libooz.

Nothing is written outside this repo -- no global pip install, no site-packages
mutation. Re-running is safe; pass --force to redo the libooz download.
"""

import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")
PATCHES = os.path.join(ROOT, "patches", "palworld_save_tools")

PST_VERSION = "0.24.0"
OOZ_VERSION = "0.2.4"
OOZ_URL = (
    f"https://github.com/zao/ooz/releases/download/v{OOZ_VERSION}"
    f"/bun-{OOZ_VERSION}-x64-Release.zip"
)


def install_upstream() -> None:
    print(f"Installing palworld-save-tools=={PST_VERSION} into vendor/ ...")
    subprocess.check_call(
        [
            sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
            "--target", VENDOR, f"palworld-save-tools=={PST_VERSION}",
        ]
    )


def apply_patches() -> None:
    """Overlay the patched decoders onto the freshly installed package."""
    target = os.path.join(VENDOR, "palworld_save_tools")
    if not os.path.isdir(target):
        raise SystemExit(f"expected {target} to exist after the pip install")
    count = 0
    for root, _, files in os.walk(PATCHES):
        for name in files:
            if not name.endswith(".py"):
                continue
            src = os.path.join(root, name)
            rel = os.path.relpath(src, PATCHES)
            dst = os.path.join(target, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    print(f"Applied {count} patched files over vendor/palworld_save_tools/")


def fetch_ooz(force: bool = False) -> None:
    dest_dir = os.path.join(VENDOR, "ooz")
    dll = os.path.join(dest_dir, "libooz.dll")
    if os.path.exists(dll) and not force:
        print(f"libooz.dll already present at {dll}")
        return
    print(f"Downloading {OOZ_URL} ...")
    with urllib.request.urlopen(OOZ_URL) as resp:
        payload = resp.read()
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith("libooz.dll")]
        if not names:
            raise SystemExit(f"libooz.dll not in the archive: {zf.namelist()}")
        with zf.open(names[0]) as src, open(dll, "wb") as out:
            out.write(src.read())
    print(f"Extracted {dll}")


def main() -> None:
    force = "--force" in sys.argv
    install_upstream()
    apply_patches()
    fetch_ooz(force=force)
    print("\nSetup complete. Try:\n    python -m ded2solo --world <world folder> --list")


if __name__ == "__main__":
    main()
