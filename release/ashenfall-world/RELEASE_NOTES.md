# v1.1.0 — Ashenfall: the continent of Vantyra, as a playable Minecraft world

v1.0.0 shipped the WorldPainter asset set for the continent (heightmap, masks, biome map,
surface table, setup script). This release ships the same continent **baked into Anvil region
files** — the full 8,000 x 8,000 x 384 extent as 250,000 chunks you can drop into `saves/`
and walk around in.

## Download

GitHub refuses files above 100 MB, so the save is split into ZIP shards held in this
repository at tag `v1.1.0`. Download every shard, extract them all into one folder, and you
have the world.

@@SHARDS@@

Direct links:

@@LINKS@@

The **Source code (zip)** link at the bottom of this release contains the same shards plus
the WorldPainter asset set and the generator itself. `SHA256SUMS.txt` sits next to the shards
if you want to verify the download, and
[`release/ashenfall-world/README.md`](https://github.com/Exo2v/MAP-generator./blob/v1.1.0/release/ashenfall-world/README.md)
has the same instructions with Windows and Unix commands.

## Install

1. Download all the shards into one folder.
2. Extract them all into `.minecraft/saves/Ashenfall` (Windows:
   `%appdata%\.minecraft\saves\Ashenfall`). Region files never straddle two shards, so the
   order does not matter — merge folders and overwrite.
3. Install **Lithosphere** and **Still Life** (1.21.x, Fabric or NeoForge) *before* the first
   load, then open the world in Minecraft Java 1.21.1 or newer.

## Decoration: the mods do it, vanilla does not

The chunks are written at generation status `minecraft:features`, never `minecraft:full`, and
`level.dat` ships with **`generate_features = 0`**. Terrain, biomes, water, rivers, beaches,
scree and frost are already in the file; every tree, plant and structure is left to the mods'
own decorators, which is what the populate mask is for — spec §5 method 1. Vanilla decoration
is deliberately off, so the world does not get a layer of vanilla trees and ores on top of
the mod's placement.

To flip that, or to fix an older save:

```bash
python3 -m mg.tools.set_world_decoration .minecraft/saves/Ashenfall --vanilla   # back to 1
python3 -m mg.tools.set_world_decoration .minecraft/saves/Ashenfall --mods      # to 0
```

## What is in the world

* Canvas 8,000 x 8,000 blocks (±4,000), Y −64…320, sea level Y = 62 — @@REGIONS@@ region
  files, @@CHUNKS@@ chunks.
* The nine specified landmarks at their exact centres and elevation windows — Forgotten Coast
  `(0, 68, 2500)`, Cogwork March `(-2100, 85, 0)`, Ashen Caldera `(0, 80, 0)` with its
  146-block rim and 92-block Obsidian Throne, Solitary Glacial Spine `(0, 220, -2500)`, Gilded
  Dunes `(2300, 75, 0)`, Whispering Fen `(2000, 63, 2000)`, Sunken Reach `(-2400, 54, 1600)`,
  Hermit's Spire `(-1800, 140, -1800)` and Byzantine Choir `(1800, 120, -1800)` — inside the
  Veil of Salt ring (radius 3,550).
* The project's own river logic: watershed routing over the eroded surface, channels carved
  into the valleys, lakes only where the ground genuinely closes, and no standing water on
  the shaped landforms (quarry benches, dune swales, caldera floor) while rivers still cross
  them.
* `maps/` (height, biome, climate, water, flow, population, soil) and `worldpainter/`
  (heightmap, populate/frost/scree/slope/water masks, surface table, setup script) travel in
  the first shard, so the same world can be edited in WorldPainter.
* `mapgen.json` records the exact configuration and seed, so the continent regenerates
  bit-for-bit anywhere.

## Generated and verified by

`generate_ashfall.py --out anvil --cell 4` — the full-resolution preset, seed 20250929:

```bash
python3 generate_ashfall.py --res 2048 --out anvil --cell 4 --compression 9 --dir out/ashenfall
python3 -m mg.tools.verify_ashenfall_world .minecraft/saves/Ashenfall
```

@@VERIFY@@

@@TESTS@@

## Fixed since v1.0.0

* **Let the mods decorate.** `level.dat` used to say `generate_features = 1` while the chunks
  were left undecorated, which let vanilla decorate exactly the chunks method 1 keeps bare.
  The flag now follows the export's decoration mode (`mods` → 0, `engine` → 1), and
  `mg.tools.set_world_decoration` flips an existing save either way.
* **Region flushing.** A region file was rewritten on every chunk that landed in it after the
  first 512-chunk batch — the writer counted chunks adopted from disk as pending. It now
  flushes on the un-flushed count: 4,096 chunks on the 1,024 x 1,024 slice used to measure
  went from 51 s to 34 s.
* **Resume.** Exporting into a directory that already held a partial world used to truncate
  each resumed region file down to only the chunks regenerated in that run.

## Known limits

* No release asset: `uploads.github.com` is unreachable from the machine that builds this, so
  the shards live in the repository tree rather than in the release's asset list.
* Whispering Fen heartwood exists in the surface table and the palette; the giant fungal
  trees themselves are still palette-only this pass.
* Structures are terrain only. Cirque tarns on the Solitary Glacial Spine and near the
  Hermit's Spire are deliberately left as pondable ground.
