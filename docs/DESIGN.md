# Design

How Vantraya Builder turns the Ashenfall specification into a live Minecraft world generator, what was ported from the offline generator, what was replaced, and how each claim is checked.

Source documents: [`HANDOFF.md`](../HANDOFF.md) (the build handoff), `ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf` (the master spec), and — for the numbers the handoff only summarises — the offline Python generator that the handoff describes (`mg/generation/landmarks.py`, `landform.py`, `ashenfall.py`, `surface.py`, from the repository's earlier generator branch). The handoff's own rule applies: the specification's tables are the authority, noise only supplies texture.

## 1. The idea

The handoff describes an *offline* pipeline: a Python program computes 4,000,000 cells, runs erosion and hydrology over all of them, writes 250,000 Anvil chunks and a populate mask, and ships a 300 MB download. This mod does the opposite: **the specification is evaluated on demand, per column, while Minecraft generates a chunk**. Nothing is exported.

That only works if every step of the pipeline can be a *local* function of `(seed, x, z)`. Most of it already is — the continent is an analytic construction (a bearing-dependent coast radius, splines, `windows()` blends, landmark shapers) — and where a step was global (percentile normalisation, distance transforms, flow accumulation, erosion), it has a local replacement (§5).

```
world creation screen ──► world preset "vantraya_builder:vantraya"  (JSON, listed in #minecraft:normal)
                              │
        ┌─────────────────────┼───────────────────────────────┐
        ▼                     ▼                               ▼
 dimension type         noise settings                  chunk generator + biome source
 (own copy of the      (vanilla Overworld router with  (VantrayaChunkGenerator extends
  Overworld's)          the terrain swapped for our     NoiseBasedChunkGenerator;
                        height field)                   VantrayaBiomeSource)
                              │                               │
                              ▼                               ▼
                 vantraya_builder:field density functions   BiomeLogic, SurfaceLogic
                              │                               │
                              └──────────────┬────────────────┘
                                             ▼
                                   VantrayaModel (pure Java)
                       the specification: Spec · Regions · Landforms · Instances · Tables · Noise
```

The mod is two layers:

* **`core`** — the specification as pure Java with no Minecraft import. `VantrayaModel.sample(x, z)` returns every field of a column (continentalness, erosion, ridges, temperature, humidity, height, landmark, lava / no-water masks, river and lake strength …). It is what the tests and the offline cross-check run.
* **`mc`** — a thin adapter: one density function type, one biome source, one chunk generator, a surface painter, commands. It contains no specification numbers.

## 2. Specification → code

| Handoff | What | Code | Checked by |
|---|---|---|---|
| §3.1 | canvas 8,000², Y −64…320, sea 62, 16-bit encoding, named elevations | `Spec` | `SpecTest` (numbers typed independently from the PDF) |
| §3.2 | nine landmarks: centre, box, band, biomes, density and climate windows; the Veil | `Spec.LANDMARKS`, `Spec.VEIL` | `SpecTest`; `ModelTest` (ownership, pins); `/vantraya verify` |
| §3.3 | five climate tiers, lapse `0.0055/block` | `Spec.climateTier`, `effectiveTemperature` | `SpecTest`, `ModelTest.climateTiersFollowTheLandmarks` |
| §3.4 | slope-aware surface table | `SurfaceLogic` + `SurfacePainter` | `SurfaceLogicTest` (every row), blue-noise density tests |
| §3.5 | population left to the mods | surface soil rules + biome roles (§7) | — (by construction) |
| §5.1 | generic climate | `VantrayaModel.genericTempUnit`, `genericHumUnit` | `ParityTest.climateMatches` |
| §5.2 | continentalness: warp, coast radius by bearing, `COAST_SPLINE`, inland bonus | `VantrayaModel.rawContinentalness`, `Tables` | `ParityTest` (exact outside landmarks) |
| §5.3 | `windows()` | `VantrayaModel.windows`, `Regions.boxFeather` | `ParityTest` |
| §5.4 | erosion and ridges | `VantrayaModel.compute` | `ParityTest` |
| §5.6 | base height and relief | `VantrayaModel.compute`, `Tables.BASE_SPLINE` | `ParityTest.heightFieldMatchesTheOfflineDem` |
| §5.7 | nine landmark shapers, shelf dropoff, Veil | `Landforms`, `Instances`, `Spec.shelfDrop` | `ParityTest`, `ModelTest.veilOfSaltIsAbyssal`, `shelfDropsAwayBeyondTheContinent` |
| §5.8–5.10 | erosion, hydrology, rivers | *replaced* (§5) | `ModelTest` water invariants |
| §5.11 | dry landforms, no-water caldera | `VantrayaModel` masks, `CalderaFluids` | `ModelTest.calderaIsNeverWet…`, `closedBasinLandforms…` |
| §5.12 | landmark re-pinning | pointwise pull + water keep-out | `ModelTest.everyLandmarkCentreLandsOnItsSpecifiedElevation` |
| §5.14 | per-block surface, biomes override | `BiomeLogic`, `SurfacePainter` | `BiomeLogicTest`, `SurfaceLogicTest` |
| §9.4 | verify the finished world against the table | `SpecVerifier`, `/vantraya verify` | `InstancesAndVerifierTest` (and a flat world must fail) |

### Acceptance criteria (§1.1)

| # | Criterion | Here |
|---|---|---|
| 1 | 8,000² canvas, Y −64…320, sea 62 | ✓ model and dimension type; beyond ±4000 the Veil's abyss continues (optional world border) |
| 2 | nine landmarks, exact centres and bands | ✓ every ground-level centre exact for every seed tested |
| 3 | Veil of Salt, shelf dropoff | ✓ |
| 4 | five climate tiers, per-region windows | ✓ — plus the spec's 600-block boreal belt (§5 below) |
| 5 | rivers and lakes from the project's own algorithm | ✓ own algorithm, local variant (§5) |
| 6 | slope-aware surface table | ✓ |
| 7 | export as a Java save, undecorated | not applicable — live generation; decoration is the biomes' own features |
| 8 | preview before the full run | not needed at runtime; `RenderMaps` renders the model (see `docs/img`) |
| 9 | 250,000 chunks on disk | not applicable — chunks are generated when explored |
| 10 | structures and giant fungal trees | out of scope, as in the handoff — left to the mods |

## 3. The model, step by step

All noise is the offline engine's own hash-based gradient noise, ported bit-for-bit (a test pins values produced by the Python code). For a column `(x, z)`:

1. **Continentalness.** Domain-warp (scale 2100, strength 900, 4 octaves); coast radius `R_coast(bearing)` from the 12-point bearing table plus two fBm terms; `Δ = |warped| − R_coast`; `C = COAST_SPLINE(Δ)` plus an inland bonus and a little detail; clip ±1.2; then `windows()` at weight 0.60.
2. **Erosion and ridges.** fBm stretched to −1…1, then `windows()` at 0.70. Rivers are tied to the ridges field the way the spec words it: valleys where `R ≈ 0`.
3. **Climate.** Generic temperature (latitude band + regional noise) and humidity, converted to spec units, eased toward the boreal belt around the Spine, blended into every landmark's temperature/humidity window at weight 0.85, the Veil clamped cold and wet.
4. **Relief.** `base = BASE_SPLINE(C)`; ridge belts confined to the continental interior; ridged fBm, rolling hills, hills, detail; `detail_scale` from erosion (jagged where E < −0.45): `dem = base + (relief + rolling + hills)·detail_scale + detail·land`.
5. **Shelf and Veil.** Radial blend toward the Hermite dropoff beyond r = 3300 and the flat abyss floor beyond 3550.
6. **Landmarks.** Each landmark's target-elevation field (caldera ring + crater + throne, quarry benches, cordillera instances, dunes, fen, drowned shelf, needles, terraces, coast) is blended in with a domain-warped feather of 200 blocks, in spec order, then its centre is pinned (radius 150, or 260 for the dunes and the cordillera).
7. **Dry masks.** The caldera interior and its lava floor are "never wet"; coast, quarry, dunes, terraces and caldera "may not pond".
8. **Rivers and lakes** (§5), kept out of every pin zone.
9. **Re-pin.** The handoff's radius-220, ≤ 6-block pull toward each landmark's elevation, applied pointwise.
10. **Climate on the finished surface.** The lapse rate lowers temperature with altitude; the boreal floor keeps the belt non-snowy; humidity combines the landmark window with the moisture budget.
11. **`rough3d`.** How much 3D noise the density function may add to the surface: more on ridges and mountains, none at a pinned centre — which is why the pins are *exact*.

Sampling costs about 6 µs per column and is thread-safe (stateless noise, immutable per-seed state, a small per-thread cache so the density-function channels evaluated at one column cost one evaluation).

## 4. How Minecraft is made to build it

* **Terrain.** The noise settings are vanilla's Overworld settings with one idea changed. Vanilla's `sloped_cheese` is `4·quarter_negative(depth·factor) + 3D noise` with `depth = Y-gradient + offset`; here it is `4·factor·depth + rough3d·3D noise` with `depth = (H + 0.5 − y)/128` — the same scale as vanilla's depth (1/128 per block), so biome parameters, aquifers and the cave thresholds keep their meaning — with a constant `factor` and the 3D noise multiplied by `rough3d`, which the model sets to 0 around every landmark centre. **There is deliberately no `quarter_negative` bend at the surface.** The engine does not evaluate the density at every block: it evaluates it at the corners of 4 × 4 × 8-block cells and interpolates linearly between them. Vanilla's bend makes the density four times flatter above the ground than below it, and a linear interpolation across a bend crosses zero too high — by `24·f·(1−f)/(1+3f)` blocks, where *f* is how far up the cell the surface lies: 2.6 at most. The first in-engine run measured exactly that (the specified 68 / 85 / 220 / 140 / 75 came out as 70 / 87 / 222 / 142 / 78; the formula predicted all five to the block). A straight line is interpolated exactly, so the surface sits at `round(H)`; above the ground the line is steeper than vanilla's, so the 3D noise lifts the surface by less, not more. Everything below the surface — and so the cave thresholds — is unchanged. Because the height field is read on the same 4-block grid (`flat_cache`, then interpolated), features narrower than about eight blocks are smoothed; all nine landmark centres are multiples of 4, so a centre column is evaluated exactly. The 3D noise is a private copy of vanilla's (`vantraya_builder:base_3d_noise`), so a datapack that overrides `minecraft:overworld/base_3d_noise` cannot change the roughness and move the pinned elevations. Caves, entrances, noodle and spaghetti caves, pillars, aquifers, ore veins and the bedrock/deepslate surface rule are vanilla's; the cave functions are referenced *by vanilla's ids* on purpose, so a cave overhaul that overrides them applies here as well. One exception, found by the in-engine run: a cave entrance opened a 17-block pit in the Hermit's Spire's pinned top (vanilla's `min(sloped_cheese, 5·entrances)` carves from the surface down to the depth where `sloped_cheese` reaches 1.5625, about 17 blocks), and the Forgotten Coast's centre is where a new world spawns. So within 64 blocks of every landmark centre (`Spec.PROTECT_RADIUS`, the `protect` channel) the entrance and noodle terms are lifted by `1000·protect` and cannot open the surface there; caves underground are untouched.
* **Taller peaks.** Vanilla fades terrain out between Y = 240 and 256. The Glacial Spine reaches 283, so the fade moves to 296–312.
* **Sea level.** `sea_level = 63` fills open air through Y = 62, which is what the exported world's waterline was; the model's `SEA_LEVEL = 62` is the height-field constant.
* **Lattice sampling.** The fields are wrapped in `flat_cache`, so they are evaluated on Minecraft's 4-block lattice and interpolated. All nine landmark centres are multiples of 4, so their pins are hit exactly.
* **Which world is this?** A density function is never told the world seed. Each Vantraya function therefore carries a seeded noise (`vantraya_builder:seed_probe`) and the model's seed is a fingerprint of it (§ `WorldSeeds`); the generator reads the very same noise instance from `RandomState`, so both sides always agree, with no global state. (Consequence: typing seed `20250929` does not select the canonical continent; `canonicalWorld = true` does.)
* **Post-passes** (the things a density function cannot express), in `VantrayaChunkGenerator`: after the noise fill, open water is drained from the caldera and `RiverWater` fills the river and lake channels to the water line the model chose (ice where the climate is cold); after the biome surface rules, `SurfacePainter` applies the slope table and `CalderaFluids` pours the lava basins. All use only public API.
* **Structures.** Vanilla gates structures on global biome tags, and the specification's caldera is *basalt deltas* - an `#minecraft:is_nether` biome - which is how a nether fortress ended up in a river in the first real game. `createStructures` therefore runs one policy pass over the starts vanilla made (`StructurePolicy`): a structure whose biomes are all Nether biomes is thrown out, and so is one centred in the model's river or lake (or inland water) unless vanilla itself puts it in water. Scoped to this generator: the real Nether and every other world type are untouched. Config: `structurePolicy`.
* **Keeping the world type.** Vanilla merges a world's chosen dimensions with the `dimension/*.json` of the enabled data packs in `WorldDimensions.bake`, and the pack's dimension wins — so a mod that replaces the overworld switches every world type off, which is what the first real game showed (COMPATIBILITY.md §0). This is the one place the mod reaches into Minecraft rather than using public API: `WorldDimensionsMixin` passes `bake` its argument with the pack's `minecraft:overworld` removed, and only when the chosen overworld is Vantraya's (`WorldTypePriority`, which holds the whole decision and is tested directly). It is one `@ModifyVariable` at the head of `bake`, which runs for a new world, for every later load from `level.dat`, and for the dedicated server's `level-type`; the injection is required, so a Minecraft version that changed `bake` fails loudly at start-up, and the mod declares 1.21.1 only. There are no access transformers.
* **Spawn and border** use NeoForge's `LevelEvent.CreateSpawnPosition` (cancelling vanilla's spawn search) and `ServerStartedEvent`.
* **Client**: the optional `preselectWorldType` (off by default) selects the Vantraya entry in the Create New World screen's own state object when the screen opens — public client API only.

## 5. What is different from the offline generator, and why

| Offline stage | Runtime stand-in | Why |
|---|---|---|
| `windows()` normalised by the percentile of the whole raster | fixed bounds: the median over 64 world seeds of each percentile (`Calibration`) | a chunk generator never sees the whole raster; the reference build's own bounds all lie inside the seed-to-seed spread |
| Euclidean distance-transform feathers | closed-form signed distance to the (box − overriding boxes − Veil disc) | exact for a box; same shape |
| NumPy-random placement of the Spine's 31 stations and the needles | `SplittableRandom` with the same distributions; **the canonical seed uses the offline engine's actual tables** | NumPy's PCG64 has no Java counterpart; the tables make the canonical world's mountains identical |
| thermal erosion, stream-power incision, diffusion | not applied | the handoff itself says the landforms "are already the erosion product"; `detail_scale` keeps jagged ground jagged |
| priority-flood, D8 flow, accumulation, graded profiles, meanders, deltas | river valleys + lake basins (below) | flow routing is global; worldgen must be local |
| post-hydrology re-pinning | pointwise pull + water kept out of pin zones | same effect, no second pass |
| moisture advection (26 steps over the raster) | its closed form | the offline run barely moves moisture; measured mean error 0.005 |
| the coast table's two ±180° entries (3350 vs 3150) | both ends become their mean | the wrapped curve otherwise jumped 200 blocks along the west axis |
| (not enforced) | **600-block non-snowy boreal belt** around the Glacial Spine | spec §4 tier 1; the reference world touches temperate plains to the Spine's edge (76 % tier 2 within 100 blocks) |
| populate mask as a PNG | soil kept by the slope table, blue-noise dithered; vegetation from the biomes' own features | the spec's own mechanism #3 (stripped topsoil keeps trees off cliffs), live |
| `generate_features = 0`, chunks left at `features` | normal generation | export-only contract |
| per-block surface from a raster | per-column pass on the live chunk | same table |

**Rivers and lakes** are real drainage: a `Drainage` lattice per world seed runs a priority flood (every closed basin becomes a lake at its spill level) and D8 flow accumulation (a channel starts where its catchment is big enough and grows with it), so channels follow the terrain downhill, join and widen, and end at the sea, a lake, or a declared playa. Each reach has one continuous, downstream-monotone water surface (a fall is at most one rapid per 16-block cell), and the channel is cut as a smooth cross-section under it. The older stand-in - a warped zero set (it meandered, but also wandered over ridges and stopped in mid-slope) - is replaced. Widening toward the coast, strongest where the ridges field is near zero and absent on ridge crests; the dry landforms keep their rivers but never a pond. A channel follows the terrain: its water surface sits 2 blocks below the natural ground of the bed and the floor 3–4 below that, the wide U of the valley slopes into it, and the `RiverWater` pass fills it with real water (ice where the climate is cold) right after the noise fill - so rivers run at altitude too, up to about Y = 150, lakes to Y = 110, and about 10 % of the inland surface carries a channel. (The first version cut to absolute Y = 58 and faded out above Y ≈ 104, which the first play test called "a critical lack of waterways".) What this still does *not* do: it does not obey a drainage hierarchy - the network is a noise, not flow routing - and the water surface steps where the terrain does, so a steep channel reads as rapids.

## 6. Biomes, surface, population

* `BiomeLogic` decides a biome role from what a biome source receives: the climate parameters (our density functions) and `depth`, from which the column's ground height is recovered. Landmarks override the classifier (the first-listed biome low, the second high — `landmark_biome_field`); everywhere else the offline Whittaker matrix runs. Water inland of the shoreline is a river; sea water an ocean chosen by temperature and depth; the Veil alternates deep cold ocean and deep ocean; the caldera is volcanic even where its floor is below sea level; cave biomes use vanilla's thresholds. Specified biomes are used by *role*, each resolved through a tag so other mods' biomes can join (see COMPATIBILITY.md).
* `SurfaceLogic` is the slope table plus the volcanic, dune-crest and Veil rules, as a pure function of slope, height, landmark and a blue-noise value. Slope is measured the way the offline engine measures it: `atan(|dh/dx| + |dh/dz|)` of the height field.
* Population is left to the biomes, per spec §3.5: vanilla and mods place their features by biome; this mod only decides where soil exists.

## 7. Determinism and safety

The same seed always gives the same world; different seeds give different coastlines, relief, rivers and Spine layouts but identical landmarks. Everything shared between threads is immutable or thread-local; `ModelTest.concurrentSamplingGivesTheSameAnswer` samples 20,000 columns in parallel and compares them with a sequential run.

## 8. Verification

* **110 tests** (`./gradlew test`, all passing in CI): 90 plain JUnit tests — `SpecTest` (every table), `NoiseAndSplineTest` (bit-exact noise), `ParityTest` (560-point golden file from the Python engine), `ModelTest` (pins for seven seeds, Veil, shelf, caldera dryness, ponds, rivers, threads, boreal belt), `BiomeLogicTest`, `SurfaceLogicTest`, `BlueNoiseTest`, `InstancesAndVerifierTest`, and `RouterTest`, which evaluates the *shipped* noise-router JSON with a small independent interpreter and checks that the terrain surface sits on the height field, every landmark centre is exact (also with vanilla's cave entrances and noodles forced to their strongest), the Spine clears the old ceiling, bedrock is solid, the sky is air, and the terrain references no vanilla function a terrain overhaul could override — and 20 tests in `engine/InEngineWorldgenTest`, which run **inside the real game engine**.
* **In-engine tests.** They use NeoForge's `unitTest` environment (ModDevGradle) and its `EphemeralTestServerProvider`: an in-memory `MinecraftServer` whose data is loaded by Minecraft's own `WorldLoader` from vanilla's data pack plus this mod's — no dedicated server, no EULA, no world on disk. The interpreter in `RouterTest` evaluates at exact points with noise at 0; the engine interpolates between cell corners, so only the engine can show where that matters, and it did (see §4). The tests take the generator out of the loaded preset and drive it: the specification's verification on eight world seeds, real chunks through `fillFromNoise` at each landmark, the surface pass and the Caldera's drain and lava on those chunks, the biome source against the real `Climate.Sampler`, the codec round trip, and the merge of a world's dimensions with a data pack's own overworld (vanilla's rule as a control; Vantraya keeping its overworld, also after a reload from `level.dat`). They cannot reach a player in a world: the vanilla surface-rule pass (`buildSurface` needs a `WorldGenRegion`), feature decoration, structures, mobs, the creation screen and spawn placement.
* **Cross-check against the Python engine** (done during development; reproducible with the offline generator): over the full canvas at the specification's 4-block resolution — height field mean error 0.04 blocks (p99 1.0, max 3.2); landmark ownership, lava mask and no-water mask 100 % identical; temperature mean error 0.0004; humidity 0.005.
* **In game:** `/vantraya verify` reads the running generator and noise router (the live height, seen through cave mouths: the highest top block of the column and a ring of eight about three blocks around it, since caves only remove ground). Landmark centres cannot be carved by surface cave entrances (above); the crater-floor reading and the Veil ring are not protected, so a cave opening exactly on the crater-floor column can fail that one check, by a few blocks up to about 17 below the band; if that is the only failure, a cave is the likely reason, not the world (`/vantraya tp ashen_caldera`, then look at the crater floor 200 blocks east of the Throne). The Veil's reading sets aside its two lowest of twelve samples for the same reason, and its tolerance is HANDOFF §9.4's own (`--pad 0.25` of the elevation band).

## 9. Changing the specification

The numbers live in `Spec.java` (and `Calibration.java` for percentile bounds); `SpecTest` will tell you which pinned number you changed. After changing climate or landform maths, re-run the Python engine and `ParityTest`'s golden file (`src/test/resources/parity`) if you want the cross-check to keep meaning. After changing anything in the noise router, run `python3 scripts/generate_data.py <vanilla 1.21.1 data dir>` (the router is generated from vanilla's, see the script) and `RouterTest`.
