# Rivers and biome borders — what went wrong, what the study shows, and the design for v3

*Written in response to the play-test document **"problems+solutions V1.2"** (3 October 2026), after the second session in the world. The ten screenshots referred to below are the ones from that document, kept in [`docs/img/playtest2/`](img/playtest2/).*

The report listed four problems: the water is **jagged**, it **does not behave logically**, it is **very buggy**, and **biome blending is still terrible and boxy** — with pictures of water stair-stepping down hillsides, rivers dying in mid-air, a river pouring from a mountainside with nothing upstream, and a whole biome drowned. It asked me to study how other mods and the default game handle rivers and biome transitions, and to write down my mistakes and the proper implementation for the continent of Vantraya. This is that document.

---

## 1. The symptoms, exactly as found

| # | What the screenshots show | Where |
|---|---|---|
| S1 | Water in **stepped staircases** down terraced slopes, like stacked ponds instead of a flowing line | [01](img/playtest2/01-terraced-channels-choir.jpg), [07](img/playtest2/07-terraced-slopes-channel.jpg) |
| S2 | Rivers that **stop in the middle of the land** — a channel that just ends on a hillside | [06](img/playtest2/06-perched-river-no-source.jpg) |
| S3 | Water **flowing down the side of a mountain with no water anywhere above it** — a trough perched on the slope, fed by nothing | [06](img/playtest2/06-perched-river-no-source.jpg) |
| S4 | A channel with **vertical, near-rectangular walls** cut into a plain ("cookie-cutter") | [03](img/playtest2/03-cookie-cutter-trench-plains.jpg), [04](img/playtest2/04-square-biome-patches.jpg) |
| S5 | **An entire area drowned** — one flat sheet of water covering a valley floor behind the dunes | [05](img/playtest2/05-flooded-valley.jpg) |
| S6 | Rivers cut **straight through high terrain** and along the caldera rim in places no drainage would run | [02](img/playtest2/02-canyon-from-snowy-ridge.jpg), [08](img/playtest2/08-river-along-caldera-rim.jpg) |
| S7 | **Square patches** of one biome stamped on another, and **hard straight seams** between contrasting biomes (snow against cherry blossom, badlands against green) | [04](img/playtest2/04-square-biome-patches.jpg), [09](img/playtest2/09-straight-biome-borders.jpg), [10](img/playtest2/10-patchwork-and-hard-borders.jpg) |

---

## 2. My mistakes

Each symptom above is one design mistake, not a pile of separate bugs. In order of damage done:

### M1. The water surface was computed **per column, from the ground under it** — S1, S3

`VantrayaModel.water()` set each column's water surface to *its own* natural ground minus 2 blocks (`RIVER_INCISE`), and `RiverWater.fill()` filled every column up to *its own* value. There is no such thing as "the river's surface" in that design: on a slope each column's water sits 2 blocks under the local ground, so the surface steps down with the terrain. Two columns 20 blocks apart can have water surfaces 10 blocks apart. What comes out is the staircase in S1 and the mountain-side trough in S3.

A river's surface is a property of the **river**, not of the column: one continuous surface per reach, sloping gently downstream. That is the single biggest mistake.

### M2. The channel path was **noise, not drainage** — S2, S3, S6

The path is the zero set of a domain-warped fbm (`zeroSetDistance`, trunk and tributary), scaled by `ridgeFactor · low` where `low` fades out between Y 150 and 190. Nothing in that construction knows where water would actually flow. Consequences:

* A channel can cross a ridge shoulder wherever the noise says so and the fade hasn't cut it off — S6, rivers over high terrain and along the caldera rim.
* Where `low` or `ridgeFactor` falls below the fill threshold, the channel **ends in mid-slope** with no mouth and no sea — S2. The strength fade was meant to be "rivers thin out uphill", but with no network behind it, it reads as "the river evaporates".
* There are no sources: nothing distinguishes a spring from a random wet notch.

The offline specification's engine did this properly — flow accumulation, hydrology, the global stages of §5. The runtime stand-in replaced all of it with local noise (a documented compromise in `docs/SPEC_NOTES.md` §C). That compromise is what you are looking at in the screenshots.

### M3. The valley carve and the fill rule **disagreed** — S5

`water()` carves a wide U toward the channel floor (`out -= valley · max(out − bed, 0) · 0.5`), which lowers a broad swath of ground. `RiverWater.fill()` then floods any column where `river() > 0.3` and the ground sits below that column's own line. On gentle land, the carved swath sinks *below its own per-column line* over a wide area — so the fill spreads across the whole valley floor. That is the drowned valley in S5. The carve and the water were designed against two different definitions of "the channel".

### M4. A narrow notch on a 4-block grid — S1, S4 (and the failed first fix)

Minecraft's engine samples the height field on a 4 × 4 × 8 lattice and interpolates. A channel notch about one lattice cell wide is partly smoothed away: the bed comes out shallower than the model asked for, the banks come out as stair-steps of one-block faces, and the slope-aware surface painter then strips soil from those micro-faces and paints them as scree — the terraced look of S1/S7. My first fix (cutting the channels deeper so some water survives the smoothing) treated a symptom; it made the troughs deeper and *more* like trenches, not less. A channel must be several lattice cells wide with smooth banks before it can look like anything.

### M5. Lakes were **noise blobs**, not basins — S5

`lakeStrength` is an fbm threshold (`smoothstep(0.22, 0.34)`), floored to `line − LAKE_DEPTH`. A real lake is a closed basin with a catchment and a spillway; this was a flat patch of lowered ground wherever a noise blob happened to sit in the lowlands. Combined with M3 it produced flat water sheets with arbitrary shores.

### M6. Hard thresholds at every boundary — S4

`chan` cut off at a fixed fraction of the half-width, `river() > 0.3` decided "wet", `waterLine` was either a number or −10⁹. Each of these is a wall: the channel floor meets its wall at full depth, the fill starts and stops abruptly. Hence the cookie-cutter walls of S4 and the cliff-bank look in S3.

### M7. The biome blending attempt made **squares** — S7

The second attempt at blending (after the first, straight threshold walls) kept the walls and added two things: noise wobble on the thresholds, and — near a decision edge — **alternating 64 × 64 block patches** of the two roles (`BLEND_PATCH`, `patchTakes`). Wobble was the right idea; square patches were the wrong one. The red squares stamped on the green plain in S4/S10 are those patches. And the landmark region seams are still rectangular, so S9's straight snow/cherry line survived untouched. "Terrible and boxy" is a fair description of both mechanisms: the classifier is a decision tree of thresholds (walls), the regions are rectangles (walls), and the cure was squares (walls at right angles).

### M8. I declared victory on tests that measured the wrong thing

The 0.1.2 checks asked "is there water in this column?" and "do landmarks stay pinned?" — both true, both beside the point. Nothing asked: *is there water above this water? does the surface slope downhill? does the channel have a source and a mouth? is there water outside the channel?* A green build with S1–S6 on screen. The v3 test list in §4.4 is written to ask those questions instead.

---

## 3. What the study shows

Sources: vanilla Minecraft 1.18+ worldgen; **Streams** (delvr, open source, see its `RiverGenerator.scala` and the analysis below); **"Why Are Rivers So Complicated?"** (AlcatrazEscapee, public gist — a survey of exactly this problem); **Dynamic Waters: Realistic Flowing Rivers** (AdamNew, NeoForge 1.21.1, CC-BY-4.0); **Tectonic** 2.4.3 source (read in full — `tectonic/worldgen/density_function/overworld/…`); TerraBlender's design notes.

### 3.1 Vanilla: rivers are level sets of smooth fields, and so are biome borders

Vanilla has no river paths at all. Its rivers are where the **continentalness/erosion/ridges parameter fields** and the depth splines pull the ground below the fixed sea level: a "river" is the preimage of a parameter band. Three consequences:

* **The water surface is one global number** (sea level). Nothing can be perched, stepped, or above its source, because there is no per-column surface.
* **The shape is a level set of multi-octave noise**, which is why vanilla rivers meander smoothly at every scale and never zig-zag at 90° — the octaves give fractal detail to a *smooth* base curve (this is the "hitch" of good borders and bends: the noise does the wiggling, the threshold stays invisible).
* **Vanilla biome borders are smooth for the same reason.** Biomes are chosen as the nearest point in a continuous climate-parameter space (temperature, humidity, continentalness, erosion, weirdness — all smooth noise fields). A border is where two Voronoi cells of that space meet, pulled back through smooth fields: a wandering curve, no squares, no straight lines. Small noise scales = small biomes with busy borders; the parameter scales (default xz 0.25) keep features a few hundred blocks across.

### 3.2 Streams: source → downhill → join → mouth, with the height known along the way

Streams builds rivers that **originate at multiple sources on high ground, flow down the terrain through slopes and waterfalls, and join together into wider rivers until they reach a body of water at sea level**. To make that tractable it partitions the world into 16 × 16-chunk zones with one river per zone, computes the path *with height tracking* inside the zone (drain-to-source), and merges results at the borders. The transferable ideas are not the zone trick but the invariants: **a river has sources, a downhill direction, joins that increase its width, and a mouth** — and its water surface is derived along the path, so a drop becomes a waterfall instead of a staircase.

### 3.3 The TFC-TNG / AlcatrazEscapee analysis: coarse network first, fractal detail second

The survey notes that at continental scale you cannot track height per block along a path; TFC-TNG therefore generates river segments **without height on a very large scale**, then refines them with **midpoint-bisection fractal** passes so they look self-similar at every zoom. Lesson for us: compute the *network* coarse (where does water go?), refine the *geometry* fine (how does it meander?), and only then decide water levels. Our model already works the same way — analytic fields with a coarse sampling lattice — so the network belongs on a lattice too.

### 3.4 Dynamic Waters: the network is planned against the heightmap, then carved as part of worldgen

Its own description matches what the screenshots ask for: rivers "shape themselves into natural paths through the landscape **based on elevation**", "smaller mountain streams generate by **tracking terrain heightmaps from sea level up into the hills**", branching networks and deltas, and the paths are calculated in the background **during early world generation** (not lazily per chunk). Rivers are carved into the terrain as worldgen features rather than being random water spawns. The current/boat behaviour needs custom blocks and is out of scope for us; the **planning stage** is the lesson.

### 3.5 Tectonic: river geometry lives in the terrain field itself

Tectonic's surface rivers are vanilla's parameter rivers with widened, smoothed valleys (its `overworld/spline/offset.json` re-works the offset spline with blend functions); its extra rivers (underground: `overworld/underground_river/*`, lava: `overworld/lava_rivers.json`) are **shapes in the same density functions as the terrain** — e.g. an underground river is a band of its `ridges` field (`range_choice` on ridges ∈ [−0.07, 0.07]) turned into a 3D tube, blended with `min`/`max` clamps. Because the carve and the terrain are one function, they cannot desync: no perched troughs, no flood outside the tube. The lesson is architectural: **the water shape must be a function of the same fields that made the ground**, or at least be computed in the same pass over the same lattice.

### 3.6 TerraBlender and your four blending suggestions

Your suggestions were: overlap the noise ranges, group biomes by climate with buffer biomes, make biomes bigger, and use TerraBlender. Studying them honestly:

1. **Overlap the ranges** — correct and central. Vanilla's nearest-parameter selection *is* an overlap: every biome's parameter point is live everywhere; the border is the tie-break surface. What I need is to stop using exclusive threshold walls and start using this geometry. **Adopted (§5).**
2. **Climate grouping with buffers** — correct. The Whittaker grid already orders our open-land roles by climate, but the **landmark regions** drop cherry grove next to frozen peaks with no buffer. **Adopted: the seam feathers through a climate-intermediate role (§5).**
3. **Bigger biomes** — partially right, and it explains part of what you see: our fields are continental in scale, so the boxiness is *not* small-biome pixelation; it is the walls and squares of M7. Borders will still get "more distance to smooth out" because the warped level sets replace straight lines. **Understood differently (§5).**
4. **TerraBlender** — the right tool for a different problem. TerraBlender makes *added* biomes join vanilla's overworld biome builder so they inherit vanilla's parameter math. A Vantraya world does not use the vanilla builder at all (it has its own biome source), so TerraBlender cannot be bolted on; what we do instead is **implement vanilla's method directly** in our classifier — the same continuous parameter space and nearest-point rule that TerraBlender would have given us.

---

## 4. The design for the river system (v3)

One principle replaces M1–M6: **water follows a drainage network computed from the terrain, and its surface is one continuous function of distance along that network.** Three stages, all deterministic per world seed, all evaluated from the model's height field — the same fields the chunk generator turns into blocks, so nothing can desync (Tectonic's lesson).

### 4.1 Stage 1 — the drainage lattice (Streams' invariants, TFC's coarse-first rule)

A fixed lattice (32 blocks over the canvas) is computed once per world seed and cached inside `VantrayaModel`:

1. **Depressionless terrain.** Fill closed basins on the lattice to their spill level (priority-flood). This yields, in one pass, every lake's polygon and its surface Y — M5's noise blobs are gone, and "water in a basin" means *the* basin, filled to *its* spillway.
2. **D8 flow.** Each cell drains to its lowest neighbour (tie-broken by warped noise so channels meander); accumulate upstream cell counts. Accumulation is the river's size: heads where accumulation first exceeds a threshold (springs can start high — down to Y ≈ 220 — but tiny), width growing as `log(accumulation)` toward the mouth. Rivers **join and widen** (Streams' rule); nothing "fades out" mid-run — a channel ends only at its head or by merging into a bigger one (M2 fixed). Because the network follows the depressions of the terrain, it can **never cross a ridge or cut a peak** (your "synchronise with continentalness and erosion" point, enforced by construction).
3. **Monotone water profile.** Walk each network from its mouth upward and assign `waterY` as a non-increasing function downstream (`waterY ≤ waterY_downstream + gentle drop`), smoothed along the path. Where terrain forces a step, the step is a defined **drop of 1–2 blocks at a known place** (a rapid or a small waterfall) — never the staircase of M1. The mouth is the sea, a lake surface, or another river's profile — never empty air.

### 4.2 Stage 2 — carving the channel (Tectonic's "one field" rule)

From the lattice, every column reads three smoothly interpolated values: distance to the nearest channel centreline, that channel's `waterY`, and its width. The carve is a **cross-section**, not a notch:

```
bed   = waterY − depth(width)                 // 2–4 blocks under the surface
banks = smooth rise over 8–16 blocks           // your "widen and smooth the carver splines"
```

`dem = min(dem, cross-section(dist))`, with the 3D roughness noise switched off inside the channel so the bed is smooth. The notch is at least three engine grid cells wide at full depth (M4), and the bank slope is a smoothstep, so no cookie-cutter walls (M6). Valley sides get the wide U from the old design — but now centred on a real centreline and bounded by it.

### 4.3 Stage 3 — water placement

`RiverWater` v3 fills each column to the **interpolated `waterY` of the channel reach** (one surface per reach), only inside the cross-section — flooding outside a channel becomes structurally impossible (M3). Lakes fill to their spill level from Stage 1. Cold tiers freeze the top blocks. Deltas appear where wide mouths meet the sea (width from accumulation). Source springs get a small basin at the head. The caldera stays dry, the Veil untouched, landmark pins protected by the existing `protect` field.

### 4.4 What the tests must ask this time (M8)

* Along every sampled channel: **waterY strictly decreases downstream** (no uphill water), and is continuous except at declared drops of ≤ 2 blocks.
* No water column anywhere outside a channel cross-section or lake polygon (the anti-flood test).
* Every channel head sits in a catchment with accumulation below it; every tail is a mouth (sea, lake, or a bigger channel) — **no mid-slope endings**.
* Channel width ≥ 3 engine grid cells wherever waterY is set.
* In-engine, on several seeds: no water above the waterline of its reach; no dry gap inside a channel; lake surfaces flat at their spill height.
* And the acceptance run is your screenshots again: the same viewpoints, judged by eye.

### 4.5 Determinism, cost, and what stays

The lattice is computed once per world seed and sampled per column like the rest of the model — same determinism contract as today (several worlds can coexist in one JVM). Priority-flood + D8 over an 8,000 × 8,000 canvas at 32-block cells is 250 × 250 cells: negligible next to chunk generation, cached lazily on first use. The specification's pins, elevation bands, climate tiers, surface table, and its own river *algorithm requirements* (HANDOFF §5.10: wide U-shaped valleys along R ≈ 0, network reaching the sea) are honoured — what changes is that the runtime hydrology becomes real drainage instead of the local stand-in, which `docs/SPEC_NOTES.md` §C already flags as a compromise.

---

## 5. The design for biome borders (v3)

The rule from vanilla (§3.1): **a border is a level set of smooth fields in a continuous parameter space — it can wander, but it can never be a wall.** The v3 classifier:

1. **Nearest-role selection in climate space.** Every role gets a parameter point — the centroid of its Whittaker region in (temperature, humidity, elevation). A column's role is the nearest point, like vanilla's `MultiNoiseBiomeSource`. The decision surfaces are then smooth mid-surfaces between points — curved, wandering borders at every scale. Your suggestion 1, done properly.
2. **Domain-warped parameters.** The (T, H, height) query is warped by ~100-block coherent noise before the lookup, so borders undulate at all scales instead of following one smooth contour (the vanilla "hitch"). Protected from wobble at landmark centres, as before, so the spec's pins stay exact.
3. **No patches.** `BLEND_PATCH` and `patchTakes` are deleted (M7). Where two roles genuinely interleave, it will be along the warped boundary band — an ecotone, not a checkerboard.
4. **Landmark seams feather through a buffer.** The region override keeps its hard core (the specification's landmarks override the classifier outright) but its edge uses the existing `windows()` feather: across the feather band the two sides mix by warped noise probability, and — your suggestion 2 — the mix passes through the **climate-nearest intermediate role** (meadow between cherry grove and frozen peaks), never two extremes side by side. The rectangular wall of S9 becomes a natural ecotone that follows the feather's curved shape.
5. **Size** (your suggestion 3): our fields are already continental; borders will look smoother because their *shape* stops being walls, not because biomes got bigger.

Multi-biome role tags (Still Life's biomes later joining a role) keep the patch idea but with **warped, boundary-following selection** instead of square cells.

---

## 6. Implementation order

| P | Work | Fixes |
|---|---|---|
| P1 | Drainage lattice + monotone profile + cross-section carve + `RiverWater` v3 | S1, S2, S3, S4, S5, S6 |
| P2 | Classifier v3: nearest-role in warped climate space; delete patches; feathered seams with buffer role | S7 |
| P3 | The §4.4 test battery + re-run the same viewpoints | M8 |
| P4 | Docs, version 0.1.3, jar | — |

P1 and P2 are independent and can land together. Nothing here changes the specification's pins, the climate tiers, the surface table, or the structure policy. The two designs above are what I intend to build; if you want a different balance — say, larger rivers, more lakes, harder landmark edges — say so and I'll adjust before P1 goes in.
