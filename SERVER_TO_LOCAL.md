# Server to Local Conversion Flow

This checklist is for turning a Palworld dedicated-server world into a local
single-player/co-op world after the 2026 Summer Update.

## 1. Back up both sides

Back up the server world folder before converting it:

```text
PalServer/Pal/Saved/SaveGames/0/<WORLDID>/
```

If you already have a local save slot that you care about, back up that too:

```text
%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID>\<WORLDID>\
```

Do not run the converter while the server or the Palworld client is writing the
same save.

## 2. Find the player ID

List the players in the dedicated-server world:

```bash
python setup_env.py
python -m ded2solo --world PalServer/Pal/Saved/SaveGames/0/<WORLDID> --list
```

Then inspect guild membership and base ownership:

```bash
python -m ded2solo --world PalServer/Pal/Saved/SaveGames/0/<WORLDID> \
                   --player <PLAYER_ID> \
                   --list-guilds
```

The guild marked with `*` contains the selected player. Its base IDs are the
base camps that belong to the player's guild.

## 3. Convert the server world

The normal conversion keeps the full server world state, converts the selected
player to the local host identity, and rekeys the selected player's guild Pals
so they survive the first local autosave:

```bash
python -m ded2solo --world PalServer/Pal/Saved/SaveGames/0/<WORLDID> \
                   --player <PLAYER_ID> \
                   --out ./converted/<WORLDID> \
                   --drop-other-players \
                   --world-name "My Local World"
```

Copy `./converted/<WORLDID>` into:

```text
%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID>\
```

## 4. Transfer map/exploration progress

Map fog and exploration state live in `LocalData.sav`. Dedicated servers do not
usually have this file in the server save folder; it is created by the local
client under the matching local world ID after you have joined that server.

If this file exists:

```text
%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID>\<WORLDID>\LocalData.sav
```

include it during conversion:

```bash
python -m ded2solo --world PalServer/Pal/Saved/SaveGames/0/<WORLDID> \
                   --player <PLAYER_ID> \
                   --out ./converted/<WORLDID> \
                   --local-data "%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID>\<WORLDID>\LocalData.sav"
```

`--local-data` also accepts a world folder containing `LocalData.sav`.

## 5. Transfer server rules

Server rules live in `PalWorldSettings.ini`, not `Level.sav`:

```bash
python -m ded2solo --world PalServer/Pal/Saved/SaveGames/0/<WORLDID> \
                   --player <PLAYER_ID> \
                   --out ./converted/<WORLDID> \
                   --settings PalServer/Pal/Saved/Config/WindowsServer/PalWorldSettings.ini
```

Server-only fields such as passwords, ports, RCON, and public IP are skipped by
default.

## 6. What this can and cannot prune

`ded2solo` intentionally patches `Level.sav` by replacing same-length GUID bytes
instead of fully re-writing the save. That is important: current Python
`palworld-save-tools` round trips can still change `MapObjectSaveData`,
`BaseCampSaveData`, and `GroupSaveDataMap`, which are exactly where base
structures live.

Because of that, this change does not add a supported "delete every other guild
and its bases from `Level.sav`" command yet.

Available now:

- `--drop-other-players`: do not copy other player `.sav` files.
- `--list-guilds`: show which guild contains the selected player and which base
  IDs belong to each guild.

Not available yet:

- deleting foreign guild records from `Level.sav`
- deleting foreign base camp records from `Level.sav`
- deleting all map objects, containers, work records, and references that belong
  only to those foreign bases

An eventual pruning implementation should operate on raw entry spans or another
byte-lossless writer. It should keep the selected player's guild, its base camp
records, Palbox map-object IDs, related map objects, work records, guild extra
data, and required containers, while dropping foreign guild/base references.

Before such a helper writes anything, verify it on a copy and check:

- one host player remains
- the selected guild remains
- the expected base count remains
- removed guild IDs, base IDs, and Palbox point IDs have zero references
- the game loads the result before any original backups are discarded

## 7. First launch

Start Palworld and open the converted world. If your character, Pals, bases, or
map state are wrong, quit before autosave and restore from the backup.
