# Ashenfall / Vantyra — WorldPainter asset set

Generated from the specification with the turnkey command, at 2048² and cell size 8:

```bash
python generate_ashfall.py --res 2048 --out worldpainter
```

| File | What it is |
| --- | --- |
| `ASHFALL_HEIGHTMAP_16BIT.png` | 16-bit heightfield. `uint16 = round(((Y + 64) / 384) * 65535)`, so the image decodes to Y −30.7 … 229.2 over the spec's −64 … 320 envelope. |
| `ASHFALL_POPULATE_MASK.png` | The Still Life populate mask (spec §5, method 1): white = let the mod's feature registry decorate this chunk. |
| `ASHFALL_WATER_MASK.png` | Standing water and channels. |
| `ASHFALL_SLOPE_MASK.png` | Slope, for the §6 slope-banded surface rule. |
| `ASHFALL_SCREE_MASK.png` | Bare scree where the slope strips topsoil. |
| `ASHFALL_FROST_MASK.png` | The high-altitude frost / snow line. |
| `ASHFALL_BIOME_MAP.png` | The colour-coded biome raster. |
| `ASHFALL_SURFACE_TABLE.json` | The §6 surface rule as data (slope band → block, treeline, per-landmark materials). |
| `ashenfall_worldpainter_setup.js` | JSR-223 script that rebuilds the world in WorldPainter from the rasters above. |

Regenerate at full resolution with `--cell 4`, or import the set straight into
WorldPainter and run the setup script. Nothing here is required to use the Python
generator — it is the offline copy of the map's build layers.
