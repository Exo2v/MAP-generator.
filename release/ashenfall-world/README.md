# Ashenfall — the continent of Vantyra (Minecraft Java world)

This folder ships the generated continent as a **ready-to-play Minecraft Java world**.
GitHub refuses single files above 100 MB, and the full 8,000 x 8,000 continent is far
bigger, so the save is delivered as a set of shards that you extract on top of each
other. The result is bit-for-bit the exported world.

## What is in the download

| file | contents |
| --- | --- |
| `ASHENFALL_MinecraftWorld_*_part01_of_04.zip` | `level.dat`, `mapgen.json`, `README.txt`, `maps/`, `worldpainter/` **and** the north-west quarter of the region files |
| `ASHENFALL_MinecraftWorld_*_part02_of_04.zip` … `_part04_of_04.zip` | region files only (`region/r.x.z.mca`) |
| `SHA256SUMS.txt` | checksums, if you want to verify the download |

Every shard is an ordinary ZIP. Region files never straddle two shards, so the order in
which you extract them does not matter.

## Install

1. Download **all four** shards (and keep them in one folder).
2. Extract every shard into the same target folder:

   ```
   .minecraft/saves/Ashenfall/          (Windows: %appdata%\.minecraft\saves\Ashenfall)
   ```

   On Linux/macOS:

   ```bash
   mkdir -p ~/.minecraft/saves/Ashenfall
   for z in ASHENFALL_MinecraftWorld_*_part*.zip; do unzip -o "$z" -d ~/.minecraft/saves/Ashenfall; done
   ```

   On Windows, select all four ZIPs, *Extract all* into `%appdata%\.minecraft\saves\Ashenfall`
   (allow it to merge folders and overwrite).

3. Start Minecraft **Java Edition 1.21.1 or newer** and open the world *Ashenfall*.

## Mods

The chunks are exported at generation status `minecraft:features`, which is the
specification's population *method 1*: the terrain, biomes and water are already there,
and the world is left undecorated so the mods can plant the trees, plants and structures
themselves on first load.

| mod | why |
| --- | --- |
| **Lithosphere** | the generation overhaul the continent's climate and landform logic is modelled on (1.21.x, Fabric/NeoForge) |
| **Still Life** | the population data — biome variants, transitional biomes and the higher tree/shrub/flower density the mask expects |

Install them **before** first loading the world, then walk away from spawn for a minute
so the chunks you have already visited get decorated. Do not combine with Terralith or
Biomes O' Plenty (Still Life refuses to run with them).

If you would rather play completely vanilla, that works too — the exported `level.dat`
has `generate_features` enabled, so the game's own decorators fill in vanilla-style
features when the chunks first load. To keep the world completely bare instead, set
`Data.WorldGenSettings.generate_features` to `0` in `level.dat` before the first load.

## What was generated

* Canvas 8,000 x 8,000 blocks (±4,000 from origin), Y −64…320, sea level Y = 62.
* Nine spec landmarks at their exact centres and elevation windows: Ashen Coast,
  Cogwork Quarry, Ashen Caldera, Solitary Glacial Spine, Gilded Dunes, Whispering Fen,
  Sunken Reach, Cinder Spire, Choir of Ash, plus the Veil of Salt ring at radius 3,550.
* Rivers and lakes from the project's own hydraulic river logic, beaches, scree, frost
  and populate masks in `maps/` and `worldpainter/` for editing the world in WorldPainter.

`mapgen.json` inside the save is the exact configuration (including the seed), so
`python generate_ashfall.py --config …` regenerates the same continent anywhere.
