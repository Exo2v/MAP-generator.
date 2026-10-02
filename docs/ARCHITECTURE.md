# Architecture

```
mg/
├── config.py            GenerationConfig (+ RegionInfo), defaults, 7 presets,
│                        validate / resolve_derived / save_config / load_config
├── pipeline.py          run_pipeline(cfg, progress=, should_cancel=, include_population=)
├── cli.py               python -m mg.cli  generate | map | inspect | presets | serve
├── desktop.py           desktop launcher: background server thread + native window
├── core/
│   ├── noise.py         hash2/hash3, value & gradient noise, fbm, ridged, cellular,
│   │                    domain warp, FractalSpec, gaussian_blur, normalise helpers
│   ├── types.py         RegionInfo, TerrainGrid, RiverPath/Lake/POI/ChunkBlocks,
│   │                    GenerationResult, MIN_Y/MAX_Y/SEA_LEVEL/CHUNK, BIOMES[40]
│   ├── surface_rules.py temperature/humidity edges, 6x5 land material matrix,
│   │                    biome_from_climate()
│   ├── hydrology.py     priority_flood, flow_directions, accumulation, watersheds,
│   │                    fill_lakes, ocean_mask, build_river_network, carve_channels,
│   │                    warp_field
│   ├── erosion.py       slope/curvature/hillshade, thermal_erosion + talus_relaxation,
│   │                    stream_power_erosion, droplet_erosion, deposit_sediment, diffusion
│   └── materials.py     BLOCKS (id == index), BLOCK_ID/ID_BLOCK, namespaced names,
│                        version aliases, surface PROFILES per biome, ORE_BLOCKS
├── generation/
│   ├── climate.py       continentality, temperature, humidity, rain shadow, wind
│   ├── terrain.py       continents, ridged mountains, plateaus, cliffs, islands, terracing
│   ├── water.py         sea/lake/river masks, carving, flow field assembly
│   ├── biomes.py        climate + elevation + water -> biome id, transitional blending
│   ├── population.py    fertility/population fields, POIs, roads, vegetation species
│   ├── structures.py    villages/keeps/mines/ruins builders, road stamping
│   └── surface.py       TerrainSampler: per-chunk columns -> blocks, caves, ores, trees
├── export/
│   ├── nbt.py           NBT writer + reader (all 12 tag types, little/big endian)
│   ├── anvil.py         pack_bits, build_chunk_nbt, RegionFileWriter (+ adopt_existing)
│   ├── world.py         export_world -> ExportResult, level.dat, mapgen.json, bundles
│   └── schematic.py     Sponge Schematic v2 writer, spread tile exporter, WorldPainter
│                        bundle (16-bit heightmap, water, biomes, flow, README)
├── server/
│   ├── app.py           stdlib ThreadingHTTPServer: routes for config/generate/job/
│   │                    mesh/maps/inspect/preset/export/static
│   ├── jobs.py          JobManager: background generate/export jobs, progress, cancel
│   ├── views.py         build_mesh (base64 vertex/colour/index buffers), map_raster,
│   │                    write_map_bundle, column_info
│   └── static/          index.html, style.css, app.js, controls.js, three.min.js
└── tools/
    └── inspect_world.py CLI: decode an exported .mca back to block ids / PNG
```

## Pipeline order (`mg/pipeline.py`)

| # | Stage | Progress | What happens |
| --- | --- | --- | --- |
| 1 | grid | 0.02 | Cell coordinate grids at `cell_size` resolution. |
| 2 | climate | 0.12 | Continentality, temperature (latitude + elevation + noise), humidity (wind + rain shadow), sea level. |
| 3 | terrain | 0.28 | Continent base, folded mountain ridges, plateaus/mesas, coastal cliffs, islands, optional terracing. |
| 4 | erosion | 0.28–0.50 | Diffusion → thermal (talus) → stream power along flow → deposition → droplet pass → channel smoothing. |
| 5 | water | 0.50–0.70 | Priority-flood basins, flow directions, accumulation, watersheds, lake filling, river network, channel carving with bank flare and meanders. |
| 6 | climate refinement | 0.74 | Recompute humidity/locality now that valleys and lakes exist (lake breeze, valley moisture). |
| 7 | biomes | 0.78 | Climate + elevation + water class → biome id, with transitional blending between neighbours. |
| 8 | population | 0.78–0.97 | Fertility/population fields, POIs, roads, vegetation species, ore seeding. |
| 9 | assemble | 0.98 | `TerrainGrid` + diagnostics + per-stage timings. |

`TerrainSampler` (stage N) is what the exporter and the preview pull chunks from: it
generates one 16×16 column set at a time (surface profile, caves, ores, trees, structures),
which is why a 128×128 world can be exported chunk-by-chunk without ever holding the whole
3D volume in memory.

## Data contracts you can rely on

* **Block ids are palette indices.** `mg.core.materials.BLOCKS[i]["id"] == i`, `0` is air.
  `BLOCK_ID`/`ID_BLOCK` map names ↔ ids, `namespaced()` adds `minecraft:`, and
  `BLOCK_ALIASES` renames blocks that changed name between MC versions (e.g. grass →
  short_grass, tallgrass → tall_grass) so a 1.21 palette stays valid.
* **TerrainGrid** carries grid-shaped arrays (`heightmap`, `temperature`, `humidity`,
  `flow_x/flow_z`, `discharge`, `biome`, `water_mask`, `lake_mask`, `ocean_mask`,
  `fertility`, `population`, `soil_depth`, `continentality`, `slope`) all at
  `region.cells_z × region.cells_x`. Anything flat (river ribbons, POIs, roads) lives in
  lists on the grid.
* **Chunks** are `ChunkBlocks(blocks=(Y, 16, 16) uint16 id, biomes=(16,16) uint8 canonical
  biome index, heightmap=(16,16) int32, y_offset)`. The exporter and the mesh builder both
  consume exactly this type, so preview and save can never disagree.
* **NBT** is written as an unnamed root compound *containing* `Data` for `level.dat`; chunk
  NBT is a bare compound. `pack_bits` packs palette indices into 64-bit words (never
  `astype(">u8")` — use `.view(np.int64)`), and `bits_for_palette` gives the per-version
  minimum (4 bits below 1.16, `max(4, ceil(log2(n)))` after).
* **Region files** are only ever appended to: `RegionFileWriter` flushes a batch of chunks
  and, if a writer for an already-flushed region is created again, `adopt_existing` re-reads
  the existing chunk index first. Reopening with truncation would silently delete chunks.

## Extending it

* **New biome / material**: add to `BIOMES` (order matters — it is the palette index),
  add the colour to the biome colour table, then map it in `surface_rules.py` or the
  material `PROFILES`.
* **New terrain feature**: add a stage to `mg/generation/terrain.py`, mask it, and feed the
  height array through the existing erosion/water stages — they do not care where heights
  came from.
* **New export format**: consume `TerrainSampler` for voxels (`sampler.generate_chunk`) or
  `result.terrain` for fields, and return a list of written paths so it shows up in the
  export job's `extras`.
* **New map raster**: add a branch to `views.map_raster`, then add the kind to
  `DEFAULT_PREVIEW["maps"]` (preview tabs) or `DEFAULT_EXPORT["bundle_maps"]` (bundle).
