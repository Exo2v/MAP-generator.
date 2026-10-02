# Working alongside Lithosphere, Still Life, Tectonic and Lithostitched

This page says exactly what "alongside" means for Vantraya Builder, what is known about each companion mod and **how** it is known, how to make another mod's biomes part of a Vantraya world, and what is *not* done.

> **Evidence levels used below.**
> **Read** = I read the mod's public source or its published description. **Inferred** = follows from how Minecraft/NeoForge worldgen works, but was not run. **Unknown** = I could not find out. *Nothing on this page has been run in-game* — none of the companion mods could be downloaded in the environment this was written in.

## 1. The short version

* A Vantraya world is a normal Overworld with its **own** dimension type, noise settings, chunk generator and biome source. It does **not** edit `minecraft:overworld`'s noise settings or dimension type, which is exactly what Lithosphere and Tectonic replace. So the three mods and Vantraya do not fight over the same files, and the mods keep working unchanged for every other world type.
* Inside a Vantraya world the **terrain is the specification's**, not Lithosphere's or Tectonic's. This is deliberate: the specification pins nine landmark centres to exact Y levels and bands; a second terrain generator contributing height would move them. (Lithosphere and Tectonic are also not a source of *code* for this project — the handoff's rule is that nothing is decompiled or ported.)
* What **is** shared with the other mods:
  1. everything keyed to **vanilla biomes and vanilla tags** — Vantraya places vanilla-namespace biomes, so biome modifiers, feature mods and structure mods that decorate vanilla biomes decorate Vantraya too;
  2. **biome role tags** (§4 and [Adding another mod's biomes](#adding-another-mods-biomes)): other mods' biomes can be added to the places a role covers, which is how Still Life's (or any biome mod's) biomes can appear in a Vantraya world;
  3. **Lithostitched** worldgen modifiers that target the overworld level stem (§3).

## 2. What each mod is, and what it does to Vantraya

| Mod | What it is *(evidence)* | What it changes | Effect on a Vantraya world |
|---|---|---|---|
| **Lithosphere** (Jayzet) | Closed-source terrain rework, published as a data pack **and** as Fabric/Forge/NeoForge/Quilt mods (server-side). *Read* (Modrinth page): replaces the default overworld `noise_settings`; other datapacks are made compatible by pointing their noise settings at Lithosphere's `final_density`; rivers are rarer and carve valleys. | The `minecraft:overworld` noise settings. | None on terrain: Vantraya's generator uses `vantraya_builder:vantraya` noise settings. **Unknown** whether it adds anything else (biome modifiers, features) that would apply to vanilla biomes. |
| **Still Life** (Jayzet) | Closed-source overworld biome rework, published as a data pack **and** as mods like Lithosphere; **requires Lithosphere** ("as a datapack or mod") and must be above it in the world's Data Packs list. *Read* (Modrinth page): replaces the vanilla surface biomes with its own plus transitional biomes; says it should not be combined with other biome mods. The page lists display names only — **its biome IDs and namespace are unknown to me.** It also says it has built-in compatibility for Dungeons and Taverns and Towns and Towers. *Unknown*: whether it overrides vanilla biome files (if it does, the vanilla biomes a Vantraya world places carry those changes too). | The overworld's biome set. | Its biomes **do not appear by themselves** in a Vantraya world, because Vantraya's biome source decides biomes. They appear where you add them to a role tag (see [Adding another mod's biomes](#adding-another-mods-biomes)). Anything Still Life attaches to *vanilla* biomes, if anything, applies. |
| **Tectonic** (Apollo) | Open-source terrain mod; depends on Lithostitched. *Read* (source, current development branch — the 1.21.1 build may differ in details): ships a datapack that replaces `minecraft:overworld` noise settings; a mixin on `NoiseBasedChunkGenerator` that acts **only if the generator's noise-settings key has the path `overworld`**; a Lithostitched modifier `tectonic:set_height_limits` aimed at the `minecraft:overworld` dimension type and noise settings; a world-preset tag `tectonic:incompatible` that hides listed presets on the creation screen (Large Biomes and Amplified in the version read); config-gated Lithostitched `add_features` modifiers (underground-river ice, lanterns, lichen) aimed at `#minecraft:is_overworld` and `#c:is_snowy` biomes. | `minecraft:overworld` noise settings and dimension type; adds features to vanilla biome tags. | Terrain, height limits and the generator mixin do not touch Vantraya (it uses its own keys, and `vantraya` ≠ `overworld`); the Vantraya preset is not in the `incompatible` tag, so it is listed. The feature modifiers **do** apply to the vanilla biomes Vantraya uses (*inferred*); they are built for Tectonic's own underground rivers, so they may find nothing to attach to, or look out of place — each is switchable in Tectonic's config. |
| **Lithostitched** (Apollo) | Open-source worldgen library. *Read* (source, 1.21.1 branch): modifiers such as `add_surface_rule` (aimed at level stems), `wrap_density_function` (aimed at density-function IDs), `add_features`, `add_structure_set_entries` …; needs access transformers for `SurfaceRules` internals on 1.21.1. | Whatever a pack's modifiers say. | A Vantraya overworld is the `minecraft:overworld` level stem with a `NoiseBasedChunkGenerator` subclass, so modifiers aimed at the overworld should apply (*inferred*, untested). Surface rules they add run as part of vanilla's `buildSurface`; Vantraya's slope table then repaints only where the specification says (§6). |

### Why Vantraya does not borrow Lithosphere's or Tectonic's terrain

1. **Exactness.** Each landmark's centre must land on its specified Y; the elevation bands and the Veil's abyss floor are fixed. Height contributed by another generator cannot be reconciled with that, and `RouterTest` / `/vantraya verify` would fail.
2. **Closed source.** Lithosphere and Still Life publish no source; what they do inside is not something to build on, and the handoff forbids ported mod code.
3. **They already do the same job.** Lithosphere's and Tectonic's terrain *is* a replacement for the overworld's. Doing both would be two terrains for one world.

If you want a Vantraya-flavoured region of an otherwise Lithosphere or Tectonic world, that is a different mod design (a biome/region injection) and is not what this mod does.

## 3. How the isolation works (and its one shared surface)

| Thing | Vantraya's | Why it matters |
|---|---|---|
| world preset | `vantraya_builder:vantraya`, added to `#minecraft:normal` | shown next to Default / Superflat / Large Biomes / Amplified |
| dimension type | `vantraya_builder:vantraya` (height 384, `min_y` −64, a copy of the Overworld's otherwise) | `set_height_limits`-style changes to `minecraft:overworld` don't reach it |
| noise settings | `vantraya_builder:vantraya` | replacement datapacks for `minecraft:overworld` don't reach it |
| chunk generator | `vantraya_builder:vantraya` (subclass of vanilla's `NoiseBasedChunkGenerator`) | a mixin gated on the key `overworld` doesn't act on it |
| biome source | `vantraya_builder:vantraya` | Still Life's/other biome sources don't run; their biomes join through role tags |
| density functions | `vantraya_builder:field/*`, `depth`, `sloped_cheese` | other packs may *reference* them (§7) |
| 3D roughness noise | `vantraya_builder:base_3d_noise` — a copy of vanilla's `minecraft:overworld/base_3d_noise` | a pack that overrides the vanilla function cannot change the terrain's roughness, so the pinned elevations hold (`RouterTest` fails if a terrain function starts referencing a vanilla one) |
| cave functions | vanilla's, **by id** (`minecraft:overworld/caves/*`) | deliberately shared: a cave overhaul that overrides those functions applies in Vantraya worlds too (*inferred*, untested) |
| mixins / access transformers | **none** | only public API; nothing here can break another mod's mixin target |

The one surface that **is** shared is the dimension's *key*: a Vantraya world's overworld is still `minecraft:overworld` (the preset replaces what sits under that key), the Nether and End are vanilla. Anything keyed to the overworld level stem, to vanilla biome IDs/tags, or to vanilla structure sets therefore sees a normal Overworld.

## 4. Biome roles and their tags

The specification and the offline generator describe biomes in a *vocabulary of roles* (`temperate_forest`, `xeric_shrubland`, `old_growth_taiga` … — Still-Life-flavoured names) which the exported world mapped onto vanilla biomes. Vantraya keeps that: every **role** is a biome tag,

```
#vantraya_builder:biome/<role>      file: data/vantraya_builder/tags/worldgen/biome/biome/<role>.json
```

(the folder really is `biome/biome`: the registry folder, then the `biome/` prefix of the tag's path). Out of the box each tag holds one entry — the vanilla default — so a Vantraya world uses vanilla biomes only. The classifier decides *which role* a place has (from the landmark, the climate tier, elevation, water); the tag decides *which biome(s)* realise it.

| Where it is used | Roles *(vanilla default)* |
|---|---|
| Forgotten Coast | `plains` *(plains)*, `meadow` *(meadow)*, pebble beaches: `stony_shore`, `snowy_beach` |
| Cogwork March | `wooded_badlands`, `windswept_hills` |
| Ashen Caldera | `basalt_deltas`, `eroded_badlands` |
| Glacial Spine | `grove`, `jagged_peaks`, `frozen_peaks` |
| Gilded Dunes | `desert`, `badlands` |
| Whispering Fen | `swamp`, `mangrove_swamp` |
| Sunken Reach | `warm_ocean`, `lukewarm_ocean` |
| Hermit's Spire | `windswept_hills`, `meadow` |
| Byzantine Choir | `meadow`, `cherry_grove` |
| Veil of Salt | `deep_cold_ocean`, `deep_ocean` (alternating) |
| open sea | `ocean`, `cold_ocean`, `frozen_ocean`, `warm_ocean`, `lukewarm_ocean`, `deep_ocean`, `deep_cold_ocean`, `deep_lukewarm_ocean`, `deep_frozen_ocean` |
| inland water | `river` *(river)*, `frozen_river` |
| shores | `beach`, `snowy_beach`, `stony_shore` |
| land outside the landmarks (the Whittaker grid) | `snowy_plains` *(snowy_plains)*, `snowy_taiga`, `grove`, `cold_shrubland` *(windswept_gravelly_hills)*, `taiga`, `old_growth_taiga` *(old_growth_pine_taiga)*, `highland_steppe` *(windswept_hills)*, `plains`, `temperate_forest` *(forest)*, `old_growth_temperate_forest` *(dark_forest)*, `windswept_hills`, `xeric_shrubland` *(savanna_plateau)*, `meadow`, `fertile_valley` *(sunflower_plains)*, `swamp`, `mangrove_swamp`, `desert`, `savanna`, `humid_savanna` *(savanna)*, `sparse_jungle`, `jungle`, `tropical_rainforest` *(sparse_jungle)* |
| high ground outside the landmarks | `temperate_mountains` *(windswept_hills)*, `warm_temperate_mountains` *(windswept_savanna)*, `cold_mountains` *(snowy_slopes)*, `arid_mountains` *(badlands)*, `badlands_mesa` *(badlands)*, `glacier` *(snowy_plains)*, `alpine_peaks` *(frozen_peaks)* |
| caves | `lush_caves`, `dripstone_caves`, `deep_dark` (vanilla's thresholds) |
| **reserved — not placed yet** | `shallow_coast`, `lake`, `volcanic_highland`, `salt_flats` — in the offline vocabulary, but the live classifier never produces them, so adding biomes to them does nothing |

The full list with every default is the set of files in `src/main/resources/data/vantraya_builder/tags/worldgen/biome/biome/` (57 tags, one per `BiomeRole`). When a mod biome *belongs* in a role is a matter of climate: Vantraya spreads whatever is in the tag over every place the role covers, so put a desert biome in `desert`, not in `temperate_forest`.

## Adding another mod's biomes

This is how Still Life's, Biomes O' Plenty's or any other mod's biomes become part of a Vantraya world. **I do not know Still Life's biome IDs** (its Modrinth page gives display names only, and it is closed source), so the repository ships no entries for it — list them in your own pack as follows.

**1. Find the IDs.** In a world with the mod installed, as an operator:

```
/vantraya biomes <namespace>        e.g.   /vantraya biomes still_life
```

It lists every biome in that namespace. (If the namespace is wrong it says so; the server log at start-up also prints which biome namespaces exist, because `logCompatReport` is on by default.)

**2. Write a tag file** — in a datapack, or in `data/` of your own mod or pack, however you normally ship data in the modpack:

`data/vantraya_builder/tags/worldgen/biome/biome/temperate_forest.json`

```json
{
  "replace": false,
  "values": [
    { "id": "some_mod:placeholder_temperate_forest", "required": false },
    { "id": "some_mod:placeholder_old_temperate_forest", "required": false }
  ]
}
```

*The two IDs are placeholders to show the shape; use the ones from step 1.*

* `"required": false` lets the same pack work with or without the other mod: if the biome does not exist the entry is skipped instead of the world failing to load.
* **Tags merge.** With `"replace": false` the role keeps its vanilla default **and** gains your biomes. Set `"replace": true` to use only yours.
* To make one pack cover several roles, add one file per role.

**3. What Vantraya then does.** When a role's tag has more than one biome, they alternate in patches about **320 blocks** across, chosen by a deterministic hash of the patch position and the role, each member equally likely. The hash does not use the world seed, so the patch layout is the same in every Vantraya world (and always reproducible). Biomes are ordered by ID before choosing, so load order never changes the result. The landmarks, the climate tiers and the slope-aware surface still decide *where* and *how steep*; the biomes you add only decide *which flavour* of that role appears. Each added biome brings its own features, spawns, colours and (for mods like Still Life) its own vegetation.

**4. Mind these limits.**

* Biome tags are read when the world is created/loaded, and the role palettes are resolved the first time worldgen asks for them. **Install the pack before creating the world**, or restart the world after adding it; already-generated chunks keep the biomes they have.
* A role tag only affects places that role covers. If you add a biome to a role that does not occur near you, you will not see it.
* The mod you add must have been written for this Minecraft version, and a *biome* built around Lithosphere's terrain (for instance one that assumes Lithosphere's `final_density`) will still sit on Vantraya's terrain.
* There is no climate check: a tag can put a tropical biome in a role used on the Glacial Spine. Vantraya does not second-guess it.

**5. Check it.** The log at server start lists, per role, how many biomes it now spreads (`Vantraya: role 'temperate_forest' spreads 3 biomes: …`), and `/vantraya where` followed by `/locate biome <id>` shows where one landed.

### Trying the specification's "method 2" for Still Life features (unverified)

The master specification (PDF, §5 "method 2") says Still Life binds its feature placement to biome tags — `still_life:has_canopy`, `still_life:has_fallen_logs`, `still_life:has_boulders` — and that adding vanilla biomes to them makes Still Life decorate those biomes. **I could not confirm those tag names in any public source** (Still Life has none), and they read like placeholders, so nothing here depends on them and they are not shipped. If they exist in your Still Life build, a pack can add vanilla biomes to them in the same `required: false` style:

`data/still_life/tags/worldgen/biome/has_canopy.json`
```json
{ "replace": false, "values": [ { "id": "minecraft:plains", "required": false }, { "id": "minecraft:forest", "required": false } ] }
```

Be aware that tags are global: such a file would change Still Life's behaviour in **every** world type, not just Vantraya's.

## 5. What you can see at start-up

With `logCompatReport = true` (default) the server log prints, once, at start:

```
Vantraya: companion mods detected: [...]            # Lithostitched / Tectonic / Lithosphere / Still Life, by mod id
Vantraya: data packs that look like worldgen companions: [...]   # selected data packs, by pack id
Vantraya: biome namespaces available: {minecraft=65, ...}
Vantraya: role 'xeric_shrubland' spreads 3 biomes: [...]   # only roles that gained biomes
```

The mod ids it looks for are `lithostitched`, `tectonic`, `lithosphere`, `still_life` and `stilllife` (the last two because Still Life's mod id is not confirmed). Lithosphere and Still Life are also published as plain **data packs**, which have no mod id, so the report also lists the data packs selected for the world whose pack id contains `lithosphere`, `tectonic`, `lithostitched`, or `still` and `life`. Anything installed under another name is not reported by name — it still works, and the biome-namespace line shows its biomes.

`neoforge.mods.toml` declares Lithostitched and Tectonic as **optional** dependencies, loaded before Vantraya, with any-version ranges. Nothing is required except NeoForge and Minecraft.

## 6. Surfaces and decoration

* Where the slope is < 25° (and outside the landmark palettes) Vantraya leaves the **biome's own surface**, so a mod biome's own topsoil shows through.
* Where the specification says otherwise (steeper ground, the caldera, the dune crests, the treeline, the Veil floor) Vantraya repaints the top layers after the biome surface rules ran, whatever mod added them. That is deliberate: stripping the soil is what keeps trees — vanilla's, Still Life's, any mod's — off cliffs and summits, as the specification's "method 3" intends.
* Vegetation, ores, structures and mobs are the biomes' own features and the structure mods' own rules; Vantraya adds none. The specification's structures and giant fungal trees are not generated (the handoff leaves them to the mods).
* **Who decorates.** The handoff's export contract was "the mods decorate, vanilla does not" (chunks left undecorated, mods populate on first load). A live world has no such step: each chunk is decorated as it is generated, with the features of the biomes placed there. Out of the box Vantraya places **vanilla biomes**, so **vanilla's own trees, flowers and ores are generated** — plus whatever other mods attach to vanilla biomes. To let Still Life (or any biome mod) decorate *instead of* vanilla, replace the vanilla default of the relevant role tags with that mod's biomes (`"replace": true`, see above); the biomes placed — and so their features — are then the mod's. This needs that mod's biome IDs, which this repository does not have.

## 7. For pack authors: reusing the specification's fields

The model's channels are ordinary density functions, usable from any datapack by reference:

`vantraya_builder:field/continents`, `field/erosion`, `field/ridges`, `field/temperature`, `field/humidity`, `field/height` (blocks), `field/rough3d`

Each carries a seeded noise (`vantraya_builder:seed_probe`), so it follows whichever world seed the dimension uses, with no global state. A Lithostitched `wrap_density_function` aimed at one of them, or another dimension's noise router that reads `field/height`, should work (*inferred*, untested). Overriding `vantraya_builder:worldgen/noise_settings/vantraya` entirely is possible but removes the exactness guarantees.

## 8. What is *not* done

* No terrain, density or spline from Lithosphere or Tectonic is used inside a Vantraya world (§2).
* No Still Life biome IDs are bundled (unknown); its biomes join through your tag files (see [Adding another mod's biomes](#adding-another-mods-biomes)).
* None of the companion mods has been run with Vantraya. The isolation argument (§3) is by construction; the interactions in §2 marked *inferred* are expectations, not observations. Please report any crash or oddity with the log lines from §5 — that is the first thing needed to fix it.
* Mods that **replace the Overworld's chunk generator wholesale** (rather than its noise settings or biomes) are outside what this page covers.
* The world type must be chosen when the world is created; an existing world cannot be converted.
