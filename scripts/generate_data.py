#!/usr/bin/env python3
"""Regenerates the worldgen JSON of the "Vantraya" world type from vanilla Minecraft 1.21.1 data.

The Vantraya noise settings are vanilla's Overworld noise settings with exactly one idea changed: the terrain
is no longer a spline of noise (offset / factor / jaggedness) but the height field of the Ashenfall
specification, produced by the ``vantraya_builder:field`` density function (src/main/java/.../VantrayaField).
Everything else - caves, aquifers, ore veins, the surface rule, the bottom and top "slides" - is vanilla's,
so cave systems, ores and biome surfaces behave as players expect (and as other mods expect).

Usage::

    git clone --depth 1 --branch 1.21.1-data-json https://github.com/misode/mcmeta.git /tmp/mcmeta-1.21.1
    python3 scripts/generate_data.py /tmp/mcmeta-1.21.1/data/minecraft

The generated files are committed, so building the mod does not need Python or the vanilla data.
"""
import copy
import json
import os
import re
import sys

MOD = "vantraya_builder"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "main", "resources")
DATA = os.path.join(ROOT, "data", MOD)

# Terrain steepness of the density gradient below the surface (vanilla's "factor"). The final density near
# the surface is 4 * depth * FACTOR, so 3D noise of amplitude ~0.3 moves the surface by roughly
# 0.3 / (4 * 3 / 128) = 3 blocks - and by nothing at all near a landmark centre, where rough3d is 0.
FACTOR = 3.0

# Vanilla fades terrain out between Y=240 and Y=256; the Glacial Spine reaches Y=283, so the fade moves up.
TOP_SLIDE_FROM, TOP_SLIDE_TO = 296, 312


def write(path, obj):
    full = os.path.join(DATA, path) if not os.path.isabs(path) else path
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def field(channel):
    """A column-wise field of the specification model, evaluated once per 4x4 block quart."""
    return {
        "type": "minecraft:flat_cache",
        "argument": {
            "type": f"{MOD}:field",
            "channel": channel,
            "seed_noise": f"{MOD}:seed_probe",
        },
    }


def protect_landmark_centres(final_density):
    """Keep vanilla's cave entrances and noodle tunnels from opening the surface around a landmark centre.

    The specification gives every landmark centre an exact elevation (and a new world spawns at the Forgotten
    Coast's), but vanilla's ``min(sloped_cheese, 5 * entrances)`` carves from the surface down to ~17 blocks, and
    ``min(final_density, noodle)`` carves anywhere: the first in-engine run found a 17-block cave pit in the pinned top
    of the Hermit's Spire. Within Spec.PROTECT_RADIUS of every landmark centre the ``protect`` channel is 1, which
    lifts both terms far above any terrain density, so they cannot carve there. Caves underground (the other branch
    of the range choice) are untouched. Asserts that exactly one of each is found, so a change in vanilla's data
    fails the generator instead of silently dropping the guard.
    """
    guard = {"type": "minecraft:mul", "argument1": 1000.0, "argument2": f"{MOD}:field/protect"}
    hits = {"entrances": 0, "noodle": 0}

    def walk(node):
        if isinstance(node, dict):
            if (node.get("type") == "minecraft:mul" and node.get("argument1") == 5.0
                    and node.get("argument2") == "minecraft:overworld/caves/entrances"):
                hits["entrances"] += 1
                return {"type": "minecraft:add", "argument1": node, "argument2": copy.deepcopy(guard)}
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(final_density)
    assert out["type"] == "minecraft:min" and out["argument2"] == "minecraft:overworld/caves/noodle", out.get("argument2")
    out["argument2"] = {"type": "minecraft:add", "argument1": "minecraft:overworld/caves/noodle",
                        "argument2": copy.deepcopy(guard)}
    hits["noodle"] += 1
    assert hits == {"entrances": 1, "noodle": 1}, hits
    return out


def replace_ids(node, mapping):
    if isinstance(node, dict):
        return {k: replace_ids(v, mapping) for k, v in node.items()}
    if isinstance(node, list):
        return [replace_ids(v, mapping) for v in node]
    if isinstance(node, str):
        return mapping.get(node, node)
    return node


def retarget_top_slide(node):
    """Move vanilla's Y=240..256 terrain fade (the only 1 -> 0 gradient in the router) up to the new heights."""
    if isinstance(node, dict):
        if (node.get("type") == "minecraft:y_clamped_gradient" and node.get("from_y") == 240
                and node.get("to_y") == 256):
            node = dict(node, from_y=TOP_SLIDE_FROM, to_y=TOP_SLIDE_TO)
        return {k: retarget_top_slide(v) for k, v in node.items()}
    if isinstance(node, list):
        return [retarget_top_slide(v) for v in node]
    return node


def main(vanilla):
    with open(os.path.join(vanilla, "worldgen", "noise_settings", "overworld.json"), encoding="utf-8") as f:
        overworld = json.load(f)
    with open(os.path.join(vanilla, "dimension_type", "overworld.json"), encoding="utf-8") as f:
        dim_type = json.load(f)
    with open(os.path.join(vanilla, "worldgen", "density_function", "overworld", "base_3d_noise.json"),
              encoding="utf-8") as f:
        base_3d_noise = json.load(f)

    # ---- density functions ----------------------------------------------------------------
    for ch in ("continents", "erosion", "ridges", "temperature", "humidity", "height", "rough3d", "protect"):
        write(f"worldgen/density_function/field/{ch}.json", field(ch))

    # depth = (H + 0.5 - y) / 128: positive below the ground, zero at the surface, exactly the scale of vanilla's
    # depth (a Y gradient of -1/128 per block), so biome sources, aquifers and the cave thresholds keep working.
    write("worldgen/density_function/depth.json", {
        "type": "minecraft:add",
        "argument1": {
            "type": "minecraft:mul",
            "argument1": 0.0078125,
            "argument2": f"{MOD}:field/height",
        },
        "argument2": {
            "type": "minecraft:add",
            "argument1": 0.00390625,
            "argument2": {
                "type": "minecraft:mul",
                "argument1": -0.0078125,
                "argument2": "minecraft:y",
            },
        },
    })

    # The 3D roughness noise is vanilla's, copied into our own namespace on purpose: terrain overhauls
    # (Lithosphere, Tectonic, ...) may override minecraft:overworld/base_3d_noise, and that must not change
    # the roughness - and with it the pinned elevations - of this world type. (The cave functions are
    # deliberately NOT copied: referencing vanilla's lets a cave overhaul apply here too.)
    write("worldgen/density_function/base_3d_noise.json", base_3d_noise)

    # sloped_cheese: 4 * FACTOR * depth + 3D noise, with the 3D noise switched off by the model where a landmark's
    # elevation is pinned (rough3d = 0).
    #
    # Vanilla bends this line at the surface - 4 * quarter_negative(depth * factor) is four times flatter above the
    # ground than below it - so that noise cannot raise floating blobs. The bend costs exactness here: the engine
    # evaluates the density only at the corners of 4 x 4 x 8-block cells and interpolates linearly, and a linear
    # interpolation across a 4:1 bend crosses zero too high, by up to 24 f (1 - f) / (1 + 3 f) blocks, 2.6 at most.
    # The in-engine test measured exactly that: the specified 68 / 85 / 220 / 140 / 75 came out as 70 / 87 / 222 /
    # 142 / 78. A straight line is interpolated exactly, so the surface stays where the specification puts it
    # (above the ground the line is steeper than vanilla's, so noise now lifts the surface by less, not more).
    sloped_cheese = {
        "type": "minecraft:add",
        "argument1": {
            "type": "minecraft:mul",
            "argument1": 4.0 * FACTOR,
            "argument2": f"{MOD}:depth",
        },
        "argument2": {
            "type": "minecraft:mul",
            "argument1": f"{MOD}:field/rough3d",
            "argument2": f"{MOD}:base_3d_noise",
        },
    }
    write("worldgen/density_function/sloped_cheese.json", sloped_cheese)

    # ---- noise settings -----------------------------------------------------------------------
    ns = copy.deepcopy(overworld)
    router = ns["noise_router"]
    router["continents"] = f"{MOD}:field/continents"
    router["erosion"] = f"{MOD}:field/erosion"
    router["ridges"] = f"{MOD}:field/ridges"
    router["temperature"] = f"{MOD}:field/temperature"
    router["vegetation"] = f"{MOD}:field/humidity"
    router["depth"] = f"{MOD}:depth"

    mapping = {
        "minecraft:overworld/sloped_cheese": f"{MOD}:sloped_cheese",
        "minecraft:overworld/depth": f"{MOD}:depth",
    }
    router["final_density"] = protect_landmark_centres(retarget_top_slide(replace_ids(router["final_density"], mapping)))

    # initial_density_without_jaggedness: vanilla's formula with our depth and a constant factor
    initial = replace_ids(router["initial_density_without_jaggedness"], mapping)
    initial = retarget_top_slide(initial)

    def swap_factor(node):
        if isinstance(node, dict):
            if node.get("type") == "minecraft:cache_2d" and node.get("argument") == "minecraft:overworld/factor":
                return FACTOR
            return {k: swap_factor(v) for k, v in node.items()}
        if isinstance(node, list):
            return [swap_factor(v) for v in node]
        return node

    router["initial_density_without_jaggedness"] = swap_factor(initial)
    for dropped in ("minecraft:overworld/offset", "minecraft:overworld/factor", "minecraft:overworld/jaggedness"):
        assert dropped not in json.dumps(router), dropped

    # The Overworld fills open air up to Y=62 with sea_level 63; the specification's waterline is Y=62.
    assert ns["sea_level"] == 63
    write("worldgen/noise_settings/vantraya.json", ns)

    # ---- noise used to fingerprint the world seed ----------------------------------------------
    write("worldgen/noise/seed_probe.json", {"firstOctave": -3, "amplitudes": [1.0, 1.0, 0.5]})

    # ---- dimension type ---------------------------------------------------------------------------
    write("dimension_type/vantraya.json", dim_type)

    # ---- the world type shown on the world creation screen ---------------------------------------
    write("worldgen/world_preset/vantraya.json", {
        "dimensions": {
            "minecraft:overworld": {
                "type": f"{MOD}:vantraya",
                "generator": {
                    "type": f"{MOD}:vantraya",
                    "biome_source": {"type": f"{MOD}:vantraya"},
                    "settings": f"{MOD}:vantraya",
                },
            },
            "minecraft:the_end": {
                "type": "minecraft:the_end",
                "generator": {
                    "type": "minecraft:noise",
                    "biome_source": {"type": "minecraft:the_end"},
                    "settings": "minecraft:end",
                },
            },
            "minecraft:the_nether": {
                "type": "minecraft:the_nether",
                "generator": {
                    "type": "minecraft:noise",
                    "biome_source": {"type": "minecraft:multi_noise", "preset": "minecraft:nether"},
                    "settings": "minecraft:nether",
                },
            },
        }
    })
    # list it among the "normal" world types (Default, Superflat, Large Biomes, Amplified, ...)
    write(os.path.join(ROOT, "data", "minecraft", "tags", "worldgen", "world_preset", "normal.json"),
          {"values": [f"{MOD}:vantraya"]})

    # ---- biome role tags --------------------------------------------------------------------------
    # one tag per biome role; each holds just the vanilla default, other mods may add biomes to it
    roles_java = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "main", "java",
                              "io", "github", "exo2v", "vantraya", "core", "BiomeRole.java")
    with open(roles_java, encoding="utf-8") as f:
        ROLES = re.findall(r'^\s+[A-Z_]+\("([a-z_]+)", "([a-z_]+)"\)', f.read(), re.M)
    assert len(ROLES) >= 50, ROLES

    for role, vanilla_id in ROLES:
        write(f"tags/worldgen/biome/biome/{role}.json", {"values": [f"minecraft:{vanilla_id}"]})
    print("wrote", len(ROLES), "biome role tags")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
