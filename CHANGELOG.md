# Changelog

## v1.0.0 — the Ashenfall build

The first release. A complete Minecraft terrain tool: build a world, preview it in 3D, and
export it as a real Java Edition save — plus the one specified map it was built to deliver,
the continent of Vantyra.

### The map: Ashenfall / Vantyra

`ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf` is not a set of dials, so it lands as a
purpose-built engine beside the generic World-Machine chain rather than as another preset.

* **8,000 × 8,000 blocks**, centred on (0, 0), Y −64…320, sea level Y = 62.
* **Nine cardinal landmarks** at their exact coordinates and elevations: the Forgotten
  Coast (spawn, `(0, 68, 2500)`), the Cogwork March, the Ashen Caldera with its 146-block
  rim and the Obsidian Throne, the Solitary Glacial Spine, the Gilded Dunes with vitrified
  black-glass crests, the Whispering Fen, the Sunken Reach, the Hermit's Spire and the
  Byzantine Choir. All eight ground-level centres land within 0.25 blocks of the
  specification; the caldera's centre is its throne, at the spec's Y = 92.
* **The Hermite continental shelf** — `t = clamp((r − 3300)/500)`,
  `H_drop = −600·(3t² − 2t³)` — falling away into the Veil of Salt, whose floor is the
  spec's abyss at Y = −32.
* **Five climate tiers** with the specification's 0.55-unit buffering against the
  snow-pocket glitch, and the uncapped `T_eff = T_base − 0.0055·max(0, y − 62)` lapse rate.
* **The §6 surface rule**: slope-banded grass/coarse dirt/scree/bedrock, the 225 treeline,
  the caldera's blackstone-basalt-magma-obsidian override and the Veil of Salt crust.
* **Exported undecorated** (spec §5 method 1): chunks carry `Status: minecraft:features`,
  never `minecraft:full`, so Lithosphere and Still Life decorate them on first load, driven
  by `ASHFALL_POPULATE_MASK.png`.
* **The turnkey command** from the specification works as written:
  `python generate_ashfall.py --res 2048 --out worldpainter`.

### The engine

* **Climate** — Lithosphere-style large smooth temperature/humidity regions, gradual
  coastlines and wide beaches, latitude bands over the noise, altitude lapse, continentality
  and orographic rain shadow.
* **Terrain** — World Machine / World Painter construction: continents → folded mountains →
  plateaus → cliffs → islands → volcanoes → terracing, with thermal and stream-power
  erosion, deposition and diffusion.
* **Water** — priority-flood basins, D-infinity routing with a D8 fallback for valley
  floors, flow accumulation and discharge, plus a purpose-built river model: graded
  longitudinal profiles, knickpoint migration, meander growth with cutoffs and oxbow lakes,
  and deltas at the sea.
* **Biomes and population** — Still Life-style biome variants and transitional biomes, with
  fertility and population fields driving tree, shrub and flower density.
* **Export** — Anvil `.mca` region files + `level.dat` written from scratch (no Java, no
  Minecraft install), a WorldPainter bundle (16-bit heightmap, water/slope/scree/frost
  masks, a JSR-223 setup script) and schematic tiles.

### Also in this release

* **`tools/build_ashenfall.sh`** — supervises the turnkey generator, restarting it if it
  dies.
* **`tools/restore_sandbox.sh`** — reinstalls the dependencies and resets the checkout to
  the pushed branch.
* **`mg/tools/verify_ashenfall_world.py`** — reads an exported world back out of the region
  files and checks the nine landmark centres against the specification's elevation table.
* **Resumable exports** — `export.resume` skips chunks already on disk, so an interrupted
  quarter-million-chunk export continues where it stopped instead of starting over.

### Fixed during development

* **The depression filler drowned the shaped landforms.** A quarry bench, a dune swale and
  a caldera rim all enclose closed sub-basins, and `fill_lakes` flooded every one of them —
  39 % of the caldera, 25 % of the Cogwork March and 15 % of the desert Gilded Dunes were
  standing water. The water system now takes a `no_lake` mask for those regions: no standing
  water, but rivers still cross them. A stronger `no_water` mask was the wrong tool, because
  it takes the rivers with it; that remains reserved for the lava basin.
* **Resuming an export destroyed chunks.** A writer for a region file that already had
  chunks on disk started empty and rewrote the file down to only the chunks it regenerated,
  discarding the ones the resume had skipped, so the export could never converge.

### Known limitations

* The **Windows executable must be built on Windows** (`tools/build_exe.bat`): PyInstaller
  needs a shared `libpython`, which the build environment used here does not provide. The
  Python path runs anywhere.
* The Whispering Fen's crimson/warped heartwood is not placed by this release. Decoration is
  deliberately left to the mods (spec §5), so it belongs with the Still Life biome-tag work.
* A full-resolution export (cell size 4, 250,000 chunks) takes tens of minutes; a cell size
  of 8 halves the generation time at some cost in river and caldera detail.
