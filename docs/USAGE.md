# Using MAP-generator

The UI is a single page with a control column on the left (World / Climate / Terrain /
Erosion / Water / Population / Export tabs), a 3D viewport with map tabs on the right, and
a progress strip at the bottom.

## The workflow

1. **Pick a preset** — the dropdown at the top reloads every slider to a known-good recipe.
2. **Set size and seed** — the seed is the identity of the world; the same seed + config
   always produces the same terrain.
3. **Generate world** — runs the whole pipeline (~10 s for 1024×1024 at cell size 4 on two
   cores; ~4 minutes for 4096×4096). Press **Cancel** to abort mid-run.
4. **Look at it** — orbit / pan / zoom, switch color modes, flip through map tabs, hover the
   surface for a per-column read-out.
5. **Pick an output folder** and press **Export world**. The world is written as a normal
   Minecraft Java save.

## Controls

### World
| Control | Effect |
| --- | --- |
| Name | Folder + `level.dat` name of the exported world. |
| Seed | Deterministic RNG seed. The 🎲 button rolls a new one. |
| Width / Depth | Region size in blocks. The simulation uses a coarser grid; chunks are produced at full resolution. |
| Cell size | Simulation cell in blocks (1–16). 1 = highest detail, slowest; 4 is the sweet spot; 8–16 is for very large continents. |

### Climate (Lithosphere-style)
| Control | Effect |
| --- | --- |
| Warmth / Humidity bias | Shifts the whole world's climate up or down. |
| Rain shadow | How strongly mountains dry out the downwind side. |
| Wind direction | Direction the wind blows; drives rain shadow and moisture transport. |
| Climate region size | Multiplier on the noise scale. Bigger = larger, smoother biome regions (the Lithosphere look); smaller = more, smaller biomes. |

### Terrain (World Machine-style)
| Control | Effect |
| --- | --- |
| Land fraction | Share of the map that ends up above sea level. |
| Mountain height / density | Peak amplitude and how many folded ridges exist. |
| Plateau amount | Fraction of the land turned into flat-top mesas. |
| Cliffs / coast | Steepness of coastal cliffs and inland scarps. |
| Islands | Seeded island count for archipelago-style maps. |
| Terracing | Quantises height into shelves (stylised, good for building maps). |

### Erosion (terrain diffusion)
| Control | Effect |
| --- | --- |
| Smoothing | Global diffusion pass over the height field. |
| Thermal passes | Angle-of-repose material sliding; carves scree slopes below cliffs. |
| Fluvial strength / passes | Stream-power incision along the flow network — this is what makes valleys V-shaped. |
| Rain droplets | Optional droplet simulation (0 = off; 100k+ is expensive but adds fine detail). |

### Water (Streams Reflowing-style)
| Control | Effect |
| --- | --- |
| River threshold | Flow-accumulation threshold that decides where a stream becomes a river. Lower = more, smaller rivers. |
| Valley depth | How deep rivers cut into the terrain. |
| Bank flare | Width of the carved floodplain either side of a river. |
| Meander | Lateral wander of the channel. |
| Carve passes | Repeats of the carving pass; higher = smoother, more natural banks. |
| Lake depth filter | Depth threshold for keeping filled basins as lakes rather than swamps. |

### Population (Still Life-style)
| Control | Effect |
| --- | --- |
| Settlements | Number of POIs (villages, keeps, mines, ruins, camps…), placed by a population/fertility model rather than uniformly. |
| Tree / shrub density | Multipliers on vegetation placement; species come from the biome + climate. |
| Ore veins | Number of ore bodies seeded underground. |
| Roads | Connect settlements with route-following roads. |

### Export
| Control | Effect |
| --- | --- |
| Output folder | Anywhere you have write access, e.g. `.minecraft/saves`. |
| level.dat + session.lock | Write a loadable save (off = bare region files only). |
| PNG map bundle | `maps/*.png` + `legend.json` for viewing in any image viewer. |
| structures & vegetation / caves / vegetation blocks | Toggle the expensive object passes. |
| Minecraft version | Palette/data-version pair used in the chunk NBT (`1.21`, `1.20`, `1.18`, …). |

Additional options are available through the config/API (and therefore the CLI):

```python
cfg.export["worldpainter_bundle"] = True   # worldpainter/ heightmap + biome + flow maps
cfg.export["write_schematic"] = True       # schematic/ WorldEdit tiles
cfg.export["schematic_chunks"] = 4         # tile size in chunks
cfg.export["schematic_files"] = 12         # how many tiles, spread over the region
cfg.export["chunk_batch"] = 512            # chunks buffered before a region file is flushed
```

## The 3D viewport

* **Drag** orbit, **right-drag / two-finger** pan, **wheel** zoom.
* Color modes: natural (biome-tinted), height ramp, slope, temperature, humidity,
  population, flow accumulation, soil depth.
* Exaggeration slider stretches the vertical axis for flat worlds.
* Sun azimuth/height re-light the surface; the hillshade is computed per-vertex.
* Toggles for water surface, river ribbons, roads, POIs and wireframe.
* `top` / `iso` / `coast` jump to preset camera angles; `⤓ png` saves the current view.
* Hovering the surface reports height, biome, climate, discharge, soil and the block
  profile that will be written for that column.

## Map tabs

The pipeline produces field rasters next to the mesh: height, hillshade, slope, biome,
water/hydrology, flow direction, discharge, population/vegetation, fertility, temperature,
humidity, climate composite, soil and continentality. Tabs render them on the fly through
`/api/maps/<kind>`; the same raster code writes the export bundle, so what you see in the
preview is exactly what lands in `maps/`.

## Reusing a world

`mapgen.json` inside the exported world is the full config. Reload it in the UI or CLI
(`--config path/to/mapgen.json`) to regenerate the same world, or change the seed for a
sibling world with identical character.
