"""Transfer a dedicated server's PalWorldSettings.ini into a local WorldOption.sav.

A dedicated server keeps its world settings in
``Saved/Config/WindowsServer/PalWorldSettings.ini``; a local world keeps the same
settings in ``WorldOption.sav`` inside the world folder. Converting the save
alone leaves the world running on default rules -- rates, death penalty,
difficulty and base-camp limits all revert.

Every key in the ini's ``OptionSettings=(...)`` blob has a counterpart field in
``WorldOption.sav``, so the transfer is a direct field-by-field copy. The save
holds many more fields than the ini does (voice chat, PvP drops, respawn
penalties and so on); those are left at the template's values.

``WorldOption.sav`` round-trips through the parser byte-for-byte, unlike
``Level.sav``, so this one is edited structurally rather than by byte patching.
"""

import os
import re
from typing import Optional

# Server infrastructure and credentials. These exist in WorldOption.sav but mean
# nothing for a local world, and AdminPassword in particular should not be copied
# into a save file that might get shared.
SERVER_ONLY_FIELDS = {
    "AdminPassword",
    "ServerPassword",
    "ServerName",
    "ServerDescription",
    "ServerPlayerMaxNum",
    "PublicIP",
    "PublicPort",
    "RCONEnabled",
    "RCONPort",
    "RESTAPIEnabled",
    "RESTAPIPort",
    "Region",
    "bUseAuth",
    "BanListURL",
}


def parse_ini(ini_path: str) -> dict[str, str]:
    """Pull OptionSettings=( ... ) out of PalWorldSettings.ini as a flat dict."""
    with open(ini_path, encoding="utf-8-sig") as f:
        text = f.read()
    match = re.search(r"OptionSettings\s*=\s*\((.*)\)", text, re.S)
    if not match:
        raise SystemExit(f"no OptionSettings=(...) block found in {ini_path}")
    values = {}
    for m in re.finditer(r'(\w+)\s*=\s*("(?:[^"]*)"|[^,]*)', match.group(1)):
        raw = m.group(2).strip()
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            raw = raw[1:-1]
        values[m.group(1)] = raw
    return values


def find_template(explicit: Optional[str] = None) -> str:
    """Any existing local world's WorldOption.sav works as a template.

    Every field the ini covers gets overwritten, so the template only supplies
    the fields the ini has no opinion about.
    """
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit(f"template not found: {explicit}")
        return explicit
    root = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Pal", "Saved", "SaveGames"
    )
    candidates = []
    for dirpath, _, files in os.walk(root):
        if "WorldOption.sav" in files:
            candidates.append(os.path.join(dirpath, "WorldOption.sav"))
    if not candidates:
        raise SystemExit(
            "No WorldOption.sav found to use as a template.\n"
            "Create any world in-game once with your preferred settings, or pass\n"
            "--settings-template <path to a WorldOption.sav>."
        )
    return max(candidates, key=os.path.getmtime)


def _apply(field: dict, ini_value: str) -> object:
    """Write one ini value into a parsed property, returning the value written."""
    ftype = field["type"]
    if ftype == "BoolProperty":
        value = ini_value.strip().lower() == "true"
        field["value"] = value
    elif ftype == "IntProperty":
        value = int(float(ini_value))
        field["value"] = value
    elif ftype in ("FloatProperty", "DoubleProperty"):
        value = float(ini_value)
        field["value"] = value
    elif ftype in ("StrProperty", "NameProperty"):
        value = ini_value
        field["value"] = value
    elif ftype == "EnumProperty":
        # The ini stores the bare member ("All"); the save stores it qualified
        # ("EPalOptionWorldDeathPenalty::All"). Reuse the template's prefix.
        current = field["value"]["value"]
        prefix = current.split("::")[0] if "::" in current else field["value"]["type"]
        value = ini_value if "::" in ini_value else f"{prefix}::{ini_value}"
        field["value"]["value"] = value
    else:
        raise ValueError(f"unhandled property type {ftype}")
    return value


def transfer(
    ini_path: str,
    world_folder: str,
    template: Optional[str] = None,
    include_server_fields: bool = False,
) -> dict:
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
    from palworld_save_tools.paltypes import (
        PALWORLD_CUSTOM_PROPERTIES,
        PALWORLD_TYPE_HINTS,
    )

    ini = parse_ini(ini_path)
    template_path = find_template(template)

    with open(template_path, "rb") as f:
        raw, _ = decompress_sav_to_gvas(f.read())
    gvas = GvasFile.read(
        raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
    )

    # Refuse to work from a template we cannot reproduce exactly.
    if gvas.write(PALWORLD_CUSTOM_PROPERTIES) != raw:
        raise SystemExit(
            f"template {template_path} does not round-trip losslessly; refusing to "
            "write a settings file built from it"
        )

    settings = gvas.properties["OptionWorldData"]["value"]["Settings"]["value"]

    result = {
        "template": template_path,
        "applied": [],
        "changed": [],
        "skipped_server": [],
        "unmapped": [],
    }
    for key, value in ini.items():
        if key not in settings:
            result["unmapped"].append(key)
            continue
        if key in SERVER_ONLY_FIELDS and not include_server_fields:
            result["skipped_server"].append(key)
            continue
        field = settings[key]
        before = field["value"]["value"] if field["type"] == "EnumProperty" else field["value"]
        after = _apply(field, value)
        result["applied"].append(key)
        if before != after:
            result["changed"].append((key, before, after))

    out_path = os.path.join(world_folder, "WorldOption.sav")
    payload = gvas.write(PALWORLD_CUSTOM_PROPERTIES)
    with open(out_path, "wb") as f:
        f.write(compress_gvas_to_sav(payload, 0x31))
    result["out"] = out_path

    # Read it back and confirm every applied value survived.
    with open(out_path, "rb") as f:
        check_raw, _ = decompress_sav_to_gvas(f.read())
    check = GvasFile.read(
        check_raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
    )
    verified = check.properties["OptionWorldData"]["value"]["Settings"]["value"]
    mismatches = []
    for key in result["applied"]:
        want = settings[key]
        got = verified[key]
        a = want["value"]["value"] if want["type"] == "EnumProperty" else want["value"]
        b = got["value"]["value"] if got["type"] == "EnumProperty" else got["value"]
        if a != b:
            mismatches.append((key, a, b))
    result["mismatches"] = mismatches
    return result


def main(argv=None) -> int:
    import argparse

    from . import bootstrap

    p = argparse.ArgumentParser(
        prog="ded2solo.settings",
        description="Copy a dedicated server's PalWorldSettings.ini into a local "
        "world's WorldOption.sav.",
    )
    p.add_argument("--ini", required=True, help="path to PalWorldSettings.ini")
    p.add_argument(
        "--world-folder",
        required=True,
        help="the local world folder to write WorldOption.sav into",
    )
    p.add_argument("--settings-template", help="a WorldOption.sav to use as the base")
    p.add_argument(
        "--include-server-fields",
        action="store_true",
        help="also copy server-only fields (admin password, ports, RCON, IP). "
        "Skipped by default -- they do nothing locally and the password would "
        "travel with the save.",
    )
    args = p.parse_args(argv)

    bootstrap.load()
    if not os.path.isdir(args.world_folder):
        raise SystemExit(f"world folder does not exist: {args.world_folder}")

    r = transfer(
        args.ini,
        args.world_folder,
        template=args.settings_template,
        include_server_fields=args.include_server_fields,
    )
    print(f"template: {r['template']}")
    print(f"\napplied {len(r['applied'])} settings; {len(r['changed'])} differ from the template:")
    for key, before, after in r["changed"]:
        print(f"  {key:44s} {str(before):26s} -> {after}")
    if r["skipped_server"]:
        print(f"\nskipped {len(r['skipped_server'])} server-only fields: "
              f"{', '.join(sorted(r['skipped_server']))}")
    if r["unmapped"]:
        print(f"\nini keys with no save field: {', '.join(r['unmapped'])}")
    print(f"\nwrote {r['out']}")
    if r["mismatches"]:
        print("\nFAILED -- values did not survive the write:")
        for key, a, b in r["mismatches"]:
            print(f"  {key}: wrote {a!r}, read back {b!r}")
        return 1
    print("verified: every applied value reads back correctly\n  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
