#!/usr/bin/env python3
"""Regenerates the vanilla spline stack that the "Vantraya" world type runs on, from vanilla 1.21.1 data.

Since 0.3.0 the terrain is vanilla's own Perlin-noise spline pipeline (offset / factor / jaggedness /
depth / sloped_cheese / base_3d_noise / ridges_folded), kept as data under
``src/main/resources/data/vantraya_builder/worldgen/density_function/`` with the splines' coordinates
pointed at the specification's parameter maps (``vantraya_builder:field/*``). This script performs that
copy: it rewrites the ``minecraft:overworld/...`` references to the Vantraya namespace and writes the
files. The noise settings (``worldgen/noise_settings/vantraya.json``) keep their hand-tuned deviations
(the landmark cave-protection window, the higher peak fade) and are not overwritten.

Usage::

    git clone --depth 1 --branch 1.21.1-data https://github.com/misode/mcmeta.git /tmp/mcmeta-1.21.1
    python3 scripts/generate_data.py /tmp/mcmeta-1.21.1/data/minecraft

The generated files are committed, so building the mod does not need Python or the vanilla data.
"""
import json
import os
import sys

MOD = "vantraya_builder"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "main", "resources")
DST = os.path.join(ROOT, "data", MOD, "worldgen", "density_function")

REFMAP = {
    "minecraft:overworld/continents": "vantraya_builder:field/continents",
    "minecraft:overworld/erosion": "vantraya_builder:field/erosion",
    "minecraft:overworld/ridges_folded": "vantraya_builder:field/ridges_folded",
    "minecraft:overworld/ridges": "vantraya_builder:field/ridges",
    "minecraft:overworld/offset": "vantraya_builder:offset",
    "minecraft:overworld/factor": "vantraya_builder:factor",
    "minecraft:overworld/jaggedness": "vantraya_builder:jaggedness",
    "minecraft:overworld/depth": "vantraya_builder:depth",
    "minecraft:overworld/sloped_cheese": "vantraya_builder:sloped_cheese",
    "minecraft:overworld/base_3d_noise": "vantraya_builder:base_3d_noise",
}

# file name in the vanilla data -> output name in the mod
FILES = {
    "offset": "offset",
    "factor": "factor",
    "jaggedness": "jaggedness",
    "depth": "depth",
    "sloped_cheese": "sloped_cheese",
    "ridges_folded": "field/ridges_folded",
    "base_3d_noise": "base_3d_noise",
}


def rewrite(obj):
    if isinstance(obj, dict):
        return {k: rewrite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rewrite(v) for v in obj]
    if isinstance(obj, str):
        return REFMAP.get(obj, obj)
    return obj


def main(vanilla_dir: str) -> None:
    src = os.path.join(vanilla_dir, "worldgen", "density_function", "overworld")
    for name, out in FILES.items():
        with open(os.path.join(src, name + ".json")) as fh:
            data = rewrite(json.load(fh))
        path = os.path.join(DST, out + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        leftover = [k for k in REFMAP if k in json.dumps(data)]
        print(f"{out}.json: written" + (f"  LEFTOVER REFS {leftover}" if leftover else ""))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
