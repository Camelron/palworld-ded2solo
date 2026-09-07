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
    sections: dict[str, tuple[int, int]] = {}
    original = FArchiveReader.prop_value
    original_property = FArchiveReader.property

    def traced(self, type_name, struct_type_name, path):
        if path != _KEY_PATH:
            return original(self, type_name, struct_type_name, path)
        start = self.data.tell()
        value = original(self, type_name, struct_type_name, path)
        offsets.append(start)
        return value

    def traced_property(self, type_name, size, path, nested_caller_path=""):
        # Record the byte span of each top-level worldSaveData section.
        top = path.startswith(".worldSaveData.") and path.count(".") == 2
        if not top:
            return original_property(self, type_name, size, path, nested_caller_path)
        start = self.data.tell()
        value = original_property(self, type_name, size, path, nested_caller_path)
        sections[path.rsplit(".", 1)[1]] = (start, self.data.tell())
        return value

    FArchiveReader.prop_value = traced
    FArchiveReader.property = traced_property
    try:
        gvas = GvasFile.read(
            raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
        )
    finally:
        FArchiveReader.prop_value = original
        FArchiveReader.property = original_property

    entries = gvas.properties["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    if len(offsets) != len(entries):
        raise SystemExit(
            f"internal error: traced {len(offsets)} map keys but parsed {len(entries)} entries"
        )
    return raw, gvas, entries, offsets, sections


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


def set_world_name(world_folder: str, name: str) -> tuple[str, str]:
    """Rename the world as shown in the game's world list.

    The name lives in LevelMeta.sav, which -- unlike Level.sav -- round-trips
    through the parser byte-for-byte, so this is a structural edit. The length
    change is why it cannot be done by byte patching.
    """
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
    from palworld_save_tools.paltypes import (
        PALWORLD_CUSTOM_PROPERTIES,
        PALWORLD_TYPE_HINTS,
    )

    path = os.path.join(world_folder, "LevelMeta.sav")
    if not os.path.exists(path):
        raise SystemExit(f"no LevelMeta.sav in {world_folder}; cannot set the world name")
    with open(path, "rb") as f:
        raw, save_type = decompress_sav_to_gvas(f.read())
    gvas = GvasFile.read(
        raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
    )
    if gvas.write(PALWORLD_CUSTOM_PROPERTIES) != raw:
        raise SystemExit(
            "LevelMeta.sav does not round-trip losslessly; refusing to rewrite it"
        )
    field = gvas.properties["SaveData"]["value"]["WorldName"]
    previous = field["value"]
    field["value"] = name
    with open(path, "wb") as f:
        f.write(compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type))

    with open(path, "rb") as f:
        check, _ = decompress_sav_to_gvas(f.read())
    reread = GvasFile.read(
        check, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True
    )
    got = reread.properties["SaveData"]["value"]["WorldName"]["value"]
    if got != name:
        raise SystemExit(f"world name did not stick: wrote {name!r}, read back {got!r}")
    return previous, name


def guild_membership(wsd, known_instances: set) -> list[dict]:
    """Every guild in the world, as {'index', 'members'} of 16-byte instance ids.

    Guild blobs usually fail to decode on current save versions, so members are
    recovered by scanning the raw blob for {guid, instance_id} handle pairs and
    keeping the ones whose instance id is a real character. Structurally decoded
    guilds are read directly.
    """
    from palworld_save_tools.archive import UUID

    guilds = []
    for index, group in enumerate(wsd["GroupSaveDataMap"]["value"]):
        if group["value"]["GroupType"]["value"]["value"] != "EPalGroupType::Guild":
            continue
        rd = group["value"]["RawData"]["value"]
        members = set()
        if isinstance(rd, dict) and "individual_character_handle_ids" in rd:
            for handle in rd["individual_character_handle_ids"]:
                inst = UUID.from_str(str(handle["instance_id"])).raw_bytes
                if inst in known_instances:
                    members.add(inst)
        elif isinstance(rd, dict) and "values" in rd:
            blob = bytes(rd["values"])
            for i in range(0, len(blob) - 32 + 1):
                inst = blob[i + 16 : i + 32]
                if inst in known_instances:
                    members.add(inst)
        guilds.append({"index": index, "members": members})
    return guilds


def list_characters(world: str) -> list[dict]:
    _, _, entries, _, _ = read_level(os.path.join(world, "Level.sav"))
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


def list_guilds(world: str, player_guid: Optional[str] = None) -> list[dict]:
    _raw, gvas, entries, _offsets, _sections = read_level(
        os.path.join(world, "Level.sav")
    )
    wsd = gvas.properties["worldSaveData"]["value"]
    from palworld_save_tools.archive import UUID

    def group_id_of(raw_data) -> Optional[str]:
        if not isinstance(raw_data, dict):
            return None
        if "group_id" in raw_data:
            return str(raw_data["group_id"])
        values = raw_data.get("values")
        if values and len(values) >= 16:
            return str(UUID(bytes(values[:16])))
        return None

    base_camps_by_group = collections.defaultdict(list)
    for base in wsd["BaseCampSaveData"]["value"]:
        rd = base["value"]["RawData"]["value"]
        if not isinstance(rd, dict) or "values" in rd:
            continue
        group_id = str(rd["group_id_belong_to"])
        base_camps_by_group[group_id].append(
            {
                "base_id": str(base["key"]),
                "base_point_id": str(rd["owner_map_object_instance_id"]),
            }
        )

    instances = {}
    names = {}
    for entry in entries:
        inst = str(entry["key"]["InstanceId"]["value"])
        is_player, _owner, nickname, level = describe(entry)
        instances[inst] = is_player
        if is_player:
            uid = str(entry["key"]["PlayerUId"]["value"])
            names[inst] = {
                "guid": uid,
                "name": nickname,
                "level": level,
            }

    guilds = []
    known_instances = {
        UUID.from_str(str(entry["key"]["InstanceId"]["value"])).raw_bytes: str(
            entry["key"]["InstanceId"]["value"]
        )
        for entry in entries
    }
    raw_memberships = guild_membership(wsd, set(known_instances))
    groups = wsd["GroupSaveDataMap"]["value"]
    for raw_membership in raw_memberships:
        index = raw_membership["index"]
        group = groups[index] if index < len(groups) else None
        raw_data = group["value"]["RawData"]["value"] if group is not None else {}
        group_id = group_id_of(raw_data)
        members = []
        contains_selected_player = False
        for raw_inst in raw_membership["members"]:
            inst = known_instances.get(raw_inst)
            player = names.get(inst)
            if player:
                members.append(player)
                if player_guid is not None and player["guid"] == player_guid:
                    contains_selected_player = True
        base_ids = []
        base_point_ids = []
        guild_name = None
        if group_id is not None:
            base_ids = [b["base_id"] for b in base_camps_by_group[group_id]]
            base_point_ids = [
                b["base_point_id"] for b in base_camps_by_group[group_id]
            ]
        if isinstance(raw_data, dict) and "values" not in raw_data:
            guild_name = raw_data.get("guild_name") or raw_data.get("group_name")
            base_ids = base_ids or [str(i) for i in raw_data.get("base_ids", [])]
            base_point_ids = base_point_ids or [
                str(i)
                for i in raw_data.get(
                    "map_object_instance_ids_base_camp_points", []
                )
            ]
        guilds.append(
            {
                "index": index,
                "id": group_id,
                "name": guild_name or "(unnamed guild)",
                "members": sorted(members, key=lambda p: (str(p["name"]), p["guid"])),
                "base_ids": base_ids,
                "base_point_ids": base_point_ids,
                "contains_selected_player": contains_selected_player,
            }
        )
    return guilds


def resolve_local_data(path: str) -> str:
    local_data = os.path.join(path, "LocalData.sav") if os.path.isdir(path) else path
    if not os.path.exists(local_data):
        raise SystemExit(f"--local-data does not exist: {local_data}")
    if os.path.basename(local_data).lower() != "localdata.sav":
        raise SystemExit(
            "--local-data must point at LocalData.sav or a world folder containing it"
        )
    return local_data


def convert(
    world: str,
    player_guid: str,
    out: str,
    absorb_other_players: bool = False,
    keep_other_player_files: bool = True,
    local_data: Optional[str] = None,
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

    raw, gvas, entries, offsets, sections = read_level(os.path.join(world, "Level.sav"))
    wsd = gvas.properties["worldSaveData"]["value"]
    buf = bytearray(raw)

    # Index every character by instance id, and find the chosen player's own instance.
    instances = {}
    target_instance = None
    names = {}
    for entry in entries:
        inst = UUID.from_str(str(entry["key"]["InstanceId"]["value"])).raw_bytes
        is_player, _, nickname, _ = describe(entry)
        instances[inst] = is_player
        if is_player:
            uid = str(entry["key"]["PlayerUId"]["value"])
            names[uid] = nickname
            if uid == player_guid:
                target_instance = inst

    if player_guid not in names:
        raise SystemExit(
            f"No player character with GUID {player_guid} exists in this world.\n"
            "Run with --list to see the characters it contains."
        )

    # The set to convert is the chosen player's *guild*, not merely the pals they
    # own. A guild also holds unowned base-camp workers, and other players' guilds
    # hold unowned workers of their own that must be left alone.
    guilds = guild_membership(wsd, set(instances))
    if absorb_other_players:
        convert_set = {i for g in guilds for i in g["members"] if not instances[i]}
    else:
        convert_set = set()
        for g in guilds:
            if target_instance in g["members"]:
                convert_set |= {i for i in g["members"] if not instances[i]}
    stats["guild_pals"] = len(convert_set)

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
        inst = UUID.from_str(str(entry["key"]["InstanceId"]["value"])).raw_bytes
        if instances[inst]:
            stats["plan"]["player entry (re-keyed by the GUID swap)"] += 1
            continue
        if str(parsed) != ZERO_GUID:
            stats["plan"]["pal with a non-zero key (left alone)"] += 1
            continue
        if inst in convert_set:
            rekey_at.append(at)
            stats["plan"]["pal in the chosen player's guild -> re-keyed"] += 1
        else:
            stats["plan"]["pal in another player's guild (left zero-keyed)"] += 1

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

    # 3) Point the guild membership handles at the host too. A handle is a
    #    contiguous {player_uid, instance_id} pair; on a server the player_uid of a
    #    pal handle is the zero GUID, and locally it is the host GUID. Leaving the
    #    handle on zero while the map key says host is exactly the inconsistency
    #    that makes the client delete the pal on load -- the file looks fine, and
    #    the pals simply never appear.
    #    One reference is deliberately left alone: the pal's own IndividualId,
    #    embedded inside its CharacterSaveParameterMap entry. A genuine local save
    #    keys the entry to the host but keeps that inner player_uid on zero, so
    #    every pair inside that section is skipped.
    csp_start, csp_end = sections["CharacterSaveParameterMap"]
    handles = 0
    for inst in convert_set:
        needle = zero_raw_guid + inst
        j = buf.find(needle)
        while j != -1:
            if not (csp_start <= j < csp_end):
                buf[j : j + 16] = new_raw_guid
                handles += 1
            j = buf.find(needle, j + 32)
    stats["handles"] = handles

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

    stats["localdata"] = None
    local_data_src = (
        resolve_local_data(local_data)
        if local_data
        else os.path.join(world, "LocalData.sav")
    )
    if os.path.exists(local_data_src):
        shutil.copy2(local_data_src, os.path.join(out, "LocalData.sav"))
        stats["localdata"] = local_data_src

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

    new_raw, _gvas, entries, _offsets, sections = read_level(
        os.path.join(out, "Level.sav")
    )
    csp_start, csp_end = sections["CharacterSaveParameterMap"]
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

    # The defect that silently deletes pals: an entry keyed to the host whose guild
    # or container reference still carries the zero GUID. References inside
    # CharacterSaveParameterMap are the pal's own IndividualId and correctly stay
    # zero, so they do not count.
    zero_raw = UUID.from_str(ZERO_GUID).raw_bytes
    stranded = 0
    for e in entries:
        if str(e["key"]["PlayerUId"]["value"]) != HOST_GUID or describe(e)[0]:
            continue
        inst = UUID.from_str(str(e["key"]["InstanceId"]["value"])).raw_bytes
        j = new_raw.find(zero_raw + inst)
        while j != -1:
            if not (csp_start <= j < csp_end):
                stranded += 1
                break
            j = new_raw.find(zero_raw + inst, j + 32)
    result["stranded_handles"] = stranded

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
