# Specification notes: conflicts, gaps and the defaults chosen

The handoff's own rule is **"if a specification datapoint is missing or ambiguous, ask — never guess"** (HANDOFF.md, rules). While this mod was built there was no round-trip with its author, so wherever the documents disagree or say nothing, a default was chosen *by a stated rule* and is recorded here instead of being buried in code. **Section A lists the ones that change what you will see; please confirm or correct each.** Every one is a small, local change (named in each entry), and the tests that pin the specification (`SpecTest`) will tell you if you change a pinned number.

## How conflicts were ranked

1. **HANDOFF.md §3** — it says "a build that disagrees with this section is wrong, not creative".
2. **`ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf`** — where the handoff is silent or only summarises.
3. **The offline Python generator** the handoff describes (`mg/generation/…`, from the repository's earlier generator branch) — for numbers the two documents only name, and as the numerical cross-check (`ParityTest`).
4. **A decision recorded on this page.**

`PROGRAMMATIC_LARGE_SCALE_TERRAIN_SYSTEM_SPECIFICATION.md` and its `.pdf` are the same text (the PDF is a rendering of the markdown; compared word by word, they differ only in ligature characters). It is a *generic* blueprint for a 10,000 × 10,000 offline pipeline (World Machine / Gaea → WorldPainter), not Ashenfall's numbers, and the offline pipeline is exactly what this mod was asked *not* to reproduce; it is used only as background for the algorithms (erosion, hydrology), which are replaced by local equivalents (DESIGN.md §5).

## A. Conflicts and gaps where a default was chosen — please confirm

### A1. "40 %" in the 25–35° slope band
* **PDF §5 method 3:** 25° ≤ θ ≤ 35° → *Coarse Dirt, Permadirt, Podzol*; "Vegetation & Feature Population Density: **40 % Density**: Pine Taiga, berry bushes, small stone erratics" (the column is population density).
* **HANDOFF §3.4:** "25–35° → **40 % exposed rock** mixed into the topsoil".
* **Offline Python:** every 25–35° column becomes coarse dirt (the "40 %" appears only in its docstring).
* **Mod:** **40 % of the band's columns keep soil** (coarse dirt, some podzol), spread evenly by blue-noise dithering; the other **60 % are bare stone, gravel or andesite**. This is the PDF's reading (density of what can grow = density of soil), and it matches the progression 100 % → 40 % → 5 % → 0 % down the table. The HANDOFF's wording read literally gives the opposite split (40 % rock, 60 % soil).
* **Why the PDF's reading is kept although HANDOFF outranks the PDF:** HANDOFF's *next* row says "35–45° | **5 % stone**", which cannot literally mean the share of exposed rock (steeper ground has more rock, not less); read as the PDF's *density* column — 100 % → 40 % → 5 % → 0 % — both rows make sense, and the 25–35° row's "exposed rock" wording looks like the same column re-worded as surface. Literally applied, HANDOFF's two rows would give 40 % rock at 30° but only 5 % at 40°. **Still open: please confirm** (you were asked and did not answer).
* **Change:** `SurfaceLogic.DIRT_BAND_DENSITY` (0.40 → 0.60 for the HANDOFF reading). The 35–45° band is likewise "5 % density": 5 % of its columns are mossy-cobble patches, and a scree fraction `clip((θ − 35)/12, 0, 1)` (HANDOFF) of the rest is gravel/cobblestone.

### A2. The −180° / +180° entries of the coast table
* **HANDOFF §5.2:** the bearing table lists θ = −180° → R 3350 **and** θ = 180° → R 3150. Those are the same compass bearing, and the table is "wrapped ±360° so the interpolation is periodic", so the curve jumps 200 blocks along the west axis.
* **Mod:** both ends become their mean (3250), so the coast is continuous. The offline behaviour (seam kept) is `VantrayaModel.Config.parity(…)`, which `ParityTest` runs; the world type uses `Config.production()`.
* **Change:** `seamlessCoast` in `Config.production()` / `Config.canonical()` (`false` = the handoff's literal table). Effect: a 200-block step in the shoreline on the west axis.

### A3. The Ashen Caldera's centre elevation
* **Both documents:** centre `(0, 80, 0)`, Y band 38–150, "volcanic ring wall (Y = 146), sunken crater basin (Y = 40), **Obsidian Throne spire (Y = 92)**". HANDOFF §5.7: `spire = (92 − 40)·exp(−(r/60)²)` → "the Obsidian Throne lands at Y = 92".
* **A spire at the exact centre cannot also be at 80.** The offline engine does not pin the caldera's centre to 80.
* **Mod:** the centre column is the Throne, **Y = 92**; 80 is not a point of the surface. Floor 40, rim 146 as specified. `/vantraya verify` checks the throne, the floor and the rim, not 80.
* **Change:** none needed unless 80 means something else to you (an average? the crater's mid-slope?) — tell me and the landmark can be re-pinned.

### A4. Climate tier boundaries and what "temperature" a tier refers to
* **PDF §4's tier ranges have gaps and overlaps** (Tier 0 ≤ −0.75, Tier 1 −0.60…−0.25 → a gap at −0.75…−0.60; Tier 2 up to 0.40 and Tier 3 from 0.35 → an overlap; 0.60…0.70 belongs to no tier). **HANDOFF §3.3** gives one contiguous partition: ≤ −0.75, ≤ −0.25, ≤ 0.40, < 0.70, ≥ 0.70. **The mod uses the HANDOFF's** (`Spec.climateTier`).
* **Effective vs base temperature.** The PDF defines `T_eff = T_base − 0.0055·max(0, y − 62)`. A tier is assigned to **effective** temperature for the cold regions (the Glacial Spine reaches tier 0 *because* of the lapse at altitude, and the Hermit's Spire's summits go boreal/glacial), but to **base** temperature for the hot ones: the caldera's rim at Y ≈ 146 would lose 0.46 to the lapse and drop out of tier 4 ("arid & volcanic"), so the caldera and the dunes are tier 4 by their base temperature. (`ModelTest.climateTiersFollowTheLandmarks` pins both.)

### A5. The mandatory 600-block boreal belt (added)
* **PDF §4, tier 1:** "Mandatory 600-block non-snowy pine taiga buffer. Prevents snow from touching temperate plains." HANDOFF §3.3 lists the tier but states no width, and **the offline engine never enforced it** (on the reference build, 76 % of the ground within 100 blocks of the Spine's edge is already temperate-tier plains).
* **Mod:** enforced. Around the Spine's box the temperature is eased to −0.58 at the box edge and −0.40 at 600 blocks out (both inside tier 1's −0.60…−0.25), then released back to the regional climate over the next 300 blocks; the lapse-adjusted temperature may not fall below −0.62 inside the belt, so foothills are taiga, not snowy taiga (`VantrayaModel.borealBuffer`, `borealFloor`; `ModelTest.snowNeverTouchesTemperateTerrainWithoutABorealBelt`). Landmarks with their own windows (Hermit's Spire, Byzantine Choir) still win inside their boxes. Consequence: the continent differs from the offline build around the Spine's edge by design.
* **Change:** `Config.borealBuffer` (`false` = the offline engine's behaviour).

### A6. Which biomes the Veil of Salt uses
* **Both documents:** the Veil's biomes are `deep_cold_ocean` **and** `deep_ocean`. **Offline engine:** only `deep_cold_ocean` appears in the export.
* **Mod:** both occur, alternating in broad patches by the erosion field (`BiomeLogic`, "the Veil alternates deep cold ocean and deep ocean"). Role tags (COMPATIBILITY.md) decide the actual biome of each role.
* **Change:** one line in `BiomeLogic.surfaceRole`.

### A7. Dune crests: "vitrified black-glass"
* **Documents:** "vitrified black-glass dunes" (PDF §2), "black-glass (vitrified) crests where the spec calls for them" (HANDOFF).
* **Mod:** crests above Y = 89 are **black glazed terracotta**, as in the offline engine — a literal glass block would be see-through and let the sand beneath show through the crest.
* **Change:** the `DUNES` crest line at the end of `SurfaceLogic.choose` (e.g. `BLACK_STAINED_GLASS` would need a new `Mat`).

### A8. Sheer cliffs (> 45°): "Solid Bedrock, Granite, Basalt"
* **PDF:** the surface of cliffs above 45° is *Solid Bedrock, Granite, Basalt*. **Offline engine:** granite on top (basalt in the caldera's biome), **bedrock as filler where the slope exceeds 60°**, stone otherwise.
* **Mod:** granite/basalt on top, **stone** below. **No bedrock is placed on cliff faces** — it cannot be mined or moved in survival, and HANDOFF §3.4 states the row simply as "bare rock, no soil", which granite/basalt over stone already is. The row's real job (0 % population: no soil, so no trees) is met either way.
* **Change:** the `deg > SLOPE_SCREE_MAX` branch of `SurfaceLogic.choose` (`filler = BEDROCK` where `deg > 60`).

### A9. Sea level: 62 or 63?
* **Documents:** "Sea level Y = 62" everywhere (the exported world's water line).
* **Minecraft:** in `noise_settings`, `sea_level = 63` fills open air **up to and including Y = 62**, which *is* a water line at 62 in the sense WorldPainter and the exported world used. The height field's constant is 62 (`Spec.SEA_LEVEL`); the noise settings say 63. Anything that reads the generator's sea level (the aquifer system, some mods' placement rules) therefore sees 63.
* **Change:** `sea_level` in the noise settings (generated by `scripts/generate_data.py`, which asserts the vanilla value, so changing it is a deliberate edit there). Setting 62 would put the top water block at Y = 61, one block below the water line the specification (and the exported world) have.

### A10. What a landmark's "Y band" constrains
* **Documents:** every landmark has a Y band (quarry 85–110, Glacial Spine 180–279, Fen 62–66 …) and acceptance #2 is "nine landmarks at their exact centres and elevation bands". Neither document says whether the band bounds **every column** of the landmark's box or the landmark's **characteristic features and centre**. The only operational definition is HANDOFF §9.4: the verifier "compares each landmark's top block and elevation with the specification", with `--pad` (25 % of the band) as tolerance — i.e. it is checked *at the landmark*.
* **Mod:** every centre is pinned to its exact elevation (±1 block is what `/vantraya verify` accepts; the pins are exact), and the landmark's features follow the offline engine's shapers (summits to 279, caldera rim 146, throne 92, 9 m benches, dune ridges, terraces, needles). The rest of each box is continental terrain feathered into the landform over 200 blocks. **The bands are therefore not clamps on every column**, and for the Glacial Spine they cannot be: its box is 3,600 × 2,000 blocks and its band is the summit range (the PDF's elevation table gives the horns as 220–279 and lists the subalpine taiga foothills separately, at 150–190).
* **Measured** (the model, canonical seed, 20-block grid over each landmark's own columns; the share inside the band for the canonical seed and two others):

  | Landmark | Band | median Y | share of columns inside the band |
  |---|---|---|---|
  | Forgotten Coast | 68–74 | 71 | 45 % · 29 % · 38 % (the box includes the shoreline and the land rising behind it) |
  | Cogwork March | 85–110 | 98.5 | 89 % · 88 % · 88 % |
  | Ashen Caldera | 38–150 | 80 | 99 % · 99 % · 96 % |
  | Glacial Spine | 180–279 | 109 | 3 % · 4 % · 4 % (peaks and ridge lines only) |
  | Gilded Dunes | 75–94 | 85 | 74 % · 54 % · 72 % |
  | Whispering Fen | 62–66 | 64 | 57 % · 60 % · 40 % (box corners leave the continent) |
  | Sunken Reach | 50–62 | 55 | 73 % · 76 % · 79 % |
  | Hermit's Spire | 140–185 | 108 | 22 % · 32 % · 13 % (needles only) |
  | Byzantine Choir | 110–145 | 121 | 84 % · 85 % · 85 % |

  For the quarry: its interior (more than 250 blocks inside the box) runs 60–101 and never reaches 110; about 9 % of it is below 85 (the pit floor and the river chasms), and its rim feathers into the surrounding continent, which puts 5–7 % of the rim columns above 110. That follows the offline formula `floor((85 + 34·(1−cone))/9)·9`, whose benches are multiples of 9 (81, 90, 99, 108, 117) — not the "85, 94, 103, 112" its comment promises.
* **Change:** if you want the band to hold for *every* column of a landmark (a stricter reading than the offline engine's), say so — it is a clamp per landform in `Landforms`, but it would flatten the Spine and the Spire into slabs, so I did not do it unasked.

## B. Resolved by the documents themselves (no choice made)

| Point | How the documents resolve it |
|---|---|
| The Hermit's Spire and Byzantine Choir have **no density windows in the PDF's §3 table** (it lists 7 regions and the Veil) | HANDOFF §3.2 gives all nine; those are the numbers used (`SpecTest` pins them) |
| Landmark numbering: the PDF counts 1–9, the HANDOFF 0–8 | cosmetic; code uses the HANDOFF's ids, `/vantraya info` prints 1–9 as the PDF does |
| H_drop = −600·S(t) would pass through the world floor (Y = −64) | HANDOFF §5.7: the curve is the *shape*, depth clamped to the abyss floor Y = −32 → `Spec.shelfDrop` |
| The PDF says the dropoff reaches full depth at r = 3800, the Veil begins at 3550 | both kept: shelf curve from 3300 over 500 blocks, the Veil's flat floor beyond 3550 (the clamp reaches −32 before that) |

## C. Things the specification does not cover and a runtime mod must decide

| Question | Decision | Where |
|---|---|---|
| What does "the seed" mean when the specification fixes the landmarks? | The world seed varies coastline, relief, river courses and the Glacial Spine's peaks; landmarks never move. `canonicalWorld = true` always builds the shipped Ashenfall continent (seed 20250929's actual instance tables). **Typing `20250929` as the seed does not give the canonical world**: a density function is never told the seed, so the model's seed is a fingerprint of a seeded noise, not the number itself | DESIGN.md §4; `WorldSeeds` |
| Global offline stages (percentile normalisation, distance transforms, erosion, flow accumulation, rain-shadow advection) | local equivalents | DESIGN.md §5 |
| Rivers | the project's own algorithm in a local form: valleys + lake basins; **no ported or decompiled mod code** (HANDOFF §14). They cannot run at altitude (Minecraft's generated water is sea-level water) | DESIGN.md §5 |
| "Never infer water from height" (HANDOFF §12.1) | **Cannot hold literally at runtime**: Minecraft floods any ground below its sea level; there is no separate water mask. It is honoured in intent: the model alone decides where ground dips below the waterline (the sea, carved rivers and lakes); the dry landforms are kept from ponding (`dry` / `no_lake` masks; `ModelTest`); and the caldera — whose floor, Y 40, is below sea level by specification — is drained after the noise fill and then given lava basins only. A hollow the model did not intend as water can still flood if it lies below Y 62 | `VantrayaModel`, `CalderaFluids` |
| Structures, giant fungal trees (spec acceptance #10), populate mask | out of scope as in the handoff: decoration is the biomes' own features; soil is stripped where the slope table says so, which is the mask's job done live | README, COMPATIBILITY.md §6 |
| Biome IDs for Still Life | unknown; Vantraya uses vanilla biomes through role tags that other mods' biomes can join | COMPATIBILITY.md |
| The PDF's Still Life tags (`still_life:has_canopy` …) | not verifiable; **not used** | COMPATIBILITY.md |
| Spawn | the Forgotten Coast, (0, 68, 2500) | `SpawnAndBorderHandler` |
| World border | optional (`enforceWorldBorder`), off by default: the abyss continues beyond ±4000 | config |
| "The mods decorate, vanilla does not" (HANDOFF §1.2, §3.5) | **Cannot hold literally at runtime.** It is the export's contract (chunks left at `features`, `generate_features = 0`, the mods populate on first load); a live world decorates each chunk as it is generated, with the features of whichever biomes are placed. Vantraya places **vanilla biomes by default, so vanilla's own features run** (trees, flowers, ores …) together with any mod's features that are bound to vanilla biomes. To make another mod the decorator, replace the vanilla entries of the role tags with that mod's biomes (`"replace": true`, COMPATIBILITY.md) — then the placed biomes, and so their features, are that mod's. That needs the mod's biome IDs, which are unknown here | COMPATIBILITY.md §4, §6 |
| Naming | **"Vantraya"** (your spelling) for the mod and the world type; **"Vantyra"** (the documents' spelling) is kept for the continent | README |

## D. Not verified

* **Nothing here has run inside Minecraft.** The numeric model is verified against the Python engine (DESIGN.md §8); the whole mod compiles against the real NeoForge 21.1.176 jars and the 79 tests pass in GitHub Actions. First step: create a world with the Vantraya type and run `/vantraya verify`.
* Entries A2 and A5 are the two places where the live world deliberately differs from the offline export at the *geometry* level; A1, A7 and A8 differ at the *surface* level only. Everything else matches the offline engine to the tolerances in DESIGN.md §8.
