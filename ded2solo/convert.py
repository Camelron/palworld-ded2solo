"""Convert a Palworld dedicated-server world into a local single-player world.

The whole conversion is done by same-length, in-place byte patching of the
*decompressed* GVAS payload. A full parse -> re-encode round trip is deliberately
never used to produce output: on post-2026-update saves it is not byte-lossless
(it drops bytes inside MapObjectSaveData and BaseCampSaveData and re-serialises
GroupSaveDataMap differently). Parsing is used only to locate byte offsets and to
verify the result.

Because every edit swaps one 16-byte GUID for another, nothing in the file can
shift, and structures the Python decoders still cannot parse -- notably
CharacterContainerSaveData and the guild blobs -- are patched correctly anyway.
"""

import collections
import os
import shutil
from typing import Optional

HOST_GUID = "00000000-0000-0000-0000-000000000001"
ZERO_GUID = "00000000-0000-0000-0000-000000000000"

_KEY_PATH = ".worldSaveData.CharacterSaveParameterMap.Key"

# Byte offset of the PlayerUId GUID value from the start of a CharacterSaveParameterMap
# key struct. The key is a fixed property layout:
#   "PlayerUId"(4+10) + "StructProperty"(4+15) + size u64(8) + "Guid"(4+5)
#   + struct_id guid(16) + optional-guid flag(1) = 67
# Every offset is asserted against the parsed value before anything is written,
# so a layout change fails loudly instead of corrupting the save.
_PLAYER_UID_OFFSET = 67


def guid_to_filename(guid: str) -> str:
    """'ba558fc5-0000-...' -> 'BA558FC5000000000000000000000000'."""
    return guid.replace("-", "").upper()


def filename_to_guid(name: str) -> str:
    """'BA558FC5000000000000000000000000' -> 'ba558fc5-0000-0000-0000-000000000000'."""
    h = os.path.splitext(os.path.basename(name))[0].lower()
    if len(h) != 32:
        raise ValueError(f"expected a 32-character player id, got {name!r}")
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _save_type_for(gvas) -> int:
    name = gvas.header.save_game_class_name
    if "Pal.PalWorldSaveGame" in name or "Pal.PalLocalWorldSaveGame" in name:
        return 0x32
    return 0x31


def read_level(level_path: str):
    """Decompress and parse Level.sav, recording every map-key byte offset."""
    from palworld_save_tools.archive import FArchiveReader
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import decompress_sav_to_gvas
    from palworld_save_tools.paltypes import (
        PALWORLD_CUSTOM_PROPERTIES,
        PALWORLD_TYPE_HINTS,
    )

    with open(level_path, "rb") as f:
        raw, _ = decompress_sav_to_gvas(f.read())

    offsets: list[int] = []
    original = FArchiveReader.prop_value

    def traced(self, type_name, struct_type_name, path):
        if path != _KEY_PATH:
            return original(self, type_name, struct_type_name, path)
        start = self.data.tell()
        value = original(self, type_name, struct_type_name, path)
        offsets.append(start)
        return value

    FArchiveReader.prop_value = traced
    try:
        gvas = GvasFile.read(
            raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
        )
    finally:
        FArchiveReader.prop_value = original

    entries = gvas.properties["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    if len(offsets) != len(entries):
        raise SystemExit(
            f"internal error: traced {len(offsets)} map keys but parsed {len(entries)} entries"
        )
    return raw, gvas, entries, offsets


def describe(entry) -> tuple[bool, Optional[str], Optional[str], Optional[int]]:
    """-> (is_player, owner_guid_or_None, nickname, level)"""
    sp = (
        entry["value"]["RawData"]["value"]
        .get("object", {})
        .get("SaveParameter", {})
        .get("value", {})
    )
    owner = sp.get("OwnerPlayerUId", {}).get("value")
    level = sp.get("Level", {}).get("value")
    if isinstance(level, dict):
        level = level.get("value")
    return (
        bool(sp.get("IsPlayer", {}).get("value")),
        str(owner) if owner is not None else None,
        sp.get("NickName", {}).get("value"),
        level,
    )


def list_characters(world: str) -> list[dict]:
    _, _, entries, _ = read_level(os.path.join(world, "Level.sav"))
    pals = collections.Counter()
    found = []
    for entry in entries:
        is_player, owner, nickname, level = describe(entry)
        if is_player:
            found.append(
                {
                    "guid": str(entry["key"]["PlayerUId"]["value"]),
                    "name": nickname,
                    "level": level,
                }
            )
        elif owner is not None:
            pals[owner] += 1
    for p in found:
        p["pals"] = pals.get(p["guid"], 0)
    return found


def convert(
    world: str,
    player_guid: str,
    out: str,
    absorb_other_players: bool = False,
    keep_other_player_files: bool = True,
) -> dict:
    from palworld_save_tools.archive import UUID
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
    from palworld_save_tools.paltypes import (
        PALWORLD_CUSTOM_PROPERTIES,
        PALWORLD_TYPE_HINTS,
    )

    old_raw_guid = UUID.from_str(player_guid).raw_bytes
    new_raw_guid = UUID.from_str(HOST_GUID).raw_bytes
    zero_raw_guid = UUID.from_str(ZERO_GUID).raw_bytes
    old_file = guid_to_filename(player_guid)
    new_file = guid_to_filename(HOST_GUID)

    os.makedirs(os.path.join(out, "Players"), exist_ok=True)
    stats: dict = {"plan": collections.Counter()}

    raw, gvas, entries, offsets = read_level(os.path.join(world, "Level.sav"))
    buf = bytearray(raw)

    names = {}
    rekey_at = []
    for offset, entry in zip(offsets, entries):
        parsed = entry["key"]["PlayerUId"]["value"]
        at = offset + _PLAYER_UID_OFFSET
        if bytes(buf[at : at + 16]) != parsed.raw_bytes:
            raise SystemExit(
                "The CharacterSaveParameterMap key layout is not what this tool "
                f"expects (offset {at}). The save format has probably changed; "
                "refusing to write anything."
            )
        is_player, owner, nickname, _ = describe(entry)
        if is_player:
            names[str(parsed)] = nickname
            stats["plan"]["player entry (re-keyed by the GUID swap)"] += 1
            continue
        if str(parsed) != ZERO_GUID:
            stats["plan"]["pal with a non-zero key (left alone)"] += 1
            continue
        if owner == player_guid or owner is None or absorb_other_players:
            rekey_at.append(at)
            if owner == player_guid:
                stats["plan"]["pal owned by the chosen player -> re-keyed"] += 1
            elif owner is None:
                stats["plan"]["unowned / base-camp pal -> re-keyed"] += 1
            else:
                stats["plan"]["pal owned by another player -> re-keyed (--absorb)"] += 1
        else:
            stats["plan"]["pal owned by another player (left zero-keyed)"] += 1

    if player_guid not in names:
        raise SystemExit(
            f"No player character with GUID {player_guid} exists in this world.\n"
            "Run with --list to see the characters it contains."
        )

    # 1) Swap the chosen player's GUID for the local host GUID, everywhere.
    stats["guid_swaps"] = buf.count(old_raw_guid)
    i = buf.find(old_raw_guid)
    while i != -1:
        buf[i : i + 16] = new_raw_guid
        i = buf.find(old_raw_guid, i + 16)

    # 2) Re-key pals from the zero GUID to the host GUID. A local save keys every
    #    character -- player, pals and base workers alike -- to the host; a
    #    dedicated server keys pals to the zero GUID instead. Skipping this leaves
    #    the pals present in the file but invisible in game.
    for at in rekey_at:
        if bytes(buf[at : at + 16]) != zero_raw_guid:
            raise SystemExit(f"unexpected key value at offset {at}; refusing to write")
        buf[at : at + 16] = new_raw_guid
    stats["rekeyed"] = len(rekey_at)

    if len(buf) != len(raw):
        raise SystemExit("internal error: payload length changed")

    level_type = _save_type_for(gvas)
    with open(os.path.join(out, "Level.sav"), "wb") as f:
        f.write(compress_gvas_to_sav(bytes(buf), level_type))
    stats["level_save_type"] = level_type
    stats["player_name"] = names[player_guid]

    # ---- player saves ----
    players_dir = os.path.join(world, "Players")
    stats["players"] = []
    for fn in sorted(os.listdir(players_dir)):
        src = os.path.join(players_dir, fn)
        if not fn.upper().startswith(old_file):
            if keep_other_player_files:
                shutil.copy2(src, os.path.join(out, "Players", fn))
                stats["players"].append((fn, fn, 0))
            continue
        with open(src, "rb") as f:
            praw, _ = decompress_sav_to_gvas(f.read())
        pgvas = GvasFile.read(
            praw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
        )
        pbuf = bytearray(praw)
        swaps = pbuf.count(old_raw_guid)
        j = pbuf.find(old_raw_guid)
        while j != -1:
            pbuf[j : j + 16] = new_raw_guid
            j = pbuf.find(old_raw_guid, j + 16)
        dst_name = new_file + fn[len(old_file) :]
        with open(os.path.join(out, "Players", dst_name), "wb") as f:
            f.write(compress_gvas_to_sav(bytes(pbuf), _save_type_for(pgvas)))
        stats["players"].append((fn, dst_name, swaps))

    # ---- LevelMeta.sav ----
    # Converters routinely forget this one. It holds WorldName and InGameDay, and
    # without it the world simply does not appear in the game's world list.
    meta = os.path.join(world, "LevelMeta.sav")
    if os.path.exists(meta):
        shutil.copy2(meta, os.path.join(out, "LevelMeta.sav"))
        stats["levelmeta"] = True
    else:
        stats["levelmeta"] = False

    local_data = os.path.join(world, "LocalData.sav")
    if os.path.exists(local_data):
        shutil.copy2(local_data, os.path.join(out, "LocalData.sav"))

    return stats


def verify(world: str, out: str, player_guid: str) -> dict:
    """Re-read the converted world and confirm it says what it should."""
    from palworld_save_tools.archive import UUID
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import decompress_sav_to_gvas
    from palworld_save_tools.paltypes import (
        PALWORLD_CUSTOM_PROPERTIES,
        PALWORLD_TYPE_HINTS,
    )

    old_raw_guid = UUID.from_str(player_guid).raw_bytes
    with open(os.path.join(world, "Level.sav"), "rb") as f:
        src_raw, _ = decompress_sav_to_gvas(f.read())
    with open(os.path.join(out, "Level.sav"), "rb") as f:
        new_raw, _ = decompress_sav_to_gvas(f.read())

    result = {
        "length_preserved": len(new_raw) == len(src_raw),
        "residual_old_guid": new_raw.count(old_raw_guid),
        "bytes_changed": sum(1 for a, b in zip(new_raw, src_raw) if a != b),
    }

    gvas = GvasFile.read(
        new_raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
    )
    entries = gvas.properties["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    result["entries"] = len(entries)
    host = [
        e
        for e in entries
        if str(e["key"]["PlayerUId"]["value"]) == HOST_GUID and describe(e)[0]
    ]
    result["host_entries"] = len(host)
    result["host_name"] = describe(host[0])[2] if host else None
    result["host_keyed"] = sum(
        1 for e in entries if str(e["key"]["PlayerUId"]["value"]) == HOST_GUID
    )

    player_sav = os.path.join(
        out, "Players", guid_to_filename(HOST_GUID) + ".sav"
    )
    with open(player_sav, "rb") as f:
        praw, _ = decompress_sav_to_gvas(f.read())
    pg = GvasFile.read(
        praw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
    )
    sd = pg.properties["SaveData"]["value"]
    result["player_uid"] = str(sd["PlayerUId"]["value"])
    result["player_residual_old_guid"] = praw.count(old_raw_guid)
    instance = str(sd["IndividualId"]["value"]["InstanceId"]["value"])
    result["player_linked"] = any(
        str(e["key"]["InstanceId"]["value"]) == instance for e in host
    )
    return result
