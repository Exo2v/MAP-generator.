"""The generation pipeline: climate -> terrain -> erosion -> water -> biomes -> population.

One call to :func:`run_pipeline` produces a fully populated :class:`TerrainGrid` plus
per-stage timings and diagnostics.  Every stage reports progress through a callback and
can be cancelled, which is what lets the desktop UI stay responsive while a 4 km region
is being built.
"""

from __future__ import annotations

import dataclasses
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .config import GenerationConfig, resolve_derived
from .core import erosion as erosion_mod
from .core.noise import grid_coords
from .core.types import GenerationResult, RegionInfo, TerrainGrid
from .generation.biomes import BiomeClassifier
from .generation.climate import ClimateModel
from .generation.population import PopulationModel
from .generation.terrain import TerrainGenerator
from .generation.water import WaterSystem

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class Cancelled(RuntimeError):
    """Raised when the caller cancels a run."""


@dataclass
class StageTimer:
    stages: List[Dict[str, Any]] = field(default_factory=list)

    def record(self, name: str, seconds: float, extra: Optional[Dict[str, Any]] = None):
        entry = {"stage": name, "seconds": round(seconds, 3)}
        if extra:
            entry.update(extra)
        self.stages.append(entry)


def run_pipeline(
    cfg: GenerationConfig,
    *,
    progress: Optional[ProgressFn] = None,
    should_cancel: Optional[CancelFn] = None,
    include_population: bool = True,
) -> GenerationResult:
    """Run every generation stage for ``cfg`` and return the finished terrain grid."""
    cfg = resolve_derived(cfg)
    region = cfg.region
    seed = int(cfg.seed)
    timer = StageTimer()

    def report(frac: float, label: str):
        if progress:
            progress(float(np.clip(frac, 0.0, 1.0)), label)

    def check():
        if should_cancel and should_cancel():
            raise Cancelled("generation cancelled")

    # ---- coordinate grid ---------------------------------------------------------------
    t0 = time.time()
    cells_x, cells_z = region.cells_x, region.cells_z
    X, Y = grid_coords(cells_x, cells_z, region.cell_size, (region.x0, region.z0))
    timer.record("grid", time.time() - t0, {"cells": cells_x * cells_z})
    report(0.02, "preparing grid")
    check()

    # ---- climate ------------------------------------------------------------------------
    t0 = time.time()
    climate = ClimateModel(cfg.to_dict(), region, seed).compute(X, Y)
    timer.record("climate", time.time() - t0)
    report(0.12, "climate")
    check()

    # ---- macro terrain -------------------------------------------------------------------
    # Two engines: the generic World-Machine style chain, and the specification-driven
    # Ashenfall builder that composes the continent of Vantyra from its tables.
    t0 = time.time()
    engine = str((cfg.terrain or {}).get("engine", "generic")).lower()
    no_water = None
    if engine == "ashenfall":
        from .generation.ashenfall import AshenfallBuilder

        ashen = AshenfallBuilder(cfg.to_dict(), region, seed).build(
            X, Y, climate.temperature, climate.humidity,
            progress=lambda f, l: report(0.12 + 0.16 * f, l),
        )
        heights = ashen.dem
        climate = dataclasses.replace(climate, temperature=ashen.temperature,
                                      humidity=ashen.humidity)
        masks = {
            "continentalness": ashen.cont.astype(np.float32),
            "ashenfall_erosion": ashen.erosion.astype(np.float32),
            "ashenfall_ridges": ashen.ridges.astype(np.float32),
            "ashenfall_ids": ashen.ids,
            "ashenfall_bare": ashen.bare.astype(np.float32),
            "ashenfall_lava": ashen.lava.astype(np.uint8),
        }
        # the caldera basin is a lava basin: water must not flood it
        no_water = ashen.dry
        masks["ashenfall_basin"] = ashen.dry.astype(np.uint8)
        extra_terrain = {"ashenfall": ashen.diagnostics}
    else:
        generator = TerrainGenerator(cfg.to_dict(), region, seed)
        heights, masks = generator.generate(X, Y, climate)
        extra_terrain = {}
    timer.record("terrain", time.time() - t0,
                 {"min": float(heights.min()), "max": float(heights.max()),
                  "engine": engine, **extra_terrain})
    report(0.28, "terrain")
    check()

    # ---- erosion + diffusion -------------------------------------------------------------
    er = cfg.erosion
    accum_erosion = None
    if er.get("enabled", True):
        t0 = time.time()
        before = heights.copy()

        def erosion_progress(frac, label):
            report(0.28 + 0.22 * frac, label)

        heights, accum_erosion = erosion_mod.hydraulic_erosion(
            heights,
            cell_size=float(region.cell_size),
            sea_level=float(cfg.climate.get("sea_level", 63.0)),
            thermal_iterations=int(er.get("thermal_iterations", 16)),
            thermal_rate=float(er.get("thermal_rate", 0.26)),
            diffusion=float(er.get("diffusion", 0.45)),
            sp_iterations=int(er.get("fluvial_iterations", 12)),
            sp_k=float(er.get("fluvial_k", 0.55)),
            sp_m=float(er.get("fluvial_m", 0.6)),
            sp_n=float(er.get("fluvial_n", 1.0)),
            max_incision=float(er.get("max_incision", 1.2)),
            droplets=int(er.get("droplets", 0)),
            droplet_seed=seed + 5,
            deposit=float(er.get("deposition", 0.5)),
            smooth_sigma=float(er.get("channel_smoothing", 1.1)),
            progress=erosion_progress,
        )
        stats = erosion_mod.erosion_stats(before, heights,
                                          accum_erosion if accum_erosion is not None
                                          else np.ones_like(heights))
        timer.record("erosion", time.time() - t0, stats)
        report(0.5, "erosion")
        check()

    # ---- water: rivers, lakes, flow --------------------------------------------------------
    t0 = time.time()
    water = WaterSystem(cfg.to_dict(), region, seed).build(
        heights, climate, progress=lambda f, l: report(0.5 + 0.2 * f, l),
        no_water=no_water,
    )
    heights = water.dem
    timer.record("water", time.time() - t0, water.diagnostics)
    report(0.7, "water")
    check()

    # ---- climate refinement (needs the finished heightfield) ---------------------------------
    t0 = time.time()
    climate = ClimateModel(cfg.to_dict(), region, seed).finalize(
        climate, heights, ocean=(water.water_mask == 3)
    )
    timer.record("climate_refine", time.time() - t0)
    report(0.74, "climate refinement")
    check()

    # ---- biomes + fertility + population field ---------------------------------------------
    t0 = time.time()
    biome_fields = BiomeClassifier(cfg.to_dict(), region, seed).compute(
        heights=heights,
        water_mask=water.water_mask,
        water_level=water.water_level,
        temperature=climate.temperature,
        humidity=climate.humidity,
        continentality=climate.continentality,
        climate_cfg=cfg.climate,
        volcano_mask=masks.get("volcano"),
        cell_size=float(region.cell_size),
    )
    if engine == "ashenfall":
        # spec §2: the landmark table names the biome of every region outright
        from .generation.ashenfall import landmark_biome_field

        masks["ashenfall_biome"] = landmark_biome_field(
            masks["ashenfall_ids"], heights, water.water_mask
        )
    timer.record("biomes", time.time() - t0)
    report(0.78, "biomes")
    check()

    # ---- population: vegetation, POIs, roads, ores ------------------------------------------
    pop = None
    if include_population:
        t0 = time.time()
        pop = PopulationModel(cfg.to_dict(), region, seed).build(
            heights=heights,
            biome=biome_fields.biome,
            population=biome_fields.population,
            fertility=biome_fields.fertility,
            water_mask=water.water_mask,
            temperature=climate.temperature,
            humidity=climate.humidity,
            sea_level=float(cfg.climate.get("sea_level", 63.0)),
            progress=lambda f, l: report(0.78 + 0.19 * f, l),
        )
        timer.record("population", time.time() - t0, pop.diagnostics)
    report(0.98, "assembling result")

    # ---- assemble ---------------------------------------------------------------------------
    cs = float(region.cell_size)
    soil_depth = np.clip(
        heights - erosion_mod.slope_map(heights, cs) * 6.0, 4.0, 64.0
    ).astype(np.float32)

    terrain = TerrainGrid(
        region=region,
        heights=heights.astype(np.float32),
        water_level=water.water_level.astype(np.float32),
        temperature=climate.temperature,
        humidity=climate.humidity,
        continentality=climate.continentality,
        fertility=biome_fields.fertility,
        population=biome_fields.population,
        biome=biome_fields.biome,
        soil_depth=soil_depth,
        flow_dir=water.flow,
        discharge=water.accum.astype(np.float32),
        water_mask=water.water_mask,
        rivers=water.rivers,
        lakes=water.lakes,
        pois=[] if pop is None else pop.pois,
        roads=[] if pop is None else pop.roads,
        vegetation=None if pop is None else pop.vegetation,
        species_table=[] if pop is None else pop.species_table,
        veins=[] if pop is None else pop.veins,
        meta={
            "snowline": biome_fields.snowline,
            "seed": seed,
            "masks": {k: v for k, v in masks.items()
                      if k in ("continentalness", "mountain_belt", "islands", "volcano")
                      or k.startswith("ashenfall_")},
            "sea_level": float(cfg.climate.get("sea_level", 63.0)),
            "waterfalls": water.waterfalls[:5000],
            "stage_times": timer.stages,
        },
    )

    result = GenerationResult(
        terrain=terrain,
        config=cfg.to_dict(),
        stages=timer.stages,
        diagnostics={
            "cells": [cells_x, cells_z],
            "cell_size": region.cell_size,
            "stats": terrain.stats(),
            "water": water.diagnostics,
            "population": {} if pop is None else pop.diagnostics,
        },
    )
    report(1.0, "done")
    return result


def run_pipeline_safe(cfg: GenerationConfig, **kw) -> GenerationResult:
    """Wrapper that turns unexpected exceptions into a readable message."""
    try:
        return run_pipeline(cfg, **kw)
    except Cancelled:
        raise
    except Exception as exc:  # pragma: no cover - surfaced to the UI
        raise RuntimeError(f"generation failed: {exc}\n{traceback.format_exc()}") from exc
