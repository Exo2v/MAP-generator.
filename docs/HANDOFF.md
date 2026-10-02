# Ashenfall / Vantyra — World Build Handoff

**What this document is.** A complete, self-contained description of a Minecraft terrain
generator and of the one world it is currently being used to produce. It explains the goal, the
data the world is built from, the algorithms and mathematics that turn that data into terrain,
the file formats that make the result playable, the commands that run and verify it, and the
failure modes the design has to defend against. Everything here is reproducible from the
repository alone — no external context is required.

Read §1 for the goal, §2 for what exists, §3 for the specification data, §5–§7 for the
algorithms and their formulas, §9 for the commands, and §12 before debugging anything.

---

## 1. The goal

Produce the continent of **Vantyra** — the world of *Ashenfall* — exactly as its master
specification describes it, and ship it as a playable, mod-ready **Minecraft Java Edition**
world.

The specification (`ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf`) pins down:

* an 8,000 × 8,000-block canvas centred on the origin, with a fixed sea level;
* nine named landmarks, each with an exact centre coordinate, an exact elevation band and an
  exact bounding box;
* a continental shelf that drops away into a ring ocean ("the Veil of Salt");
* a five-tier climate model with per-region temperature and humidity windows;
* three Lithosphere-style density functions (continentalness, erosion, ridges), each with a
  permitted range per region;
* a slope-aware surface table (soil, scree, ice, treeline);
* a population method that leaves decoration to mods rather than baking it into the terrain.

The generator does not "make an interesting map and hope it resembles the spec". It treats the
specification's tables as the authority and uses noise only for texture: every landmark is
shaped to land on its stated elevation, and the finished surface is re-checked against the
specification's table by reading the exported files back from disk.

At the same time the software is a general-purpose generator: terrain, a live 3-D preview, and
export to a user-chosen folder. Ashenfall is the current, specified build; other presets exist
and the same pipeline serves them.

### 1.1 Acceptance criteria

| # | Criterion | Notes |
| --- | --- | --- |
| 1 | 8,000 × 8,000 blocks centred on the origin, Y −64…320, sea level Y = 62 | per spec §1 |
| 2 | Nine landmarks at their exact centres and elevation bands | verified by reading the exported `.mca` files back |
| 3 | Veil of Salt ring with the specified shelf dropoff | ring radius 3,550 |
| 4 | Climate on the spec's five tiers, with per-region windows | spec §4 |
| 5 | Rivers and lakes from the project's own river algorithm | no ported mod code |
| 6 | Spec §6 slope-aware surface table | grass / scree / bare rock / ice / treeline |
| 7 | Export as a Java save with chunks left undecorated for the mods | spec §5 method 1 |
| 8 | Landmark placement confirmed on a cheap preview before the full-resolution run | workflow requirement |
| 9 | The full-resolution world on disk (250,000 chunks) and downloadable | the heavy step; ~2 h on 2 cores |
| 10 | Structures and giant fungal trees placed as blocks | deliberately out of scope for this pass: the mods place features |

### 1.2 Project rules (non-negotiable)

* **If a specification datapoint is missing or ambiguous, ask — never guess.** Every number in
  the tables below comes from the document.
* **Rivers use this project's own algorithm**; the referenced mods inform the design, but no
  mod code is decompiled, ported or copied.
* **Export format is a Minecraft Java save**: Anvil `.mca` region files plus `level.dat`.
* **The mods decorate, vanilla does not**: chunks are written at generation status
  `minecraft:features` and `level.dat` carries `generate_features = 0`.
* **Reference documents live on the repository's `main` branch**, not in a working directory.

---

## 2. What exists

The repository contains a working generator (`MAP-generator.`):

| Component | Location | Purpose |
| --- | --- | --- |
| Core engine | `mg/` | noise, hydrology, erosion, generation, export, HTTP/3-D UI |
| Ashenfall engine | `mg/generation/ashenfall.py`, `landform.py`, `landmarks.py` | the specified continent |
| Turnkey CLI | `generate_ashfall.py` | build the continent (WorldPainter set, Minecraft world, or both) |
| Web preview | `mg/server/` | HTTP API + Three.js viewer for `POST /api/generate`, `/api/mesh` |
| Desktop/EXE packaging | `tools/build_exe.py`, `tools/build_exe.bat` | PyInstaller bundle of the same UI |
| Download packer | `tools/pack_world_download.py` | splits a save into ZIP shards; watches a live export |
| Decoration tool | `mg/tools/set_world_decoration.py` | flips `generate_features` in an existing save |
| Verifier | `mg/tools/verify_ashenfall_world.py` | reads exported region files and checks the spec table |
| Tests | `tests/` | 82 unit tests, including the export/resume/packer cases |
| Build layers | `assets/ashenfall/` | a checked-in WorldPainter asset set (rasters + setup script) |
| Release notes | `release/ashenfall-world/` | shards, install guide, release-note template |

Two output products are defined:

1. **WorldPainter asset set** — 16-bit heightmap, masks (populate, water, scree, frost, slope),
   biome map, surface table as JSON, and a JSR-223 script that rebuilds the world inside
   WorldPainter. Fast to produce; the map can be hand-edited afterwards.
2. **Minecraft Java save** — the terrain baked into Anvil region files, ready to drop into
   `saves/`. This is the heavy product: one chunk of work per 16 × 16 blocks.

---

## 3. The specification data, in full

Everything in this section is transcribed from the specification. A build that disagrees with
this section is wrong, not creative.

### 3.1 Canvas and units (spec §1)

| Quantity | Value |
| --- | --- |
| Canvas | 8,000 × 8,000 blocks, `x0 = z0 = −4000` (origin at the centre) |
| Vertical range | Y −64 … 320 (384 blocks = 24 chunk sections) |
| Sea level | Y = 62 |
| Heightmap encoding | `h = (Y + 64) / 384`; `uint16 = round(h × 65535)` |
| Inverse | `Y = −64 + norm × 384` |
| Data version | 3953 (Minecraft 1.21.1, resource pack 48) |
| Named elevations | world floor −64; abyss/trench floor −32; trench top 10; caldera floor 40 (band 38–42); Obsidian Throne 92; shelf top 56; sea 62; coast 68–74; dunes 82–96; quarry 85–110; caldera rim 146 (band 142–156); taiga 150–190; spine 220–279.2; treeline 225; ceiling 320 |

### 3.2 Landmarks (spec §2, §3, §4)

| # | Landmark | Centre (x, y, z) | Box x | Box z | Y band | Kind | Biomes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | Forgotten Coast | (0, 68, 2500) | −600…600 | 2000…3200 | 68–74 | coast | `plains`, `meadow` |
| 1 | Cogwork March | (−2100, 85, 0) | −2800…−1400 | −700…700 | 85–110 | quarry | `windswept_hills`, `wooded_badlands` |
| 2 | Ashen Caldera | (0, 80, 0) | −750…750 | −750…750 | 38–150 | caldera | `basalt_deltas`, `eroded_badlands` |
| 3 | Solitary Glacial Spine | (0, 220, −2500) | −1800…1800 | −3500…−1500 | 180–279 | cordillera | `frozen_peaks`, `jagged_peaks`, `grove` |
| 4 | Gilded Dunes | (2300, 75, 0) | 1600…3100 | −800…800 | 75–94 | dunes | `desert`, `badlands` |
| 5 | Whispering Fen | (2000, 63, 2000) | 1300…2700 | 1300…2700 | 62–66 | fen | `swamp`, `mangrove_swamp` |
| 6 | Sunken Reach | (−2400, 54, 1600) | −3200…−1700 | 1000…2300 | 50–62 | drowned shelf | `warm_ocean`, `lukewarm_ocean` |
| 7 | Hermit's Spire | (−1800, 140, −1800) | −2300…−1300 | −2300…−1300 | 140–185 | spires | `windswept_hills`, `meadow` |
| 8 | Byzantine Choir | (1800, 120, −1800) | 1300…2300 | −2300…−1300 | 110–145 | terraces | `meadow`, `cherry_grove` |
| — | The Veil of Salt | ring, r > 3550 | whole canvas | whole canvas | −32…62 | veil | `deep_cold_ocean`, `deep_ocean` |

Each landmark also carries three Lithosphere density windows and two climate windows:

| Landmark | continentalness | erosion | ridges | temperature | humidity |
| --- | --- | --- | --- | --- | --- |
| Forgotten Coast | 0.10 … 0.30 | −0.10 … 0.20 | −0.30 … 0.30 | −0.15 … 0.40 | −0.10 … 0.80 |
| Cogwork March | 0.25 … 0.60 | −0.35 … 0.15 | −0.80 … −0.20 | 0.00 … 0.50 | −0.40 … 0.30 |
| Ashen Caldera | 0.20 … 0.50 | −0.20 … 0.30 | 0.00 … 0.40 | 0.70 … 1.00 | −0.80 … −0.20 |
| Solitary Glacial Spine | 0.45 … 0.90 | −0.80 … −0.45 | 0.50 … 0.95 | −1.00 … −0.75 | −0.40 … 0.40 |
| Gilded Dunes | 0.20 … 0.55 | 0.25 … 0.70 | −0.40 … 0.40 | 0.70 … 1.00 | −0.80 … −0.20 |
| Whispering Fen | 0.05 … 0.25 | 0.40 … 0.85 | −0.20 … 0.20 | 0.35 … 0.60 | 0.50 … 1.00 |
| Sunken Reach | −0.35 … −0.10 | 0.10 … 0.50 | −0.50 … 0.50 | 0.35 … 0.60 | 0.40 … 1.00 |
| Hermit's Spire | 0.30 … 0.60 | −0.60 … −0.20 | 0.20 … 0.70 | −0.60 … −0.25 | −0.20 … 0.60 |
| Byzantine Choir | 0.30 … 0.65 | −0.40 … 0.10 | 0.10 … 0.60 | −0.15 … 0.40 | −0.10 … 0.80 |
| Veil of Salt | −0.90 … −0.45 | −0.60 … 0.40 | −1.00 … 1.00 | −1.00 … 1.00 | −1.00 … 1.00 |

All windows are in the specification's `−1 … +1` units.

Specified surfaces (§6 material table):

| Landmark | Surface |
| --- | --- |
| Forgotten Coast | grass block, podzol, stony shore |
| Cogwork March | orange/yellow terracotta, stone, andesite |
| Ashen Caldera | basalt, blackstone, magma block, obsidian |
| Solitary Glacial Spine | snow block, packed ice, calcite, stone |
| Gilded Dunes | red sand, sandstone, terracotta; black-glass (vitrified) crests |
| Whispering Fen | mud, peat, moss, coarse dirt |
| Sunken Reach | sand, coral, prismarine gravel |
| Hermit's Spire | granite, stone, cobblestone |
| Byzantine Choir | cherry terraces, stone, gilded ruins |
| Veil of Salt | gravel, deepslate, calcite salt crust |

### 3.3 Climate tiers (spec §4)

| Tier | Temperature | Meaning |
| --- | --- | --- |
| 0 | t ≤ −0.75 | glacial / arctic |
| 1 | −0.75 < t ≤ −0.25 | boreal buffer belt |
| 2 | −0.25 < t ≤ 0.40 | temperate lowlands |
| 3 | 0.40 < t < 0.70 | subtropical bayou / arid |
| 4 | t ≥ 0.70 | arid / volcanic (default) |

### 3.4 Surface rule (spec §6)

Slope is measured in degrees from the block-level height field.

| Slope | Surface |
| --- | --- |
| < 25° | biome topsoil (grass, podzol, sand, terracotta, mud …) |
| 25–35° | 40 % exposed rock mixed into the topsoil |
| 35–45° | 5 % stone; scree fraction `clip((deg − 35) / 12, 0, 1)` |
| > 45° | bare rock, no soil |
| Y > 225 | treeline: snow block on `frozen_peaks`, otherwise calcite; filler packed ice |

### 3.5 Population (spec §5)

The specification offers two population methods. This project uses **method 1**:

* the terrain, biomes, water, rivers, beaches, scree and frost are baked into the save;
* every chunk is left at generation status `minecraft:features`, never `minecraft:full`;
* `level.dat` sets `generate_features = 0`, so the game's own decorators do **not** run;
* a **populate mask** is exported marking the cells where decoration is allowed (not water,
  slope < 35°, above sea level + 1 block, below the treeline). The mods — Lithosphere for
  generation, Still Life for population — place the trees, plants and structures on first load.

Measured on a full build, the populate mask covers ≈ 27 % of the canvas.

---

## 4. Software architecture

```
generate_ashfall.py            turnkey CLI for this map
mg/
  config.py                    default config, presets (incl. "ashenfall"), merge/validate
  pipeline.py                  runs every stage in order; returns GenerationResult
  core/
    noise.py                   value/gradient noise, fbm, ridged/billow, domain warp,
                               Catmull-Rom spline(), smoothstep(), stretch01()
    hydrology.py               priority-flood fill, D8 directions, flow vectors, accumulation,
                               watershed labels, lake fill, downstream ordering
    erosion.py                 thermal/diffusive erosion, stream-power incision, droplets,
                               channel smoothing, slope_map(), hillshade()
    types.py                   RegionInfo, TerrainGrid, RiverPath, Lake, biome ids/colours
    materials.py               block ids, palettes, vanilla biome ids
    bluenoise.py               blue-noise matrix used for the populate mask
  generation/
    ashenfall.py               the continent engine: fields, climate, base height
    landform.py                landmark shapers, shelf, Veil, dry/no-lake masks, re-pinning
    landmarks.py               the spec tables: canvas, elevations, landmarks, windows, feather
    climate.py                 generic climate model (temperature, humidity, rain shadow)
    water.py                   WaterSystem: rivers, lakes, flow, no-pond enforcement
    rivers.py                  the project's own river logic
    biomes.py                  climate biome classifier, fertility
    population.py              Still Life-style density fields (trees, shrubs, ores, POIs)
    surface.py                 TerrainSampler: per-block columns, caves, surface layers
    terrain.py                 the generic chain used by the non-Ashenfall presets
  export/
    anvil.py                   RegionFileWriter + chunk NBT (1.18+ sections, palettes)
    nbt.py                     NBT read/write (gzip)
    world.py                   export_world(): chunks, resume, level.dat, maps, masks
    worldpainter.py            the WorldPainter asset set (PNGs + JSR-223 script)
  tools/
    verify_ashenfall_world.py  reads .mca back and checks the landmark table
    set_world_decoration.py    flips generate_features in an existing save
    inspect_world.py           chunk decoder used by the verifier
  server/                      HTTP API + Three.js preview UI
tools/
  pack_world_download.py       ZIP shards for distribution; --watch / --push / --restore
  fill_release_notes.py        fills RELEASE_NOTES.md placeholders from the real shards
  build_exe.py / .bat          PyInstaller packaging for the desktop build
tests/                         82 unit tests
release/ashenfall-world/       shards, install guide, release-note template
assets/ashenfall/              checked-in WorldPainter asset set
docs/specs/ashenfall_spec.txt  extracted text of the specification
```

Pipeline order (`mg/pipeline.py`), with the AI/UI progress fractions in brackets:

```
grid → climate [0.00–0.12] → continental fields & base height [0.12–0.28]
     → erosion + diffusion [0.28–0.50] → water: rivers, lakes, flow [0.50–0.70]
     → climate refinement → biomes, fertility, population [0.70–1.00] → assemble
```

---

## 5. The generation pipeline, step by step, with the math

All noise is seeded from the world seed (the shipped build uses **seed 20250929**), and every
field is evaluated on a regular grid whose spacing is the *cell size* — 4 blocks for the
shipped world, i.e. 2,000 × 2,000 = 4,000,000 cells.

### 5.1 Climate (generic model)

`ClimateModel` produces temperature and humidity in 0…1 from latitude bands, fBm noise, a
lapse rate of 0.00275 per block above sea level, a wind direction (90°) with a rain-shadow
term, and a region scale of 3,200 blocks. The Ashenfall engine converts to the spec's units
once at the boundary and back at the end:

```
spec = 2 · unit − 1                  # 0..1 → −1..+1
```

### 5.2 Continentalness — `AshenfallBuilder.build`, step 1

```
(wx, wz) = domain_warp(X, Z, scale=2100, strength=900, octaves=4, seed+17)
R_coast  = coast_radius(wx, wz)                              # periodic by bearing θ
         + fbm(X, Z, octaves=3, scale=2600, seed+19) · 620
         + fbm(X, Z, octaves=2, scale= 820, seed+29) · 190
Δ        = hypot(wx, wz) − R_coast                            # > 0 inland, < 0 at sea
cont     = spline(COAST_SPLINE, Δ)
cont    += 0.12 · exp(−((X/2700)² + ((Z+1500)/2300)²))        # inland bonus
cont    += fbm(X, Z, octaves=3, scale=900, seed+23) · 0.05
cont     = clip(cont, −1.20, 1.20)
cont     = windows(cont, key="cont", weight=0.60)
```

`coast_radius` interpolates this bearing table (degrees, 0° = +X/east, 90° = +Z/south); the
table is wrapped ±360° so the interpolation is periodic. The coastline is deliberately not a
circle: the continent reaches furthest north, which puts the Glacial Spine inland, and is
closest in the south-west, which creates the drowned Sunken Reach.

| θ° | −180 | −135 | −90 | −45 | 0 | 30 | 45 | 70 | 90 | 120 | 146 | 180 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R (blocks) | 3350 | 3320 | 3480 | 3260 | 3150 | 3120 | 3150 | 3080 | 3200 | 2860 | 2700 | 3150 |

`COAST_SPLINE` maps `Δ` (blocks) to continentalness:

| Δ | −2600 | −1200 | −700 | −400 | −150 | −40 | 0 | 140 | 450 | 950 | 2600 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C | 0.60 | 0.52 | 0.44 | 0.31 | 0.15 | 0.05 | −0.02 | −0.20 | −0.45 | −0.70 | −1.05 |

`spline()` is a Catmull-Rom interpolation through the control points — the World Machine-style
spline primitive. The waterline sits where `Δ = 0`, i.e. at `C ≈ 0`.

### 5.3 How a spec range becomes a field — `windows()`

The Lithosphere windows and the climate windows are applied with the same primitive. For each
landmark:

```
lo, hi = percentile(field, 1), percentile(field, 99)     # robust range of the raw noise
unit   = clip((field − lo) / (hi − lo), 0, 1)
target = window[0] + (window[1] − window[0]) · unit       # scale into the spec window
taper  = feather(mask, width = max(220, 12 · cell))       # smooth 1 inside → 0 outside
k      = clip(taper · weight, 0, 1)
field  = field · (1 − k) + target · k
```

Weights: continentalness 0.60, erosion 0.70, ridges 0.70, climate 0.85. The point of this
construction is that noise keeps its internal structure — valleys, ridges, patchiness — while
its *amplitude* is confined to the range the specification allows for that region. Landmarks
therefore differ in character, not just in average height.

### 5.4 Erosion and ridges — step 2

```
ero = stretch01(fbm(X, Z, octaves=4, scale=1500, seed+31), 1, 99) · 2 − 1
ero = windows(ero, "erosion", 0.70)
rid = stretch01(fbm(X, Z, octaves=5, scale=1250, seed+41), 1, 99) · 2 − 1
rid = windows(rid, "ridges",  0.70)
```

`stretch01(field, 1, 99)` re-normalises to the 1st–99th percentile before scaling to −1…+1.

### 5.5 Climate in spec units — step 3

```
t = clip(temp_unit, 0, 1) · 2 − 1
h = clip(hum_unit,  0, 1) · 2 − 1
reg = fbm(X, Z, octaves=3, scale=2400, seed+91)

t = clip(t · 0.45 + reg · 0.85 + 0.10, −1.15, 1.15)
h = clip(h · 0.55 + fbm(X, Z, octaves=3, scale=2000, seed+97) · 0.7, −1.15, 1.15)

# per-landmark climate windows (weight 0.85, same blend as §5.3)
# the Veil of Salt is deep ocean everywhere: cold and wet
veil: t = min(t, −0.55);  h = clip(h, −0.4, 0.6)

t, h = clip(t, −1.2, 1.2), clip(h, −1.2, 1.2)
return (t + 1)/2, (h + 1)/2           # back to 0..1 for the rest of the engine
```

Tier index from the finished temperature (`blend_tiers`): the bands in §3.3.

### 5.6 Base height and relief — step 4

```
land = smoothstep(−0.12, 0.28, cont)
base = spline(BASE_SPLINE, cont)
```

`BASE_SPLINE` (continentalness → blocks):

| C | −1.05 | −0.85 | −0.60 | −0.35 | −0.20 | −0.02 | 0.05 | 0.12 | 0.25 | 0.45 | 0.65 | 0.85 | 1.15 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Y | 20 | 30 | 40 | 48 | 53 | 60 | 63 | 66 | 68 | 74 | 84 | 98 | 122 |

Relief is composed from four noise terms, with ridge belts confined to the continental
interior so the lowlands stay low:

```
rid01  = clip(rid · 0.5 + 0.5, 0, 1)
belt   = smoothstep(0.48, 0.95, rid01) · smoothstep(0.20, 0.62, cont)
amp    = 18 + 145 · belt
shape  = ridged_fbm(X, Z, octaves=6, scale=1050, seed+51)
relief = amp · shape · land

detail_scale = clip(1.1 − 0.7 · (ero · 0.5 + 0.5), 0.2, 1.2)   # jagged where E < −0.45
rolling      = fbm(X, Z, octaves=4, scale=1150, seed+61) · (4 + 9 · land)
hills        = fbm(X, Z, octaves=4, scale= 420, seed+71) · (3 + 6 · land)
detail       = fbm(X, Z, octaves=4, scale= 120, seed+81) · 2.6

dem = base + (relief + rolling + hills) · detail_scale + detail · land
```

`smoothstep(a, b, t) = u²(3 − 2u)` with `u = clip((t − a)/(b − a), 0, 1)`.

### 5.7 Landmark shaping — `mg/generation/landform.py`

Nine shapers run over the base DEM inside each landmark's bounding box, blended in with the
same feather used by `windows()`. Representative formulas:

* **Ashen Caldera** — volcanic ring plus a sunken crater and a central spire:
  `wall = 146 · exp(−((r − ring_r)/ring_w)²) · wobble`, floor at Y = 40,
  `spire = (92 − 40) · exp(−(r/60)²)` → the Obsidian Throne lands at Y = 92.
* **Solitary Glacial Spine** — alpine cordillera: crests and needle ridges with randomised
  crest width `sigma ∈ [38, 62]` blocks, summits up to 279.
* **Gilded Dunes** — 45° barchan ridges on a terracotta mesa base, with black-glass
  (vitrified) crests where the spec calls for them.
* **Cogwork March** — concentric 9 m quarry benches with a carved river chasm.
* **Byzantine Choir** — stepped terraces; **Hermit's Spire** — isolated needles;
  **Whispering Fen** — a flat sunken basin; **Forgotten Coast** — shingled beach and cliffs;
  **Sunken Reach** — a drowned shelf just under sea level (50–62).
* **Veil of Salt** — everything outside radius 3,550 becomes abyssal.

Shelf dropoff (spec §1) beyond the continental shelf, as a Hermite S-curve:

```
t      = clamp((r − 3300) / 500, 0, 1)
S(t)   = 3t² − 2t³
H_drop = max(−600 · S(t), −32 − 62)
```

The document's literal −600 would pass through the world floor at Y = −64; the curve is
therefore treated as the *shape* of the dropoff and its depth is clamped to the stated abyss
floor (Y = −32), which lands the trench on the Y = −32…10 band the table describes.

### 5.8 Erosion and diffusion — `mg/core/erosion.py`

Preset values for this build: `diffusion 0.1`, `thermal_iterations 6`, `thermal_rate 0.15`,
`fluvial_iterations 6`, `fluvial_k 0.5`, `fluvial_m 0.6`, `fluvial_n 1.0`,
`max_incision 0.6`, `deposition 0.5`, `channel_smoothing 1.2`, `droplets 0`.

* **Thermal (diffusive) erosion** — hillslope creep on the Laplacian, 6 passes at rate 0.15.
* **Stream-power incision** — the detachment-limited law with a hard incision cap:

```
Δh = −k · A^m · S^n        k = 0.5, m = 0.6, n = 1.0,  |Δh| ≤ 0.6 blocks per pass
```

  where `A` is drainage area and `S` is local slope.
* **Channel smoothing** — a Gaussian pass along cells whose accumulation exceeds the river
  threshold, which turns the erosion's blocky channel into a valley floor.
* **Deposition** — sediment is dropped where the channel slope drops below transport capacity.
* `diffusion_pass` smooths more near sea level (wide beaches) and less on cliffs.

### 5.9 Hydrology — `mg/core/hydrology.py`

1. **`priority_flood(dem, epsilon=1e-4)`** — Barnes et al. 2014 priority-flood: processes cells
   in height order and raises every closed depression to its spill point, producing a surface
   with no pits. Runs in O(n log n) with a heap.
2. **`flow_directions`** — D8 steepest descent per cell (returns a direction index, −1 at pits),
   plus `flow_vectors` for a smoothed direction field (the preset uses a D-infinity-style
   vector field for carving).
3. **`flow_accumulation`** — drainage area per cell, computed by walking cells in downstream
   order so each cell is visited once.
4. **`fill_lakes`** — lakes exist only where the flooded surface stands above the filled DEM,
   with `lake_min_depth 1.0`, `lake_min_cells 4`, `max_lakes 200`.
5. **River carve** — cells with accumulation ≥ `river_threshold 900` (biased by climate),
   carved over 3 passes with `valley_depth 1.25`, `bank_flare 2.0`, floodplain width 0.3.
6. **Waterfalls** — steps where the bed drops ≥ 3.5 blocks across a cell.

### 5.10 The river algorithm — `mg/generation/rivers.py`

The hydrology above answers *where the water goes* and *how much there is*. The river module
answers the remaining question — **what shape the river itself should be, from headwater to
sea** — with three geomorphic models, all pure numpy on ordered polylines.

**(a) Graded longitudinal profile (Flint's law).** For a channel ordered source → mouth, with
discharge `Q(s)`, spacing `ds`, and total fall `Δz = z[0] − z[−1]`:

```
w_i        = Q_i^(−θ),      θ = 0.45            # concavity exponent (0.4–0.6 in nature)
seg_weight = ½ (w_i + w_{i+1})
slope_i    = Δz · seg_weight / Σ (seg_weight · ds)
graded     = walk upstream from the mouth:
             graded[i] = graded[i+1] + slope_i · ds_i
blend      = clip((Q − 90) / 90, 0, 1) · 0.85   # only large rivers grade their bed
z_new      = z · (1 − blend) + graded · blend
```

The profile keeps the total fall — the mouth stays at base level — and redistributes it
concave-up: steep torrents at the head, nearly flat water at the mouth. Headwater streams
(below discharge 90) keep the terrain's own steps, which is where waterfalls live.

**(b) Knickpoints and meanders.**

```
smooth polyline (Laplacian, endpoints fixed — source and mouth never move)
κ            = (x′z″ − z′x″) / (x′² + z′²)^{3/2}        # signed curvature, 1/blocks
knickpoint   = slope > 2.2 · graded_slope  and  drop > 3 blocks
migration    = upstream at a rate ∝ discharge (0.35)
turn         = −κ · magnitude                            # erode the outer bank
slowness     = 1 / (1 + 14 · slope)                      # only slow, large lowlands migrate
displacement = 0.45 · bankfull width, per pass, 6 passes
cutoff       = when the channel comes within 1.6 · width of itself → oxbow lake
```

Bend wavelength is seeded at the physically correct 10–14 channel widths (Leopold & Wolman)
and curvature then drives lateral displacement weighted by width and discharge. Where a river
with `Q ≥ 800` reaches the sea it splits into 3 distributaries spreading over a 90-block
reach, i.e. a delta.

**(c) Hydraulic geometry.** Width and depth follow the network's discharge hierarchy:
`W ∝ Q^0.5`, `D ∝ Q^0.4`, with a bank flare so the channel blends into the hillslope.

### 5.11 Keeping the shaped landforms dry

A quarry bench, a dune swale, a terrace and a caldera rim all enclose closed sub-basins, so a
naive depression filler floods them. Two masks prevent that:

```
DRY_KINDS = (caldera, dunes, quarry, terraces, coast)
no_lake   = the cells of those regions      → no standing water, but rivers still cross
no_water  = caldera interior | lava sheets  → absolutely no water
```

Rule inside `water.py`: a lake with **≥ 50 %** of its cells inside the mask is dropped
entirely; a lake that merely laps over the border keeps its valid part and has the overlap
trimmed away. A final invariant pass re-checks `water_mask == 2` cells against the mask after
every other step, so nothing downstream can reintroduce a pond. (Without this rule, a build
filled 39 % of the caldera, 25 % of the quarry and 15 % of the dunes with lakes.)

### 5.12 Landmark re-pinning

Erosion and river carving move the ground a few blocks, but the specification gives an exact
elevation for every landmark centre. After hydrology, each centre is re-pinned:

```
radius = 220 blocks, strength = 1.0, max_delta = 6.0 blocks
skips: the caldera, lakes, open sea
if a river crosses the pin, its surface is lifted together with its bed
```

Measured on a full-resolution build: all eight ground-level centres land within **0.25 blocks**
of the specification. The caldera is exempt by design — its centre is the Obsidian Throne, at
Y = 92, not ground.

### 5.13 Biomes, fertility, population

`biomes.py` classifies from `(temperature, humidity, height, slope)` with preset bands
(highland 40, alpine 90, peak 160, beach height 4, snowline base 190, swamp max slope 0.18,
shallow depth 14, deep depth 50, …). `population.py` produces the Still Life-style density
fields: tree, shrub, flower and boulder densities, ore veins (90 veins, radius 3.2), POIs and
roads. In this export those fields feed the **populate mask**, not placed blocks.

### 5.14 Per-block columns and surface — `mg/generation/surface.py`

Each chunk is 16 × 16 columns × 384 blocks (`y −64…320` = 24 sections):

1. Interpolate the macro fields (height, temperature, humidity, fertility, water surface, water
   mask) to the 16 × 16 block positions.
2. Derive biome **per block**, not per cell, so forest edges have no cell-sized staircase;
   spec landmarks override the classifier outright.
3. Add per-block detail noise to the height and compute the slope.
4. Fill the column: stone above the deepslate boundary, deepslate below Y = 0 with a
   noise-softened transition, bedrock at the floor, air above ground.
5. Apply the §3.4 slope-aware surface rule; snow line =
   `190 + value_noise(x/60, z/60, seed + 6001) · 6.0`.
6. Carve caves, flood the water cells (`water_mask > 0` only), and add the snow layer.

---

## 6. Export format

`export_world()` writes a Minecraft Java 1.21.1 save:

```
level.dat              gzipped NBT — LevelName, RandomSeed, spawn, GameType 1,
                       DataVersion 3953, WorldGenSettings { seed, generate_features },
                       dimensions { minecraft:overworld → noise generator }
level.dat_old          a second copy, kept in step by the decoration tool
session.lock           8 zero bytes
mapgen.json            the exact configuration, so the world regenerates bit-for-bit
README.txt             what is in the save and how it was made
region/r.X.Z.mca       Anvil region files (8 KiB header, 4 KiB sectors)
maps/*.png             height, biome, climate, water, flow, population, soil
worldpainter/          heightmap, masks, surface table, setup script
```

**Chunk NBT** (1.18+ layout): `DataVersion`, `xPos`, `zPos`, `Status`, `LastUpdate`,
`InhabitedTime`, and `sections[24] → { Y, block_states { palette, data }, biomes { palette,
data } }`. Palette indices are bit-packed at `bits = max(4, ceil(log2(palette_size)))` with
`entries_per_word = 64 / bits` indices per 64-bit word.

**The decoration contract** (spec §5 method 1):

| Mode | Chunk `Status` | `generate_features` | Who decorates |
| --- | --- | --- | --- |
| `mods` (this build) | `minecraft:features` | **0** | Lithosphere / Still Life only |
| `engine` | `minecraft:full` | 1 | baked in-engine |

`export.generate_features` overrides either. Getting this wrong is subtle: chunks left at
`minecraft:features` with `generate_features = 1` would let **vanilla** decorate exactly the
chunks that method 1 reserves for the mods.

**Resume.** `export.resume = true` skips chunks already present in their region file, so an
interrupted export continues where it stopped. Two rules keep it safe:

* a writer that opens an existing region file must first *adopt* the chunks already on disk,
  otherwise the file is rewritten down to only the chunks regenerated in that run;
* resume keys on chunk **presence**, not content — never change generation or surface code
  while an export is running or between resumes, or the world becomes a mix of two versions.

---

## 7. Distribution: turning a save into a download

A full-resolution world is far too large for a single GitHub file. The hard numbers:

| Quantity | Measured |
| --- | --- |
| Chunks | 250,000 (250 region files of 1,024 chunks) |
| Region file, full | 2 header sectors + 1 sector per chunk ≈ **4.2 MB** each |
| World on disk | ≈ **1.05 GB** in 250 region files |
| Compressed world | 1,247 bytes/chunk measured on a 4,096-chunk sample at maximum compression → **≈ 312 MB** as ZIP |
| GitHub repository file cap | 100 MB — a single-file world is impossible |
| Release assets | allowed up to 2 GB, but the upload endpoint may be unreachable from a build host |

`tools/pack_world_download.py` handles both routes:

```bash
# one-shot split of a finished world into four shards of ≈ 78 MB
python3 tools/pack_world_download.py <save>/Ashenfall \
        --out release/ashenfall-world --parts 4 --version v1.1.0

# follow a live export: zip each finished region file, push a shard when one fills up
python3 tools/pack_world_download.py <save>/Ashenfall \
        --out release/ashenfall-world --version v1.1.0 \
        --watch --push --interval 90 --min-raw-mb 40 --max-mb 90 --expect-regions 250
```

Design rules the tool implements:

* shards contain **whole region files** — regions never straddle two shards, so the player can
  extract them in any order into one folder;
* a region file counts as finished only when all 1,024 of its chunk slots are filled, so a
  shard is always a valid piece of the world even while an export runs;
* shard numbering continues after the shards already on disk, and an existing shard is never
  overwritten, so re-running the publisher cannot clobber published data;
* `SHA256SUMS.txt` is written beside the shards;
* the metadata (level.dat, masks, `mapgen.json`, README) travels in its own shard, written
  once the export reports itself complete;
* `--restore` unpacks the shards back into the save folder, which is how a wiped working
  directory rebuilds an interrupted build without regenerating a single chunk;
* `--push` commits and pushes each shard, and only reports success after the push really
  succeeded.

Player-facing install instructions live in `release/ashenfall-world/README.md`: download all
shards → extract them all into `.minecraft/saves/Ashenfall` → install Lithosphere and Still
Life before the first load → open the world.

Release text is templated in `release/ashenfall-world/RELEASE_NOTES.md` and filled from the
shards that actually exist:

```bash
python3 tools/fill_release_notes.py --tag v1.1.0 --repo <owner>/<repo> --branch <branch> \
        --verify "<verifier output>" --tests "82 tests OK" --out out/release_notes_v1.1.0.md
```

---

## 8. Sizing and performance expectations

| `--cell` | Cells | Generation | Memory | Use |
| --- | --- | --- | --- | --- |
| 4 | 2,000² = 4,000,000 | ≈ 11 min | ≈ 2.5 GB | the shipped world |
| 8 | 1,000² | 149 s | 0.63 GB | the cheap landmark check before a full run |
| 16 | 500² | ≈ 40 s | — | preview UI default |
| 32 | 250² | ≈ 9 s | — | sketching |

Export throughput: **30–35 chunks/s** with maximum compression on a 2-core machine while
generation runs alongside — about **2 hours** for 250,000 chunks. Generation and export are
both single-process; the export is dominated by per-chunk terrain sampling (~60 %), NBT
building, and zlib compression. Two cores are enough to generate and export simultaneously
without exhaustion.

Working set: the 4,000,000-cell fields are ~30 float64 arrays (~1 GB), peak RSS ≈ 2.5 GB during
generation, dropping to ~1.4 GB during export.

---

## 9. Running it

### 9.1 Environment

```bash
pip install --break-system-packages numpy scipy pillow pypdf     # Linux, PEP 668 systems
python3 -m unittest discover -s tests                            # 82 tests, ≈ 25 s
```

Python 3.11+, numpy 2.x, scipy, pillow. The .exe build needs Windows: `tools/build_exe.py
--check` reports whether the local interpreter can be bundled (it needs a shared libpython,
which static-only interpreters lack).

### 9.2 Build the world

```bash
# the specification's own command — WorldPainter asset set
python generate_ashfall.py --res 2048 --out worldpainter

# the full Minecraft world (the long one)
python3 generate_ashfall.py --res 2048 --out anvil --cell 4 --compression 9 \
        --dir out/ashenfall --progress-log 45

# both, one pass
python3 generate_ashfall.py --out both --cell 4 --dir out
```

Flags: `--res` heightmap resolution (2048 is the spec's), `--size` canvas size in blocks
(override for probes), `--cell` simulation cell size, `--compression` zlib level for region
files (9 = smallest download), `--progress-log SECONDS` timestamped progress lines instead of
a carriage-return bar (readable in a redirected log), `--quiet`.

Because the export takes hours, run it under a restart loop so an interruption resumes rather
than restarts:

```bash
for i in $(seq 1 200); do
  python3 generate_ashfall.py --res 2048 --out anvil --cell 4 --compression 9 \
          --dir out/ashenfall --progress-log 45 >> out/world_export.log 2>&1
  rc=$?
  [ $rc -eq 0 ] && { echo "=== BUILD COMPLETE $(date -Is)" >> out/world_export.log; break; }
  sleep 20
done
```

A silent log is normal: progress lines appear at stage boundaries, at most once every 45 s.

### 9.3 Preview and API

```bash
python3 -u -m mg.server.app --no-browser --host 0.0.0.0 --port 8000
```

| Endpoint | Purpose |
| --- | --- |
| `GET /api/config`, `GET /api/preset/<id>` | configuration and named presets (includes `ashenfall`) |
| `POST /api/generate` | start a generation job (202 + job id) |
| `GET /api/job/<id>`, `POST /api/cancel/<id>` | job status and cancellation |
| `GET /api/mesh` | 3-D preview payload for the last generation |
| `GET /api/maps/<kind>.png` | rendered raster (height, biome, climate, water, flow, …) |
| `POST /api/export` | write a world; body needs a top-level `output_dir` plus `options` |
| `GET /api/inspect` | column inspector at a world coordinate |

### 9.4 Verify an exported world

```bash
python3 -m mg.tools.verify_ashenfall_world out/ashenfall/Ashenfall --pad 0.25
```

The verifier opens the `.mca` files, decodes the chunks and compares each landmark's top block
and elevation with the specification — it never consults the generator's own state. Exit code
0 means the save on disk really holds the specified continent. `--pad` is the fraction of each
elevation band allowed as tolerance.

### 9.5 Decoration flag

```bash
python3 -m mg.tools.set_world_decoration <world-dir> --mods      # generate_features = 0
python3 -m mg.tools.set_world_decoration <world-dir> --vanilla   # generate_features = 1
python3 -m mg.tools.set_world_decoration <world-dir> --mods --dry-run
```

The tool patches both `level.dat` and `level.dat_old`, parses the result back before writing,
and refuses a file without the key — so a failed patch leaves the save untouched.

### 9.6 Recover an interrupted build

```bash
# rebuild the save folder from published shards, then simply re-run the export:
python3 tools/pack_world_download.py <save>/Ashenfall \
        --out release/ashenfall-world --restore
```

The export skips every chunk already present, so recovery costs only the chunks that were never
published.

### 9.7 Release

```bash
git tag -a v1.1.0 -m "v1.1.0 — Ashenfall: the continent of Vantyra, playable world"
git push origin v1.1.0
gh release create v1.1.0 --title "..." --notes-file out/release_notes_v1.1.0.md
```

If the release-asset upload endpoint is reachable, the shards can be attached with `--attach`;
if not, committing them to the repository (as `release/ashenfall-world/`) is the fallback route
and the tag's source archive then carries them too. Never leave `--attach` in a command that
fails: a release whose asset upload fails can be rolled back entirely.

---

## 10. Verification and tests

```bash
python3 -m unittest discover -s tests        # 82 tests
```

The tests that matter most for a build this size:

| Test | Guards against |
| --- | --- |
| `TestResumeExport` | a resumed export truncating a region file down to its newly generated chunks |
| `TestRegionFlushBatching` | a region file being rewritten once per chunk instead of per batch (≈ 2× export time) |
| `TestDecorationFlag` | `mods` → `generate_features 0`, `engine` → 1, the override, and the patch tool's behaviour on both level files |
| `TestNoPonding` | standing water inside a dry landform; `no_water` being absolute |
| `test_pack.py` (7 tests) | a shard published from a half-written region, a shard name reused, shards with gaps or duplicates, a shard over the size budget |
| Export/NBT tests | palette bit-packing, chunk round-trip, `level.dat` readability, schematic and bundle outputs |

End-to-end acceptance is §9.4: the verifier against a freshly exported world.

---

## 11. Key results to expect

From a full build at cell size 4, seed 20250929:

* **Landmark centres**: 8 of 9 within 0.25 blocks of the specification; the caldera centre is
  the Obsidian Throne at Y = 92 (as specified) rather than ground.
* **Water**: zero standing water on the shaped landforms (caldera, quarry, dunes, terraces,
  coast) with rivers preserved — for example the Cogwork March keeps 1.03 % river cover.
  Map-wide at cell 8: 48,575 river cells, 22,071 lake cells across 156 lakes, 521,730 ocean
  cells.
* **Terrain**: heightmap Y from roughly −31 to 229 in the WorldPainter product; the mesh
  preview gives ≈ 27.5 k vertices, 54.4 k triangles, 192–328 river paths depending on cell size.
* **Populate mask**: ≈ 27 % of the canvas flagged for decoration.
* **WorldPainter set**: 16-bit heightmap, 8 masks, biome map, surface table, setup script,
  ≈ 8 MB total, ≈ 4.1 MB as a ZIP.

---

## 12. Failure modes and design constraints

These are the traps inherent in this design; each is either covered by a test or enforced by an
invariant in the code.

1. **Never infer water from height.** Only cells with `water_mask > 0` get water placed; height
   alone cannot tell a lake from a hollow. The dry-landform masks exist because of this, and
   the invariant pass at the end of `water.py` re-checks them.
2. **Flush region files on unwritten chunks, not on the file total.** Counting adopted chunks
   as pending makes every chunk after the first batch rewrite a 1 MB file — roughly double the
   export time on 250,000 chunks.
3. **Adopt on resume.** A writer opening an existing region file must read its chunks back
   first, or the file is truncated to only the newly written chunks.
4. **`generate_features` must match the chunk status.** `minecraft:features` + `1` hands the
   chunks to vanilla, which method 1 forbids.
5. **Never edit generation or surface code mid-export.** Resume keys on chunk presence, so the
   result silently mixes two versions of the terrain.
6. **A quantised landmark step can create a bogus basin**, which the lake filler then floods —
   the reason landmarks are re-pinned *after* hydrology and why the dry-landform masks are
   checked after every water step.
7. **`smooth_max(a, 0, k) = √k / 2 ≠ 0`.** Smooth maximum blends do not vanish at zero; using
   one where exact zero matters leaves a residue.
8. **Slicing source text is unsafe.** Patch with `s.replace(old, new)` guarded by
   `assert old in s`; a slice assignment has already destroyed a source file in this codebase's
   history.
9. **Static-only interpreters cannot be bundled** into an .exe (`--check` reports this); build
   the Windows bundle on Windows.
10. **A failed release-asset upload can roll the release back.** Verify the upload endpoint
    before relying on `--attach`; the repository-shard route always works.
11. **Long jobs must be backgrounded** with output to a log. A silent stage is normal — a
    4,000,000-cell generation reports only at stage boundaries.
12. **`.gitignore` the outputs.** `out/` holds gigabytes of generated data; only the shards
    that are meant to be distributed belong in the repository, and they are the one exception
    because they *are* the download.

---

## 13. Where to take it next

1. **Finish and ship the full world.** Run §9.2 until `BUILD COMPLETE`, verify with §9.4, tag
   `v1.1.0` per §9.7, and publish the shards (§7). Everything else in this document exists to
   make that reproducible.
2. **Placed structures and features** (criterion 10): temples, ruins and the giant fungal
   heartwood of the Whispering Fen exist in the surface table and the material palette but are
   not yet placed as blocks. The population module is the seam.
3. **Cirque tarns** on the Glacial Spine and near the Hermit's Spire are deliberately left as
   pondable ground; enabling them is a water-mask edit, not a terrain change.
4. **Generalisation**: the preset system plus the generic terrain chain
   (`mg/generation/terrain.py`) is the seam for using the same software as an
   any-purpose map generator; the continent engine is the reference example of a fully
   specified world.

---

## 14. Provenance and design ancestry

* **Engine concept**: World Machine / World Painter / terrain-diffusion logic fused with
  Lithosphere-style temperature and density functions, Still Life-style population and
  Streams-Reflowing-style water and rivers — all reimplemented from their published behaviour,
  with no mod code decompiled or ported.
* **This world**: `ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf`, vendored as
  `docs/specs/ashenfall_spec.txt`.
* **Climate and continents** follow Lithosphere's approach: continents and oceans with gradual
  coastlines and wide beaches, smooth height transitions, large climate regions so biomes are
  big and not condensed, and rivers that are rarer but carve deep valleys.
* **Population** follows Still Life: biome variants and transitional biomes, higher density of
  trees, shrubs and flowers without new blocks, and decoration driven by a mask rather than
  baked pieces.
* **Water** follows Streams Reflowing: bounded, height-aware river paths, streams that end at
  sea or lakes, and rivers that never drain into caves.
* **Rivers** are this project's own algorithm: Flint's concave-up graded profile, knickpoint
  migration, curvature-driven meanders with cutoffs, and distributary deltas.
