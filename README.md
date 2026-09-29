# MAP-generator.

A Minecraft terrain generator you can run as a desktop app: **build** a world, **preview it
in 3D**, and **export it to a folder you choose** as a real Java Edition save (Anvil `.mca`
region files + `level.dat`) that the game loads straight from `saves/`.

The generator is an original engine (Python + numpy, no mod code) that borrows the *ideas*
of four famous worldbuilding tools:

| Inspiration | What was taken from it |
| --- | --- |
| **Lithosphere** | Climate-driven worldgen: continents/oceans/islands with gradual coastlines and wide beaches, large temperature & humidity regions so biomes are big and smooth instead of speckled, rivers that carve deep valleys, swamps confined to low ground, overhauled caves. |
| **Still Life** | Population data: natural biome variants plus transitional biomes, high tree/shrub/flower density, cave biomes, and structure placement driven by where people would actually settle. |
| **Streams Reflowing** | Hydrology: priority-flood basins, per-cell flow direction, flow accumulation, watersheds, lakes that actually hold water, rivers that widen downstream and end at the sea instead of drying up in a cave. |
| **World Machine / World Painter** | Node-graph-style terrain construction: continents → mountains → plateaus → terracing → thermal/fluvial erosion → deposition, plus a WorldPainter-style export bundle (16-bit heightmap, water, biome and flow maps) for hand-editing in WorldPainter. |

Everything runs offline. There is no Java, no Minecraft install and no internet access
involved in generating a world.

---

## Quick start

### 1. Run it live in a browser (no install)

```bash
cd "/home/user/MAP-generator."
python3 -m mg.desktop --serve-only --port 8791
```

Then open `http://localhost:8791/`. Pick a preset, press **Generate world**, orbit the
result, then set an output folder and press **Export world**.

### 2. Run it as a windowed desktop app

```bash
python3 -m mg.desktop          # native window via pywebview, browser fallback
python3 -m mg.desktop --browser   # force the default browser
```

### 3. Build the single-file executable

```bash
python3 -m pip install pyinstaller pywebview   # pywebview optional
python3 tools/build_exe.py                     # -> dist/mapgen.exe (Windows) / dist/mapgen
python3 tools/build_exe.py --onedir            # faster start, folder build
python3 tools/build_exe.py --check             # pre-flight only, build nothing
```

PyInstaller cannot cross-compile: run this on the OS you want the binary for. On a stock
Debian/Ubuntu system Python you also need the shared-library package
(`libpython3.11` / `libpython3.12`), otherwise the pre-flight check stops you with a clear
message instead of a crash. Windows one-click: `tools\build_exe.bat`.

### 4. Drive it from the command line

```bash
python3 -m mg.cli presets                                  # list the 7 presets
python3 -m mg.cli generate --preset cinematic --size 2048 --seed 42 --out ~/.minecraft/saves
python3 -m mg.cli map      --preset desert --size 1024 --kind biome --resolution 2048 --out desert.png
python3 -m mg.cli inspect  --preset rainforest --size 512 --x 128 --z 128
python3 -m mg.cli serve    --port 8791
```

`--set key.path=value` overrides any config field, e.g.
`--set water.river_threshold=500 --set erosion.fluvial_iterations=20`.
`--save-config cfg.json` writes the exact config used, which you can feed back with
`--config cfg.json`. Ready-made configs live in `presets/`.

### 5. Use it from Python

```python
from mg.config import default_config, RegionInfo
from mg.pipeline import run_pipeline
from mg.export.world import export_world

cfg = default_config()
cfg.region = RegionInfo(blocks_x=2048, blocks_z=2048, cell_size=4)
cfg.seed = 42
result = run_pipeline(cfg, progress=lambda f, m: print(f"{f:5.1%} {m}"))
out = export_world(result.terrain, cfg.to_dict(), "~/.minecraft/saves", seed=cfg.seed)
print(out.chunks, "chunks ->", out.world_dir)
```

---

## What ends up in the save folder

```
<output>/<World Name>/
├── level.dat              # seed, spawn point, generator metadata, time-of-day
├── level.dat_old
├── session.lock
├── region/r.0.0.mca       # standard Anvil region files, zlib/gzip chunks
├── maps/                  # PNG map bundle + legend.json (height, biome, climate, …)
├── worldpainter/          # heightmap.png (16-bit), water.png, biomes.png, flow.png
├── schematic/             # optional WorldEdit .schem tiles (Sponge Schematic v2)
├── mapgen.json            # the exact config that produced this world
└── README.txt             # what this world is, in-world spawn coords, how to reload
```

Copy the folder into `.minecraft/saves/`, launch Minecraft Java 1.21, and it is playable.
Water, beaches, rivers, lakes, caves, ores, trees, roads and settlement structures are all
already in the chunk data — no mods or datapacks required.

See **[docs/USAGE.md](docs/USAGE.md)** for every control in the UI and
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for how the pipeline works and where to
extend it.

## Tests

```bash
python3 -m unittest discover -s tests     # 61 tests: core maths, pipeline, exporters
```

All three test modules run offline and take a few seconds; they cover noise/hydrology/erosion
maths, end-to-end pipeline invariants (shape agreement, river widening downstream, seed
reproducibility, every preset), and byte-level checks of the NBT/Anvil/schematic writers
including a reference bit-unpacker that re-decodes exported chunks.
