"""Background job manager.

Generation and export both run in worker threads so the HTTP server (and therefore the
3D viewport) stays responsive.  Jobs report progress, can be cancelled, and expose
their result once they are done.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ..config import GenerationConfig
from ..export.world import ExportResult, export_world
from ..generation.surface import TerrainSampler
from ..pipeline import Cancelled, run_pipeline


@dataclass
class Job:
    id: str
    kind: str  # generate | export
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    result: Any = None
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    config: Optional[Dict[str, Any]] = None

    def to_dict(self, *, include_result: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - self.started, 2),
        }
        if include_result and self.result is not None:
            data["result"] = self.result
        return data


class JobManager:
    """Tracks every job the UI started (at most one active per kind)."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._cancel: Dict[str, bool] = {}
        self.active: Dict[str, str] = {}
        #: cache of the last finished generation, reused by the mesh/map endpoints
        self.last_generation = None  # GenerationResult
        self.last_config: Optional[GenerationConfig] = None

    # ----------------------------------------------------------------------------------
    def submit_generation(self, cfg: GenerationConfig, *, include_population: bool = True) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind="generate", config=cfg.to_dict())
        with self._lock:
            self._jobs[job.id] = job
            self._cancel[job.id] = False
            self.active["generate"] = job.id

        def work():
            job.status = "running"
            try:
                result = run_pipeline(
                    cfg,
                    progress=lambda f, m: self._progress(job, f, m),
                    should_cancel=lambda: self._cancel.get(job.id, False),
                    include_population=include_population,
                )
                self.last_generation = result
                self.last_config = cfg
                job.result = {
                    "stats": result.terrain.stats(),
                    "stages": result.stages,
                    "diagnostics": result.diagnostics,
                    "config": result.config,
                }
                job.status = "done"
                job.progress = 1.0
                job.message = "generation complete"
            except Cancelled:
                job.status = "cancelled"
                job.message = "cancelled"
            except Exception as exc:  # pragma: no cover - reported to the UI
                job.status = "error"
                job.error = str(exc)
                job.message = "generation failed"
            finally:
                job.finished = time.time()
                with self._lock:
                    if self.active.get("generate") == job.id:
                        self.active.pop("generate", None)

        threading.Thread(target=work, name="mapgen-generate", daemon=True).start()
        return job

    # ----------------------------------------------------------------------------------
    def submit_export(self, output_dir: str, options: Optional[Dict[str, Any]] = None) -> Job:
        if self.last_generation is None or self.last_config is None:
            raise RuntimeError("generate a world before exporting")
        job = Job(id=uuid.uuid4().hex[:12], kind="export")
        options = options or {}
        with self._lock:
            self._jobs[job.id] = job
            self._cancel[job.id] = False
            self.active["export"] = job.id

        cfg = self.last_config
        result = self.last_generation

        def work():
            job.status = "running"
            try:
                config_dict = cfg.to_dict()
                export_cfg = config_dict.setdefault("export", {})
                export_cfg.update({k: v for k, v in options.items() if k in export_cfg}
                                  or {})
                for key in ("world_name", "version", "data_version", "compression",
                            "write_level_dat", "structures", "vegetation", "caves",
                            "chunk_batch", "min_y", "max_y"):
                    if key in options:
                        export_cfg[key] = options[key]
                sampler = TerrainSampler(
                    result.terrain, seed=cfg.seed, cfg=config_dict
                )
                out: ExportResult = export_world(
                    result.terrain,
                    config_dict,
                    output_dir,
                    seed=cfg.seed,
                    progress=lambda f, m: self._progress(job, f, m),
                    should_cancel=lambda: self._cancel.get(job.id, False),
                    sampler=sampler,
                )
                extras = []
                if options.get("generate_png_maps", True):
                    from .views import write_map_bundle

                    written = write_map_bundle(result.terrain, out.world_dir, config_dict)
                    extras.extend(written)
                if options.get("worldpainter_bundle", True):
                    from ..export.schematic import export_worldpainter_bundle

                    written = export_worldpainter_bundle(result.terrain, out.world_dir)
                    extras.extend(written)
                if options.get("write_schematic", False):
                    from ..export.schematic import export_schematic_tiles

                    extras.extend(export_schematic_tiles(
                        sampler,
                        os.path.join(out.world_dir, "schematic"),
                        tile=int(options.get("schematic_chunks", 4)),
                        limit=int(options.get("schematic_files", 12)),
                        version=str(options.get("version", "1.21")),
                        data_version=int(options.get("data_version", 3953)),
                    ))
                job.result = {
                    "world_dir": out.world_dir,
                    "region_files": out.region_files,
                    "chunks": out.chunks,
                    "blocks": out.blocks_written,
                    "seconds": round(out.seconds, 2),
                    "extras": extras,
                }
                job.status = "done"
                job.progress = 1.0
                job.message = "export complete"
            except Exception as exc:  # pragma: no cover
                job.status = "error"
                job.error = f"{exc}\n{traceback.format_exc()}"
                job.message = "export failed"
            finally:
                job.finished = time.time()
                with self._lock:
                    if self.active.get("export") == job.id:
                        self.active.pop("export", None)

        threading.Thread(target=work, name="mapgen-export", daemon=True).start()
        return job

    # ----------------------------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        if job_id in self._jobs:
            self._cancel[job_id] = True
            return True
        return False

    def list(self) -> Dict[str, Any]:
        return {
            "jobs": [j.to_dict() for j in self._jobs.values()],
            "active": dict(self.active),
        }

    # ----------------------------------------------------------------------------------
    @staticmethod
    def _progress(job: Job, frac: float, message: str) -> None:
        job.progress = float(frac)
        job.message = message
