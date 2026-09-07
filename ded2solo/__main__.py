import argparse
import os
import sys

from . import bootstrap


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ded2solo",
        description="Convert a Palworld dedicated-server world into a local "
        "single-player / co-op world.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  # see which characters the world contains
  python -m ded2solo --world ./SaveGames/0/<WORLDID> --list

  # convert one of them
  python -m ded2solo --world ./SaveGames/0/<WORLDID> \\
                     --player BA558FC5000000000000000000000000 \\
                     --out ./converted/<WORLDID>

Then copy the output folder into %LOCALAPPDATA%\\Pal\\Saved\\SaveGames\\<SteamID>\\
alongside your other worlds. Back up your saves first.""",
    )
    p.add_argument(
        "--world",
        required=True,
        help="the server's world folder (contains Level.sav, LevelMeta.sav, Players/)",
    )
    p.add_argument(
        "--player",
        help="the player to become the local host: a 32-character id, the "
        "Players/<id>.sav filename, or a dashed GUID",
    )
    p.add_argument("--out", help="output folder (must not be inside --world)")
    p.add_argument(
        "--list",
        action="store_true",
        help="list the characters in the world and exit",
    )
    p.add_argument(
        "--list-guilds",
        action="store_true",
        help="list guilds, members, and base ids in the world and exit",
    )
    p.add_argument(
        "--absorb-other-players",
        action="store_true",
        help="also re-key other players' pals to the host. Off by default: those "
        "pals live in containers only their owner can reach, so this mostly "
        "matters if you would rather they were not dropped on load.",
    )
    p.add_argument(
        "--drop-other-players",
        action="store_true",
        help="do not copy the other players' .sav files into the output",
    )
    p.add_argument(
        "--world-name",
        help="rename the world as it appears in the game's world list",
    )
    p.add_argument(
        "--local-data",
        help="copy map/exploration progress from LocalData.sav, or from a local "
        "world folder containing LocalData.sav",
    )
    p.add_argument(
        "--settings",
        help="also transfer the server's world settings: path to its "
        "PalWorldSettings.ini. Without this the world runs on default rules.",
    )
    p.add_argument(
        "--settings-template",
        help="a WorldOption.sav to base the settings file on (default: the most "
        "recent one found under %%LOCALAPPDATA%%\\Pal\\Saved\\SaveGames)",
    )
    p.add_argument(
        "--include-server-fields",
        action="store_true",
        help="with --settings, also copy server-only fields (admin password, "
        "ports, RCON, IP). Skipped by default.",
    )
    p.add_argument(
        "--no-verify", action="store_true", help="skip the post-conversion check"
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    bootstrap.load()

    from . import convert as conv

    if not os.path.isdir(args.world):
        raise SystemExit(f"--world folder does not exist: {args.world}")
    if not os.path.exists(os.path.join(args.world, "Level.sav")):
        raise SystemExit(f"no Level.sav in {args.world} -- is that the world folder?")

    if args.list:
        print(f"Characters in {args.world}:\n")
        print(f"  {'player id':34s} {'name':20s} {'level':>5s} {'pals':>6s}")
        for c in conv.list_characters(args.world):
            print(
                f"  {conv.guid_to_filename(c['guid']):34s} "
                f"{str(c['name']):20s} {str(c['level']):>5s} {c['pals']:>6d}"
            )
        print("\nRe-run with --player <id> --out <folder> to convert one of them.")
        return 0

    player_guid = None
    if args.player:
        try:
            player_guid = conv.filename_to_guid(args.player)
        except ValueError:
            player_guid = args.player.lower()

    if args.list_guilds:
        print(f"Guilds in {args.world}:\n")
        for g in conv.list_guilds(args.world, player_guid):
            marker = " *" if g["contains_selected_player"] else ""
            print(
                f"  [{g['index']}] {g['name']}{marker}: "
                f"{len(g['members'])} player(s), {len(g['base_ids'])} base(s)"
            )
            if g["id"]:
                print(f"      guild id: {g['id']}")
            for member in g["members"]:
                print(
                    f"      {conv.guid_to_filename(member['guid']):34s} "
                    f"{str(member['name']):20s} level={member['level']}"
                )
            if g["base_ids"]:
                print("      base ids:")
                for base_id in g["base_ids"]:
                    print(f"        {base_id}")
            if g["base_point_ids"]:
                print("      palbox map object ids:")
                for point_id in g["base_point_ids"]:
                    print(f"        {point_id}")
        if player_guid:
            print("\n  * contains the selected player")
        return 0

    if not args.player or not args.out:
        raise SystemExit("--player and --out are required (or use --list)")

    world = os.path.abspath(args.world)
    out = os.path.abspath(args.out)
    if out == world or out.startswith(world + os.sep):
        raise SystemExit("--out must not be inside --world")
    if os.path.isdir(out) and os.listdir(out):
        raise SystemExit(f"--out already exists and is not empty: {out}")

    stats = conv.convert(
        world,
        player_guid,
        out,
        absorb_other_players=args.absorb_other_players,
        keep_other_player_files=not args.drop_other_players,
        local_data=args.local_data,
    )

    print(f"\nConverted {stats['player_name']!r} ({player_guid}) to the local host.\n")
    for label, n in stats["plan"].most_common():
        print(f"  {n:>6}  {label}")
    print(f"\n  {stats['guid_swaps']:>6}  GUID swaps in Level.sav")
    print(f"  {stats['rekeyed']:>6}  character entries re-keyed to {conv.HOST_GUID}")
    print(f"  {stats['handles']:>6}  guild membership handles repointed to the host")
    print(f"\n  Level.sav written as zlib, save_type=0x{stats['level_save_type']:02x}")
    for src, dst, swaps in stats["players"]:
        note = f"{swaps} GUID swaps" if src != dst else "unchanged"
        print(f"  Players/{src} -> {dst}  ({note})")
    if stats["levelmeta"]:
        print("  LevelMeta.sav copied (required, or the world will not be listed)")
    else:
        print("  WARNING: no LevelMeta.sav in the source -- the world will not be listed")
    if stats["localdata"]:
        print(f"  LocalData.sav copied from {stats['localdata']} (map/exploration progress)")

    if args.world_name:
        old, new = conv.set_world_name(out, args.world_name)
        print(f"\nWorld name: {old!r} -> {new!r}")

    if args.settings:
        from . import settings as settings_mod

        r = settings_mod.transfer(
            args.settings,
            out,
            template=args.settings_template,
            include_server_fields=args.include_server_fields,
        )
        print(f"\nSettings from {os.path.basename(args.settings)} "
              f"(template: {os.path.basename(os.path.dirname(r['template']))}):")
        print(f"  applied {len(r['applied'])}, {len(r['changed'])} differ from the template")
        for key, before, after in r["changed"]:
            print(f"    {key:42s} {str(before):26s} -> {after}")
        if r["skipped_server"]:
            print(f"  skipped {len(r['skipped_server'])} server-only fields "
                  "(use --include-server-fields to copy them)")
        if r["mismatches"]:
            print("  FAILED: values did not survive the write")
            return 1

    if not args.no_verify:
        v = conv.verify(world, out, player_guid)
        print("\nVerification:")
        print(f"  payload length preserved   : {v['length_preserved']}")
        print(f"  bytes changed vs original  : {v['bytes_changed']}")
        print(f"  residual old GUID (level)  : {v['residual_old_guid']}")
        print(f"  residual old GUID (player) : {v['player_residual_old_guid']}")
        print(f"  character entries          : {v['entries']}")
        print(f"  entries keyed to the host  : {v['host_keyed']}")
        print(f"  stranded guild handles     : {v['stranded_handles']}  (must be 0)")
        print(f"  host character             : {v['host_name']!r}")
        print(f"  player save PlayerUId      : {v['player_uid']}")
        print(f"  player save links to world : {v['player_linked']}")
        ok = (
            v["length_preserved"]
            and v["residual_old_guid"] == 0
            and v["player_residual_old_guid"] == 0
            and v["host_entries"] == 1
            and v["player_uid"] == conv.HOST_GUID
            and v["player_linked"]
            and v["stranded_handles"] == 0
        )
        print(f"\n  {'PASS' if ok else 'FAILED'}")
        if not ok:
            return 1

    print(f"\nOutput: {out}")
    print("Copy that folder into %LOCALAPPDATA%\\Pal\\Saved\\SaveGames\\<SteamID>\\")
    return 0


if __name__ == "__main__":
    sys.exit(main())
