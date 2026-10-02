# HANDOFF — Ashenfall / Vantyra world build

**Audience:** whoever (or whatever) picks this up next. Everything below is either copied
from the code or measured on this machine; nothing is remembered from a chat.

**One-line state:** the generator is finished and released (`v1.0.0`, WorldPainter assets),
the full-resolution Minecraft world export is *the remaining job* — it is running, it
survives resets, and it publishes itself to GitHub as it goes.

---

## 0. TL;DR — what to do if you have 5 minutes

```bash
cd /home/user/MAP-generator.                 # note the trailing dot in the folder name
bash tools/restore_sandbox.sh                # deps + git + rebuild out/ from published shards

# then, if the build is not already running, in two terminals (or two background processes):
python3 generate_ashfall.py --res 2048 --out anvil --cell 4 --compression 9 \
        --dir out/ashenfall --progress-log 45            # the world (≈2 h)
python3 tools/pack_world_download.py out/ashenfall/Ashenfall \
        --out release/ashenfall-world --version v1.1.0 \
        --watch --push --interval 90 --min-raw-mb 40 --max-mb 90 --expect-regions 250
```

When `out/world_export.log` says `BUILD COMPLETE`, run the verifier, fill the release notes,
tag `v1.1.0`, push, and open the release. The exact commands are in §7 and §10.

---

## 1. What the deliverable is

`MAP-generator.` is an integrated Minecraft terrain generator (Python core + Three.js
preview, packaged to an .exe). This pass of the project is about **one specified map**, not
about the generic presets:

> Build the 8,000 × 8,000-block continent of **Vantyra** ("Ashenfall") exactly as
> `ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf` describes it, and ship it as a
> mod-ready Minecraft Java world.

Acceptance criteria that are already met, and the ones that are not:

| # | Criterion | Status |
| --- | --- | --- |
| 1 | 8,000 × 8,000 blocks centred on origin, Y −64…320, sea level Y = 62 | ✅ |
| 2 | Nine landmarks at their exact spec coordinates and elevation bands | ✅ (verified on a full run, 8/9 within 0.25 blocks) |
| 3 | Veil of Salt ring, radius 3,550, with the §1 shelf dropoff | ✅ |
| 4 | Climate in the spec's five tiers, per-region windows | ✅ |
| 5 | Water: rivers from this project's own river logic, lakes only where real | ✅ |
| 6 | Spec §6 slope-aware surface table (grass/scree/ice/treeline) | ✅ |
| 7 | Export: chunks at `minecraft:features`, `generate_features = 0`, populate mask | ✅ |
| 8 | Landmark placement verified on a cheap render before the full run | ✅ |
| 9 | **Full 8,000 × 8,000 Anvil world on disk (250,000 chunks) and downloadable** | ⏳ **the open item** |
| 10 | Structures / giant fungal trees placed as blocks | ⏳ explicitly deferred (mods place features) |

The user's standing rules, which you must not silently change:

* **If a spec datapoint is missing or ambiguous, ask — never guess.**
* Rivers must use **this project's own algorithm**, not ported mod code.
* Export format is a Minecraft Java save (Anvil `.mca` + `level.dat`), with the mods
  (Lithosphere / Still Life) doing the decoration — **vanilla decoration must stay off**.
* Reference documents live on the GitHub remote's `main` branch, not in the workspace.

---

## 2. Repository map

Repo: `https://github.com/Exo2v/MAP-generator.` (the GitHub slug really does end in a dot).

```
mg/
  config.py                  default config, presets (incl. "ashenfall"), merge/validate
  pipeline.py                runs every stage, in order; returns GenerationResult
  core/
    noise.py                 value/gradient noise, fbm, ridged/billow, domain warp,
                             Catmull-Rom spline(), smoothstep(), stretch01()
    hydrology.py             priority-flood fill, D8 dirs, flow vectors, accumulation,
                             watershed labels, lake fill, downstream order
    erosion.py               thermal/diffusive erosion, stream-power incision, droplets,
                             channel smoothing, slope_map(), hillshade()
    types.py                 RegionInfo, TerrainGrid, RiverPath, Lake, biome ids/colours
    materials.py             block ids, palettes, vanilla biome ids
    bluenoise.py             blue-noise matrix used for the populate mask
  generation/
    ashenfall.py             THE continent engine (fields + climate + base height)
    landform.py              the nine landmark shapers, shelf, Veil, dry/no_lake masks,
                             repin_landmark_centres()
    landmarks.py             the spec tables: canvas, elevations, 9 landmarks + veil,
                             spline/climate windows, feather(), region_ids()
    climate.py               generic climate model (temperature/humidity/rain shadow)
    water.py                 WaterSystem: rivers + lakes + flow + no_pond enforcement
    rivers.py                the project's own river logic (Flint profile, meanders, delta)
    biomes.py                climate biome classifier, fertility
    population.py            Still Life-style density: trees, shrubs, flowers, ores, POIs
    surface.py               TerrainSampler: per-block columns, caves, surface layers
    terrain.py               the *generic* chain (presets other than ashenfall)
  export/
    anvil.py                 RegionFileWriter + chunk NBT (1.18+ sections, palettes)
    nbt.py                   NBT read/write (gzip)
    world.py                 export_world(): chunks, resume, level.dat, maps, masks
    worldpainter.py          the WorldPainter asset set (PNG masks + JSR-223 script)
  tools/
    verify_ashenfall_world.py  reads .mca files back and checks the landmark table
    set_world_decoration.py    flips generate_features in an existing save
    inspect_world.py           chunk decoder used by the verifier
  server/                    HTTP API + Three.js preview UI (app.py, views.py, jobs.py)
generate_ashfall.py          the turnkey CLI for this map
tools/
  pack_world_download.py     shard the save into ≤100 MB ZIPs; --watch / --push / --restore
  restore_sandbox.sh         one-command recovery after the sandbox is wiped
  build_ashenfall.sh         supervisor used during earlier runs
  fill_release_notes.py      fills RELEASE_NOTES.md placeholders from the real shards
tests/                       82 unit tests (export, packer, pipeline, noise, ...)
release/ashenfall-world/     the download: shards + README.md + RELEASE_NOTES.md
assets/ashenfall/            checked-in WorldPainter asset set (shipped in v1.0.0)
docs/specs/ashenfall_spec.txt   extracted text of the master specification
```

---

## 3. Numbers you will need

**Canvas and units** (spec §1)

| quantity | value |
| --- | --- |
| canvas | 8,000 × 8,000 blocks, `x0 = z0 = −4000` |
| vertical | Y −64 … 320 (384 blocks = 24 sections) |
| sea level | Y = 62 |
| heightmap encoding | `h = (Y + 64) / 384`, `uint16 = round(h × 65535)` |
| inverse | `Y = −64 + norm × 384` |
| data version | 3953 (Minecraft 1.21.1, pack 48) |

**Grid sizes and measured cost on this 2-core box**

| `--cell` | cells | generation | notes |
| --- | --- | --- | --- |
| 4 | 2,000² = 4,000,000 | ≈ 11 min, ≈ 2.5 GB RSS | shipped resolution |
| 8 | 1,000² | 149.5 s, 0.63 GB | used for the cheap landmark check |
| 16 | 500² | ≈ 40 s | UI default |
| 32 | 250² | ≈ 9 s | quick sketches |

**Export cost:** 250,000 chunks (250 region files). Measured 30–35 chunks/s with
`--compression 9` while generation runs on the other core; ≈ 2 hours. Each region file is
1,056,768 bytes; the zipped world is ≈ 312 MB (1,247 bytes/chunk measured on a 4,096-chunk
probe). Region files flush every 512 *unwritten* chunks — see the trap in §11.

**Seed / config:** `--seed 20250929`, preset `ashenfall`, `--res 2048` (the spec's own
worldpainter command uses 2048).

---

## 4. The generation pipeline, stage by stage, with the math

Entry point: `run_pipeline(cfg)` in `mg/pipeline.py`. For `preset == "ashenfall"` it takes the
dedicated engine; the generic chain in `mg/generation/terrain.py` is bypassed. Progress is
reported at 0.12–0.28 (ashenfall build), 0.28–0.50 (erosion), 0.50–0.70 (water), then biomes
and population to 1.0.

### 4.0 Grid and climate

`RegionInfo(-4000, -4000, 8000, 8000, cell_size=4)`. The generic `ClimateModel` produces
temperature and humidity in 0…1 (`region_scale = 3200`, `region_octaves = 4`, lapse rate
`0.00275` per block above sea level, wind from 90°, rain shadow on). The Ashenfall engine then
converts to the spec's −1…+1 units:

```
spec = 2 · unit − 1
```

### 4.1 Continentalness — `AshenfallBuilder.build`, step 1

```
(wx, wz) = domain_warp(X, Z, scale=2100, strength=900, octaves=4, seed+17)
R_coast  = coast_radius(wx, wz)                     # periodic by bearing
         + fbm(X, Z, octaves=3, scale=2600, seed+19) · 620
         + fbm(X, Z, octaves=2, scale= 820, seed+29) · 190
Δ       = hypot(wx, wz) − R_coast
cont    = spline(COAST_SPLINE, Δ)
cont   += 0.12 · exp(−((X/2700)² + ((Z+1500)/2300)²))     # inland bonus
cont   += fbm(X, Z, octaves=3, scale=900, seed+23) · 0.05
cont    = clip(cont, −1.20, 1.20)
cont    = windows(cont, key="cont", weight=0.60)
```

`coast_radius` interpolates this bearing table (degrees, 0 = +X/east, 90 = +Z/south; the
table is wrapped ±360° so the interpolation is periodic):

| θ° | −180 | −135 | −90 | −45 | 0 | 30 | 45 | 70 | 90 | 120 | 146 | 180 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R | 3350 | 3320 | 3480 | 3260 | 3150 | 3120 | 3150 | 3080 | 3200 | 2860 | 2700 | 3150 |

`COAST_SPLINE` (`Δ` → continentalness): (−2600, .60), (−1200, .52), (−700, .44), (−400, .31),
(−150, .15), (−40, .05), (0, −.02), (140, −.20), (450, −.45), (950, −.70), (2600, −1.05).

`windows()` is how every spec range is applied. For each landmark:

```
unit   = clip((field − p1) / (p99 − p1), 0, 1)        # percentile-normalised
target = window[0] + (window[1] − window[0]) · unit
taper  = feather(mask, width = max(220, 12 · cell))
k      = clip(taper · weight, 0, 1)
field  = field·(1 − k) + target·k
```

### 4.2 Erosion and ridges — step 2

```
ero = stretch01(fbm(X, Z, octaves=4, scale=1500, seed+31), 1, 99) · 2 − 1
ero = windows(ero, "erosion", 0.70)
rid = stretch01(fbm(X, Z, octaves=5, scale=1250, seed+41), 1, 99) · 2 − 1
rid = windows(rid, "ridges",  0.70)
```

### 4.3 Climate tiers — step 3 (`_climate`)

```
t = clip(temp_unit, 0, 1)·2 − 1 ;  h = clip(hum_unit, 0, 1)·2 − 1
reg = fbm(X, Z, octaves=3, scale=2400, seed+91)
t = clip(t·0.45 + reg·0.85 + 0.10, −1.15, 1.15)
h = clip(h·0.55 + fbm(X, Z, octaves=3, scale=2000, seed+97)·0.7, −1.15, 1.15)
# per-landmark climate window, taper weight 0.85  (same blend as §4.1)
# veil (r > 3550):  t = min(t, −0.55);  h = clip(h, −0.4, 0.6)
t = clip(t, −1.2, 1.2);  h = clip(h, −1.2, 1.2)
return (t+1)/2, (h+1)/2                     # back to 0..1 for the rest of the engine
```

`blend_tiers(t_unit)` — spec §4 five tiers, on the −1…1 scale:

| tier | condition | meaning |
| --- | --- | --- |
| 0 | t ≤ −0.75 | glacial / arctic |
| 1 | −0.75 < t ≤ −0.25 | boreal buffer belt |
| 2 | −0.25 < t ≤ 0.40 | temperate lowlands |
| 3 | 0.40 < t < 0.70 | subtropical bayou / arid |
| 4 | t ≥ 0.70 | arid / volcanic (default) |

### 4.4 Base height — step 4

```
land = smoothstep(−0.12, 0.28, cont)
base = spline(BASE_SPLINE, cont)
```

`BASE_SPLINE`: (−1.05, 20), (−0.85, 30), (−0.60, 40), (−0.35, 48), (−0.20, 53), (−0.02, 60),
(0.05, 63), (0.12, 66), (0.25, 68), (0.45, 74), (0.65, 84), (0.85, 98), (1.15, 122).

```
rid01 = clip(rid·0.5 + 0.5, 0, 1)
belt  = smoothstep(0.48, 0.95, rid01) · smoothstep(0.20, 0.62, cont)   # ridges only inland
amp   = 18 + 145 · belt
shape = ridged_fbm(X, Z, octaves=6, scale=1050, seed+51)
relief = amp · shape · land

detail_scale = clip(1.1 − 0.7·(ero·0.5 + 0.5), 0.2, 1.2)   # jagged where ero<−0.45
rolling = fbm(X, Z, octaves=4, scale=1150, seed+61) · (4 + 9·land)
hills   = fbm(X, Z, octaves=4, scale= 420, seed+71) · (3 + 6·land)
detail  = fbm(X, Z, octaves=4, scale= 120, seed+81) · 2.6

dem = base + (relief + rolling + hills)·detail_scale + detail·land
```

### 4.5 The nine landmarks + shelf + Veil — `mg/generation/landform.py`

The exact spec table, as encoded in `landmarks.py` (index = id used in masks):

| id | landmark | centre (x, y, z) | box x | box z | spec Y band | kind |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | Forgotten Coast | (0, 68, 2500) | −600…600 | 2000…3200 | 68–74 | coast |
| 1 | Cogwork March | (−2100, 85, 0) | −2800…−1400 | −700…700 | 85–110 | quarry |
| 2 | Ashen Caldera | (0, 80, 0) | −750…750 | −750…750 | 38–150 | caldera |
| 3 | Solitary Glacial Spine | (0, 220, −2500) | −1800…1800 | −3500…−1500 | 180–279 | cordillera |
| 4 | Gilded Dunes | (2300, 75, 0) | 1600…3100 | −800…800 | 75–94 | dunes |
| 5 | Whispering Fen | (2000, 63, 2000) | 1300…2700 | 1300…2700 | 62–66 | fen |
| 6 | Sunken Reach | (−2400, 54, 1600) | −3200…−1700 | 1000…2300 | 50–62 | drowned shelf |
| 7 | Hermit's Spire | (−1800, 140, −1800) | −2300…−1300 | −2300…−1300 | 140–185 | spires |
| 8 | Byzantine Choir | (1800, 120, −1800) | 1300…2300 | −2300…−1300 | 110–145 | terraces |
| — | Veil of Salt | ring, r > 3550 | whole canvas | whole canvas | −32…62 | veil |

Each landmark also carries the three Lithosphere density windows and the two climate windows
(spec §3/§4) — printed in full by:

```bash
python3 -c "import mg.generation.landmarks as L; [print(l.key, l.spline, l.climate) for l in L.LANDMARKS]"
```

Selected shapers (the rest are analogous):

* **Caldera**: `wall = 146 · exp(−((r − ring_r)/ring_w)²) · wobble`,
  `spire = (92 − floor) · exp(−(r/60)²)`, floor Y = 40, rim 142–156.
* **Shelf dropoff** (spec §1): `t = clamp((r − 3300)/500)`, `S(t) = 3t² − 2t³`,
  `H_drop = −600·S(t)` — *clamped* to the abyss floor: `max(−600·S, −32 − 62)`. The spec's
  literal −600 would pass through the world floor at Y = −64.
* **Cordillera / spires**: crests and needles with randomised `sigma ∈ [38, 62]` blocks.

Landmark biomes (written straight into the export; the first name covers low ground, the
second high/deep ground):

| landmark | biomes | spec surface |
| --- | --- | --- |
| Forgotten Coast | `plains`, `meadow` | grass block, podzol, stony shore |
| Cogwork March | `windswept_hills`, `wooded_badlands` | orange/yellow terracotta, stone, andesite |
| Ashen Caldera | `basalt_deltas`, `eroded_badlands` | basalt, blackstone, magma block, obsidian |
| Solitary Glacial Spine | `frozen_peaks`, `jagged_peaks`, `grove` | snow block, packed ice, calcite, stone |
| Gilded Dunes | `desert`, `badlands` | red sand, sandstone, terracotta; black-glass crests |
| Whispering Fen | `swamp`, `mangrove_swamp` | mud, peat, moss, coarse dirt |
| Sunken Reach | `warm_ocean`, `lukewarm_ocean` | sand, coral, prismarine gravel |
| Hermit's Spire | `windswept_hills`, `meadow` | granite, stone, cobblestone |
| Byzantine Choir | `meadow`, `cherry_grove` | cherry terraces, stone, gilded ruins |
| Veil of Salt | `deep_cold_ocean`, `deep_ocean` | gravel, deepslate, calcite salt crust |

### 4.6 Erosion and diffusion — `mg/core/erosion.py`

Preset values (`preset ashenfall` → `erosion`): `enabled`, `diffusion 0.1`,
`thermal_iterations 6`, `thermal_rate 0.15`, `fluvial_iterations 6`, `fluvial_k 0.5`,
`fluvial_m 0.6`, `fluvial_n 1.0`, `max_incision 0.6`, `deposition 0.5`,
`channel_smoothing 1.2`, `droplets 0`.

The stream-power step is the standard detachment-limited form

```
Δh = −k · A^m · S^n        (k = 0.5, m = 0.6, n = 1.0),  incision capped at 0.6 blocks
```

Thermal erosion is hillslope diffusion on the Laplacian with rate 0.15 per pass, 6 passes;
`channel_smoothing` applies a Gaussian along cells whose accumulation passes the river
threshold, and `diffusion_pass` smooths more near sea level (beaches) and less on cliffs.

### 4.7 Hydrology — `mg/core/hydrology.py` + `mg/generation/water.py`

1. `priority_flood(dem, epsilon=1e-4)` — Barnes et al. 2014 priority-flood; removes every
   closed depression, giving a monotonically drainable surface.
2. `flow_directions` — D8 steepest descent; `flow_vectors` — direction vectors smoothed once
   (`flow_model = "dinf"` in the preset).
3. `flow_accumulation` — drainage area, processed in downstream order.
4. `fill_lakes` — lakes only where the filled surface stands above the filled DEM:
   `lake_min_depth 1.0`, `lake_min_cells 4`, `max_lakes 200`.
5. Rivers: cells whose accumulation ≥ `river_threshold 900` (climate-biased by 0.7), carved
   over 3 passes with `valley_depth 1.25`, `bank_flare 2.0`, floodplain width 0.3.
6. Waterfalls where the bed drops ≥ 3.5 blocks across a step.

### 4.8 The river logic — `mg/generation/rivers.py` (own algorithm)

Three geomorphic ideas, all pure numpy on ordered polylines:

**(a) Graded longitudinal profile (Flint's law).** For a channel ordered source → mouth with
discharge `Q(s)` and total fall `Δz = z[0] − z[−1]`:

```
w_i        = Q_i^(−θ),          θ = 0.45        (0.4–0.6 is the real-world range)
seg_weight = ½(w_i + w_{i+1})
slope_i    = Δz · seg_weight / Σ(seg_weight · ds)
graded     = walk upstream from the mouth:  graded[i] = graded[i+1] + slope_i · ds_i
blend      = clip((Q − 90)/90, 0, 1) · 0.85     # only big rivers grade their bed
z_new      = z·(1 − blend) + graded·blend
```

This keeps the total fall (the mouth stays at base level) and redistributes it concave-up:
steep headwaters, flat mouth.

**(b) Knickpoints and meanders.** A knickpoint is a step steeper than `2.2 ×` the graded
slope with a drop > 3 blocks; knickpoints migrate upstream at a rate proportional to
discharge (0.35). Meanders apply real curvature dynamics:

```
κ   = (x′z″ − z′x″) / (x′² + z′²)^{3/2}
turn = −κ · magnitude                       # erode the outer bank
slowness = 1 / (1 + 14·slope)               # only slow, large lowland reaches migrate
displacement = 0.45 · bankfull_width        # per pass, 6 passes
cutoff when the channel comes within 1.6 × width of itself → oxbow lake
```

Bend wavelength is set at the physical 10–14 channel widths (Leopold & Wolman), and a delta
is built where `Q ≥ 800` meets the sea: 3 distributaries spreading over a 90-block reach.

**(c) Hydraulic geometry.** Width and depth follow discharge through the network,
`W ∝ Q^0.5`, `D ∝ Q^0.4`, with a bank flare so the channel blends into the hillslope.

### 4.9 Keeping the shaped landforms dry — `DRY_KINDS` / `no_lake`

A quarry bench, a dune swale, a terrace and a caldera rim all enclose closed sub-basins, so
the depression filler would flood them. The masks:

```
DRY_KINDS = (caldera, dunes, quarry, terraces, coast)
no_lake   = union of those regions' cells     → no standing water, rivers still cross
no_water  = caldera interior | lava sheets    → nothing at all (absolute)
```

Rule inside `water.py`: a lake with **≥ 50 %** of its cells inside `no_pond` is dropped
entirely; one that merely laps over the border keeps its valid part and has the overlap
trimmed. A final invariant re-checks `water_mask == 2 & no_pond` after every pass — that
check is what fixed an earlier build that filled 39 % of the caldera.

### 4.10 Landmark re-pinning — `repin_landmark_centres`

Erosion and river carving move the ground a few blocks, but the spec gives an exact elevation
for every centre, so the pin is re-applied to the finished surface:

```
radius = 220 blocks, strength = 1.0, max_delta = 6.0 blocks
skips: caldera, lakes, open sea
lifts the river surface together with its bed if a channel crosses the pin
```

Result on the last full run: all eight ground-level centres within **0.25 blocks** of spec
(the caldera is exempt — its centre is the Obsidian Throne at Y = 92, not ground).

### 4.11 Biomes, fertility, population

`biomes.py` classifies from `(temperature, humidity, height, slope)` with the preset bands
(highland 40, alpine 90, peak 160, beach height 4, snowline base 190, swamp max slope 0.18,
etc.). `population.py` produces the Still Life-style density field: tree/shrub/flower/boulder
densities, ore veins (90 veins, radius 3.2), POIs, roads. **In the Ashenfall export this is
used to build the populate mask, not to place blocks** — the mods place the features.

### 4.12 Block-level surface — `mg/generation/surface.py`

Per chunk (16 × 16 columns × 384 y):
columns from the interpolated macro fields, biome at block level (climate biomes re-derived
per block to kill cell staircases), caves, then the spec §6 slope-aware surface rule:

| slope | surface |
| --- | --- |
| < 25° | biome topsoil (grass / podzol / sand / terracotta …) |
| 25–35° | 40 % exposed rock mixed into the topsoil |
| 35–45° | 5 % stone; scree fraction `clip((deg − 35)/12, 0, 1)` |
| > 45° | bare rock, no soil |
| Y > 225 | treeline: snow block (frozen peaks) or calcite, filler packed ice |

Snow line = `190 + value_noise(x/60, z/60, seed+6001) · 6.0` (preset `snowline_base = 190`).
Deepslate takes over below Y = 0 with a noise-softened boundary.

---

## 5. Export format — what actually lands on disk

`mg/export/world.py::export_world()` writes a Java 1.21.1 save:

```
level.dat              gzipped NBT: LevelName, seed, spawn, GameType 1, DataVersion 3953,
                       WorldGenSettings{ seed, generate_features },
                       dimensions{ minecraft:overworld → noise generator }
level.dat_old          copy (kept in step by the decoration tool)
session.lock           8 zero bytes
mapgen.json            the exact config, so the world regenerates bit-for-bit
README.txt             what is in the save and how it was made
region/r.X.Z.mca       Anvil: 8 KiB header (locations + timestamps), 4 KiB sectors
maps/*.png             height, biome, climate, water, flow, population, soil
worldpainter/          heightmap + masks + surface table + setup JS
```

Chunk NBT (1.18+ layout): `DataVersion`, `xPos`, `zPos`, `Status`,
`sections[24] → { Y, block_states{palette[+data]}, biomes{palette[+data]} }`, plus
`LastUpdate` / `InhabitedTime`. Palette indices are bit-packed with
`bits = max(4, ceil(log2(palette_size)))`, `entries_per_word = 64 // bits`.

**The decoration contract** (spec §5 method 1, and the user's explicit instruction):

| mode | chunk `Status` | `generate_features` | who decorates |
| --- | --- | --- | --- |
| `mods` (default for ashenfall) | `minecraft:features` | **0** | Lithosphere / Still Life only |
| `engine` | `minecraft:full` | 1 | baked in-engine |

`export.generate_features` overrides either. The populate mask
(`ASHFALL_POPULATE_MASK.png`) marks the cells the mods are allowed to plant: not water, slope
< 35°, above sea level +1, below the treeline — 27.2 % of the canvas on the last run.

**Resume.** `export.resume = true` skips chunks already present in their region file, and the
writer **adopts** the chunks already on disk before rewriting (otherwise a resumed file is
truncated to only the chunks regenerated in that run — this bug existed and is now tested).
Resume keys on chunk *presence*, not content: never edit generation code while an export is
running or between resumes, or the world becomes a mix of two versions.

---

## 6. The commands, verbatim

### 6.1 Environment

```bash
pip install --break-system-packages numpy scipy pillow pypdf
python3 -m unittest discover -s tests           # 82 tests, ~25 s
```

### 6.2 Build

```bash
# the spec's own command (WorldPainter asset set)
python generate_ashfall.py --res 2048 --out worldpainter

# the full Minecraft world (this is the long one)
python3 generate_ashfall.py --res 2048 --out anvil --cell 4 --compression 9 \
        --dir out/ashenfall --progress-log 45

# both in one pass
python3 generate_ashfall.py --out both --cell 4 --dir out
```

Useful flags: `--size` (override canvas, for cheap probes), `--compression 9` (smallest
download; the preset default is 2), `--progress-log SECONDS` (timestamped lines instead of a
`\r` bar, so a redirected log stays readable), `--quiet`.

### 6.3 3D preview / API

```bash
python3 -u -m mg.server.app --no-browser --host 0.0.0.0 --port 8000
# GET /api/config, /api/preset/ashenfall, POST /api/generate, GET /api/mesh,
# GET /api/maps/<kind>.png, POST /api/export  (needs {"output_dir": ..., "options": {...}})
```

### 6.4 Verify

```bash
python3 -m mg.tools.verify_ashenfall_world out/ashenfall/Ashenfall --pad 0.25
```

It reads the `.mca` files back — never the generator's state — and compares each landmark's
top block and Y with the spec band. Exit code 0 means the save really holds the continent.

### 6.5 Package and publish

```bash
# one-shot split of a finished world (4 shards ≈ 78 MB each)
python3 tools/pack_world_download.py out/ashenfall/Ashenfall \
        --out release/ashenfall-world --parts 4 --version v1.1.0

# follow a running export: zip finished regions, push each shard to the branch
python3 tools/pack_world_download.py out/ashenfall/Ashenfall \
        --out release/ashenfall-world --version v1.1.0 \
        --watch --push --interval 90 --min-raw-mb 40 --max-mb 90 --expect-regions 250

# rebuild out/ from the published shards (after a wipe)
python3 tools/pack_world_download.py out/ashenfall/Ashenfall \
        --out release/ashenfall-world --restore
```

### 6.6 Decoration flag

```bash
python3 -m mg.tools.set_world_decoration <world-dir> --mods      # generate_features = 0
python3 -m mg.tools.set_world_decoration <world-dir> --vanilla   # generate_features = 1
python3 -m mg.tools.set_world_decoration <world-dir> --mods --dry-run
```

### 6.7 Release notes

```bash
python3 tools/fill_release_notes.py --tag v1.1.0 \
        --repo Exo2v/MAP-generator. --branch arena/01a0ece3-map-generator \
        --verify "$(python3 -m mg.tools.verify_ashenfall_world out/ashenfall/Ashenfall | tail -12)" \
        --tests "82 tests OK" --out out/release_notes_v1.1.0.md
```

---

## 7. Release mechanics (and the GitHub constraints)

* GitHub rejects files > 100 MB, and **`uploads.github.com` is unreachable from this
  sandbox** (`SSL_ERROR_SYSCALL`), so `gh release create --attach` and `gh release upload`
  cannot be used. The first attempt rolled the whole release back.
* Therefore the world ships **as ZIP shards committed to the repository**, and the tag's
  source archive carries them too. Verified once on `v1.0.0` by downloading
  `https://codeload.github.com/Exo2v/MAP-generator./zip/refs/tags/v1.0.0`.
* Shards are ordinary ZIPs of whole region files: extract all of them into
  `.minecraft/saves/Ashenfall`, order does not matter. `SHA256SUMS.txt` sits beside them.
* `release/ashenfall-world/README.md` is the player-facing install guide;
  `RELEASE_NOTES.md` is the release text template.
* Tagging (when the world is complete):

```bash
git tag -a v1.1.0 -m "v1.1.0 — Ashenfall: the continent of Vantyra, playable world"
git push origin v1.1.0
gh release create v1.1.0 --title "..." --notes-file out/release_notes_v1.1.0.md   # no --attach
```

* `v1.0.0` already exists: WorldPainter asset set in `assets/ashenfall/` (9 files + README),
  tag pointing at the commit that added them.
* Pushing: `git push origin arena/01a0ece3-map-generator` — this session is fixed to that
  branch. If a push is rejected non-fast-forward after a reset: `git reset --soft origin/<branch>`
  and re-commit; never force-push.

---

## 8. Verification and tests

```bash
python3 -m unittest discover -s tests        # 82 tests
```

Coverage that matters for a build of this size:

* `TestResumeExport` — an interrupted export must not truncate a resumed region file.
* `TestRegionFlushBatching` — one full region is written exactly twice (512 + 1024 chunks),
  i.e. regions are flushed per batch; fails on the old "flush on file total" rule.
* `TestDecorationFlag` — `mods` → `generate_features 0`, `engine` → 1, explicit override
  wins, the patch tool flips both `level.dat` and `level.dat_old` and refuses a keyless file.
* `TestNoPonding` — `no_lake` removes standing water inside the mask and keeps channels;
  `no_water` is absolute.
* `tests/test_pack.py` — shards partition the regions exactly, respect the size budget, never
  reuse a shard name, and never publish a half-written region.

End-to-end check on any export: §6.4.

---

## 9. Operational runbook

### 9.1 Sandbox resets (they happen — 9 so far)

The workspace is periodically wiped: pip packages vanish, processes die, and git is rewound to
the base commit. **Only the repository survives.** One command fixes it:

```bash
bash tools/restore_sandbox.sh
```

It installs the deps, `git reset --hard origin/arena/01a0ece3-map-generator`, then unpacks the
published shards into `out/ashenfall` so the resumable export continues from the chunks that
were already pushed. Then start the two long-lived processes again (§0).

### 9.2 Long jobs

Use background processes with output to a log and poll; never a foreground command that
outlives a tool timeout. The export is wrapped in a supervisor loop:

```bash
for i in $(seq 1 200); do
  python3 generate_ashfall.py ... --dir out/ashenfall --progress-log 45 >> out/world_export.log 2>&1
  rc=$?; [ $rc -eq 0 ] && { echo "=== BUILD COMPLETE" >> out/world_export.log; break; }
  sleep 20
done
```

A silent stage is normal — progress lines only appear at stage boundaries, every 45 s.

### 9.3 How to tell where you are

```bash
tail -3 out/world_export.log                     # stage + percentage
ls release/ashenfall-world/*.zip | wc -l          # published shards
python3 - <<'PY'                                  # complete regions (of 250)
import os
d='out/ashenfall/Ashenfall/region'
n=[x for x in os.listdir(d) if x.endswith('.mca')]
c=[x for x in n if sum(1 for i in range(1024) if open(os.path.join(d,x),'rb').read(4096)[i*4:i*4+3]!=b'\x00\x00\x00')==1024]
print(len(c),'of 250 complete,',len(n),'files')
PY
```

A region file is finished when all 1,024 of its chunk slots are filled; the publisher only ever
zips finished regions, so a shard is always a valid piece of the world.

---

## 10. Open items, in priority order

1. **Finish the world.** Start/continue §6.2, watch §6.5 push shards, confirm `BUILD COMPLETE`.
2. **Verify the finished save** with §6.4 and paste the output into the release notes.
3. **Tag and release `v1.1.0`** per §7 (no `--attach`).
4. **Deferred by decision, not by accident:** structures are terrain only; Whispering Fen
   heartwood exists in the surface table and palette but the giant fungal trees are not placed
   as blocks; cirque tarns on the Glacial Spine and near the Hermit's Spire are deliberately
   left as pondable ground.
5. **Long-term:** generalise the same software into the any-purpose tool (the preset system
   and the generic chain in `mg/generation/terrain.py` are the seam).

---

## 11. Traps — every one of these has already bitten this build

1. **The repo folder is `/home/user/MAP-generator.`** — with a trailing dot. Always quote it.
2. **Spec documents are not in the workspace**; they live on `origin/main`
   (`git show origin/main:path`). The extracted text is vendored at
   `docs/specs/ashenfall_spec.txt`.
3. **Never edit generation or surface code while an export is running or before a resume** —
   resume keys on chunk presence, so the world silently mixes two versions.
4. **Region flushing:** flush on *unwritten* chunks (`RegionFileWriter.pending`), not on the
   file's total, or every chunk after the first batch rewrites a 1 MB file (~2× export time).
5. **Resume adoption:** a writer opening an existing region file must `adopt_existing()` first,
   or it truncates the file to only its new chunks.
6. **`generate_features` must agree with the chunk status.** `minecraft:features` + `1` means
   vanilla decorates what method 1 leaves for the mods. `mods` ⇒ 0.
7. **Water is never inferred from height** — only `water_mask > 0` gets water; the shaped
   landforms rely on `no_lake`/`no_water` masks, and the invariant block at the end of
   `water.py` re-checks them for a reason.
8. **`uploads.github.com` is blocked**; never burn a release with `--attach`. If a release is
   created with a failing asset, GitHub rolls the whole release back.
9. **`gh`/git credentials can go stale mid-session**; the publisher reports a push failure
   instead of pretending. Re-check with `gh auth status` before a release.
10. **EXE build is impossible in this sandbox** — `/usr/bin/python3` has no shared libpython
    (`tools/build_exe.py --check` exits 2). Build the .exe on Windows.
11. **Slicing source text is forbidden** (a slice-replace once destroyed `rivers.py`); use
    `s.replace(old, new)` with `assert old in s`.
12. Long tool calls hit timeouts: use background processes + logs, and poll with a single
    long `wait` rather than a `sleep`/`curl` loop.

---

## 12. Provenance

* Engine spec: World Machine / World Painter / terrain-diffusion logic fused with
  Lithosphere-style temperature, Still Life-style population and Streams-Reflowing-style water
  — reimplemented originally (no mod code decompiled or ported).
* This map: `ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf` (on `origin/main`, commit
  `6b1da75`), vendored extract at `docs/specs/ashenfall_spec.txt`.
* River algorithm: original, documented in `mg/generation/rivers.py` (Flint profile,
  knickpoint migration, curvature-driven meanders with cutoffs, distributary deltas).
* Reference documents that shaped the design: Lithosphere (climate/continent overhaul),
  Streams Reflowing + "Why Are Rivers So Complicated?" (bounded, height-aware river paths),
  Still Life (biome variants, transitional biomes, denser population).
