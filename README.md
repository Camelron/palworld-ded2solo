# ded2solo

Convert a Palworld **dedicated-server world** into a **local single-player / co-op world**, on game versions after the 2026 Summer Update.

Works offline, on the save version whose `.sav` files begin with `PlM1`. No browser, no upload.

```
python setup_env.py
python -m ded2solo --world PalServer/Pal/Saved/SaveGames/0/<WORLDID> --list
python -m ded2solo --world PalServer/Pal/Saved/SaveGames/0/<WORLDID> \
                   --player BA558FC5000000000000000000000000 \
                   --out ./converted/<WORLDID>
```

Then copy the output folder into `%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID>\`, alongside your existing worlds. **Back up your saves first.**

For a step-by-step dedicated-server to local save checklist, including
map/exploration progress and guild/base cleanup notes, see
[SERVER_TO_LOCAL.md](SERVER_TO_LOCAL.md).

To carry the server's rules and give the world a proper name at the same time:

```bash
python -m ded2solo --world <world folder> --player <id> --out <out folder> \
                   --settings PalServer/Pal/Saved/Config/WindowsServer/PalWorldSettings.ini \
                   --world-name "Arkologia 1.0"
```

To carry map/exploration progress from an existing local cache for the same
server world, pass its `LocalData.sav`:

```bash
python -m ded2solo --world <server world folder> --player <id> --out <out folder> \
                   --local-data "%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID>\<WORLDID>\LocalData.sav"
```

---

## Why the existing tools break

Two separate things go wrong, and most guides only address the first.

### 1. The saves are Oodle-compressed

Since the 2026 Summer Update, `.sav` files use magic bytes `PlM1` (Oodle Kraken) instead of `PlZ` (zlib). `palworld-save-tools` up to 0.24.0 fails with `not a compressed Palworld save, found b'PlM' instead of b'PlZ'`.

Palworld statically links Oodle, so there is no `oo2core*.dll` in the game install to borrow. `setup_env.py` downloads [`libooz.dll`](https://github.com/zao/ooz) — an open-source Kraken decompressor — into `vendor/`.

Only the *decompressor* is needed: **the game still loads zlib-compressed saves**, so output is written back as plain `PlZ`. Palworld re-writes it in its own format on the first save.

### 2. Pals are referenced differently, and this is the part that eats your pals

`Level.sav`'s `CharacterSaveParameterMap` is keyed by `{PlayerUId, InstanceId}`. A local save keys a pal to the **host** GUID `00000000-0000-0000-0000-000000000001`; a dedicated server keys it to the **zero** GUID and records the real owner in `SaveParameter.OwnerPlayerUId`. So the key has to be rewritten.

**That alone is not enough, and getting it half right is worse than not trying.** Each pal is also referenced as a contiguous `{player_uid, instance_id}` pair in several other places, and a local save does *not* use the same GUID in all of them:

| section | local save | dedicated server |
|---|---|---|
| `CharacterContainerSaveData` | host | zero |
| `GroupSaveDataMap` (guild handles) | host | zero |
| `WorkSaveData` | host | zero |
| `CharacterSaveParameterMap` (the pal's own `IndividualId`) | **zero** | zero |

Re-key the map entry but leave the guild and container references on zero, and the client decides those pals are unreferenced and **deletes every one of them on the first autosave**. Nothing warns you: the world loads, your character is intact, the pals are simply gone, and the autosave has already overwritten the file.

Note the last row. The pal's *own* embedded `IndividualId` stays on the zero GUID even in a genuine local save, so a blanket "replace zero with host" pass is also wrong.

`ded2solo` rewrites exactly the first three and leaves the fourth alone.

### Which pals get converted

The set is the chosen player's **guild membership**, not the pals they personally own. A guild also contains unowned base-camp workers, and other players' guilds contain unowned workers of their own that must be left untouched. Converting by ownership sweeps up the wrong pals.

## How it edits the file

Entirely by **same-length, in-place byte patching of the decompressed payload**. Every edit replaces one 16-byte GUID with another, so nothing in the file can shift.

This is deliberate. A parse → re-encode round trip is **not** byte-lossless on this save version — it silently drops ~369 bytes across `MapObjectSaveData` and `BaseCampSaveData` and re-serialises `GroupSaveDataMap` differently. Anything that rewrites `Level.sav` through the Python encoder will quietly damage your base camps.

Parsing is used only to *locate* offsets and to *verify* the result. It also means structures the decoders still cannot parse — `CharacterContainerSaveData`, the guild blobs — get patched correctly anyway, because the GUIDs inside them are just bytes.

The map-key offset is asserted against the parsed value for every single entry before a byte is written, so a future format change fails loudly instead of corrupting the save.

## Verification

Every run ends with a check (skip it with `--no-verify`):

```
  payload length preserved   : True
  bytes changed vs original  : 31537
  residual old GUID (level)  : 0
  residual old GUID (player) : 0
  character entries          : 1584
  entries keyed to the host  : 857
  stranded guild handles     : 0  (must be 0)
  host character             : 'Brain'
  player save PlayerUId      : 00000000-0000-0000-0000-000000000001
  player save links to world : True

  PASS
```

`stranded guild handles` is the one that matters most: it counts pals keyed to the host that still have a zero reference outside `CharacterSaveParameterMap`. Any number above zero means those pals will be deleted on load.

## If it goes wrong, stop the game immediately

The client autosaves every few minutes and keeps timestamped copies in `<world>\backup\world\`. If your pals are missing, **quit without letting it save again** — otherwise the backups fill up with the already-broken state. Re-convert from the original server folder rather than from anything the client has written.

## World settings live somewhere else entirely

A dedicated server keeps its rules in `Saved/Config/WindowsServer/PalWorldSettings.ini`. A local world keeps the same rules in `WorldOption.sav` inside the world folder. Convert the save and nothing else, and your world silently reverts to **default** rules — rates, death penalty, difficulty, base-camp limits, the lot.

`--settings <ini>` transfers them. Every key in the ini's `OptionSettings=(...)` blob has a counterpart field in `WorldOption.sav`, so it is a direct field-by-field copy. The save holds far more fields than the ini does (voice chat, PvP drops, respawn penalties…); those come from a template — any existing local `WorldOption.sav`, auto-discovered, or given with `--settings-template`.

Server infrastructure fields are **skipped by default**: `AdminPassword`, `ServerPassword`, `PublicIP`, `PublicPort`, `RCON*`, `RESTAPI*`, `Region`, `bUseAuth`, `BanListURL`, `ServerName`, `ServerDescription`, `ServerPlayerMaxNum`. They do nothing in a local world, and the admin password in particular should not ride along inside a save file you might share. `--include-server-fields` copies them anyway.

Unlike `Level.sav`, both `WorldOption.sav` and `LevelMeta.sav` round-trip through the parser byte-for-byte, so these two are edited structurally and verified by reading back every value that was written.

## Renaming the world

A converted server world shows up in the world list under whatever the server called its autosave — usually something like `Autosave_W`. `--world-name "My World"` rewrites `WorldName` in `LevelMeta.sav`.

## Don't forget LevelMeta.sav

`LevelMeta.sav` holds `WorldName` and `InGameDay`. Converters routinely do not emit it, and **without it the world does not appear in the game's world list at all** — a confusing failure, because the save itself is perfectly fine. `ded2solo` copies it across automatically.

## Output layout

```
<WORLDID>/
  Level.sav        converted
  LevelMeta.sav    copied from the server, unchanged
  Players/
    00000000000000000000000000000001.sav       your character
    00000000000000000000000000000001_dps.sav   its Dimensional Pal Storage, if present
    <other server players>.sav                 copied unchanged, inert in solo play
```

`LocalData.sav` is per-installation. Use `--local-data` to copy it when you
have a local cache for the same server world and want to keep map/exploration
progress.

## Options

| flag | what it does |
|---|---|
| `--list` | list the characters in the world (id, name, level, pal count) and exit |
| `--player` | the character to become the host — a 32-char id, a `Players/<id>.sav` filename, or a dashed GUID |
| `--out` | output folder; must be empty and outside `--world` |
| `--list-guilds` | list guilds, members, base IDs, and Palbox map-object IDs |
| `--absorb-other-players` | also re-key other players' pals to the host. Off by default |
| `--drop-other-players` | do not copy the other players' `.sav` files |
| `--world-name` | rename the world as shown in the world list |
| `--local-data` | copy map/exploration progress from `LocalData.sav` or a world folder containing it |
| `--settings` | transfer world rules from the server's `PalWorldSettings.ini` |
| `--settings-template` | base the settings file on a specific `WorldOption.sav` |
| `--include-server-fields` | with `--settings`, also copy passwords/ports/RCON/IP |
| `--no-verify` | skip the post-conversion check |

The settings transfer also runs standalone against a world folder you already installed:

```bash
python -m ded2solo.settings --ini <PalWorldSettings.ini> --world-folder <world folder>
```

### About the other players

By default their character entries and pals are left exactly as the server had them: still keyed to the zero GUID. Those pals sit in containers only their owner could reach, so in solo play they are unreachable either way. If the client drops zero-keyed entries on load, that affects only those players' pals — never the host's. Use `--absorb-other-players` if you would rather keep them all.

## Requirements

Python ≥ 3.10, and network access for the one-time `setup_env.py` (pip + the libooz release). `setup_env.py` writes only into `./vendor` — nothing lands in your global site-packages.

## Credits

- [cheahjs/palworld-save-tools](https://github.com/cheahjs/palworld-save-tools) — the GVAS reader/writer everything here is built on (MIT)
- [quadrantbs/palworld-hostfix-toolkit](https://github.com/quadrantbs/palworld-hostfix-toolkit) — the Oodle-aware `palsav.py` and the hardened `rawdata` decoders in `patches/` (MIT)
- [zao/ooz](https://github.com/zao/ooz) — `libooz.dll`, downloaded at setup, not redistributed here

See [NOTICE.md](NOTICE.md). Not affiliated with or endorsed by Pocketpair.

## Disclaimer

This edits save files by patching their binary structure. Back up your world folder before running it. Use at your own risk.
