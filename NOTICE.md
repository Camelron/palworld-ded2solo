# Third-party components

## palworld-save-tools — MIT, Copyright (c) 2024 Jun Siang Cheah

<https://github.com/cheahjs/palworld-save-tools>

Not redistributed in this repository. `setup_env.py` installs version 0.24.0 from
PyPI into `./vendor` at setup time.

`ded2solo/archive_ext.py` extends two reader and two writer methods of that
package at runtime, adding `SetProperty` support and the missing `Int64Property`
/ `FloatProperty` / `DoubleProperty` / `StrProperty` map-and-set value types. It
is written as delegating monkeypatches specifically so that no upstream source
needs to be copied.

## palworld-hostfix-toolkit — MIT, Copyright (c) 2026

<https://github.com/quadrantbs/palworld-hostfix-toolkit>

The files in `patches/palworld_save_tools/` are that project's patched copies of
palworld-save-tools modules, redistributed here under the MIT licence. They
provide Oodle (`PlM`) decompression in `palsav.py` and the hardened `rawdata`
decoders that tolerate the post-2026-update struct layouts. `setup_env.py`
overlays them onto the pip-installed package inside `./vendor`.

## ooz — Copyright (c) Zao and contributors

<https://github.com/zao/ooz>

`libooz.dll` is **not** redistributed here. `setup_env.py` downloads it directly
from the upstream v0.2.4 release, because that project publishes no explicit
licence. It is used only to *decompress* Oodle Kraken data; this tool never
compresses with Oodle, and writes zlib instead.

Oodle is a trademark of Epic Games / RAD Game Tools. `libooz` is a clean-room
reimplementation and contains none of their code.

---

Palworld is a trademark of Pocketpair, Inc. This project is not affiliated with,
endorsed by, or supported by Pocketpair.
