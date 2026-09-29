"""The application server.

Strictly standard library ``http.server`` - no Flask, no uvicorn, nothing to install.
That is what makes the whole thing shippable as a single .exe: the desktop build starts
this server on a loopback port and opens a native window (or the default browser) at it.

Routes
------
``GET  /``                     the single page app
``GET  /static/<file>``        JS/CSS/three.js
``GET  /api/config``           default config + presets + block palette
``POST /api/config``           validate a config and report warnings
``POST /api/generate``         start a generation job
``GET  /api/jobs``             job list
``GET  /api/job/<id>``         one job's status
``POST /api/cancel/<id>``      cancel a job
``GET  /api/mesh``             the 3D preview payload for the last generation
``GET  /api/maps/<kind>.png``  a rendered raster of the last generation
``GET  /api/inspect``          column inspector at world x/z
``POST /api/export``           write the world to disk
``GET  /api/export/preview``   where the export would land
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import posixpath
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from ..config import GenerationConfig, default_config, preset_list, validate
from ..core.materials import BLOCKS
from ..core.types import BIOMES, BIOME_COLORS
from .jobs import JobManager
from .views import build_mesh, column_info, map_raster, png_bytes

def _static_dir() -> str:
    """UI assets live next to this module; in a frozen bundle look in _MEIPASS too."""
    candidates = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, "mg", "server", "static"))
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return candidates[0]


STATIC_DIR = _static_dir()


class Api:
    """State shared by every request handler."""

    def __init__(self) -> None:
        self.jobs = JobManager()
        self.config: GenerationConfig = default_config()
        self._mesh_cache: Dict[Tuple, Dict[str, Any]] = {}

    # -- config ---------------------------------------------------------------------------
    def config_payload(self) -> Dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "presets": preset_list(),
            "blocks": BLOCKS,
            "biomes": [
                {"name": n, "color": list(BIOME_COLORS.get(n, (128, 128, 128)))}
                for n in BIOMES
            ],
            "defaults": default_config().to_dict(),
        }

    def mesh(self, **kw) -> Dict[str, Any]:
        if self.jobs.last_generation is None:
            raise RuntimeError("nothing generated yet")
        key = (kw.get("size"), kw.get("exaggeration"), kw.get("show_water"), kw.get("show_biomes"))
        if key in self._mesh_cache:
            return self._mesh_cache[key]
        payload = build_mesh(self.jobs.last_generation.terrain, **kw)
        if len(self._mesh_cache) > 6:
            self._mesh_cache.clear()
        self._mesh_cache[key] = payload
        return payload


API = Api()


class Handler(BaseHTTPRequestHandler):
    server_version = "mapgen/1.0"
    protocol_version = "HTTP/1.1"

    # ---- plumbing -----------------------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # keep the console readable
        if os.environ.get("MAPGEN_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, content_type: str,
              extra_headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # allow the sandboxed preview host to embed the UI
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, allow_nan=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, message: str, code: int = 400) -> None:
        self._json({"error": str(message)}, code=code)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    # ---- routes -------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/config":
                return self._json(API.config_payload())
            if path == "/api/jobs":
                return self._json(API.jobs.list())
            if path.startswith("/api/job/"):
                job = API.jobs.get(path.rsplit("/", 1)[-1])
                if not job:
                    return self._error("no such job", 404)
                return self._json(job.to_dict(include_result=True))
            if path == "/api/mesh":
                payload = API.mesh(
                    size=int(query.get("size", ["192"])[0]),
                    exaggeration=float(query.get("exaggeration", ["1"])[0]),
                    show_water=query.get("water", ["1"])[0] != "0",
                    show_biomes=query.get("biomes", ["1"])[0] != "0",
                )
                return self._json(payload)
            if path.startswith("/api/maps/"):
                kind = path[len("/api/maps/"):].replace(".png", "")
                terrain = API.jobs.last_generation.terrain if API.jobs.last_generation else None
                if terrain is None:
                    return self._error("nothing generated yet", 409)
                size = int(query.get("size", ["1024"])[0])
                img = map_raster(terrain, kind, size=size)
                return self._send(200, png_bytes(img), "image/png")
            if path == "/api/inspect":
                terrain = API.jobs.last_generation.terrain if API.jobs.last_generation else None
                if terrain is None:
                    return self._error("nothing generated yet", 409)
                x = float(query.get("x", ["0"])[0])
                z = float(query.get("z", ["0"])[0])
                return self._json(column_info(terrain, x, z))
            if path.startswith("/api/preset/"):
                from ..config import apply_preset

                preset = path.rsplit("/", 1)[-1]
                cfg = apply_preset(default_config(), preset)
                return self._json({
                    "config": cfg.to_dict(),
                    "warnings": validate(cfg),
                })
            if path == "/api/health":
                return self._json({"ok": True, "generated": API.jobs.last_generation is not None})
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            return self._error("not found", 404)
        except Exception as exc:  # pragma: no cover
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/config":
                data = self._body()
                cfg = GenerationConfig.from_dict(data.get("config", data))
                warnings = validate(cfg)
                return self._json({"config": cfg.to_dict(), "warnings": warnings})
            if path == "/api/generate":
                data = self._body()
                cfg = GenerationConfig.from_dict(data.get("config", data))
                API.config = cfg
                if API.jobs.active.get("generate"):
                    return self._error("a generation job is already running", 409)
                job = API.jobs.submit_generation(
                    cfg, include_population=bool(data.get("population", True))
                )
                API._mesh_cache.clear()
                return self._json(job.to_dict(), 202)
            if path.startswith("/api/cancel/"):
                ok = API.jobs.cancel(path.rsplit("/", 1)[-1])
                return self._json({"cancelled": ok})
            if path == "/api/export":
                data = self._body()
                output_dir = str(data.get("output_dir") or "").strip()
                if not output_dir:
                    return self._error("output_dir is required")
                if API.jobs.active.get("export"):
                    return self._error("an export job is already running", 409)
                job = API.jobs.submit_export(output_dir, data.get("options") or {})
                return self._json(job.to_dict(), 202)
            return self._error("not found", 404)
        except RuntimeError as exc:
            return self._error(str(exc), 409)
        except Exception as exc:  # pragma: no cover
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    # ---- static files --------------------------------------------------------------------
    def _static(self, rel: str) -> None:
        rel = posixpath.normpath("/" + rel).lstrip("/")
        full = os.path.join(STATIC_DIR, rel)
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)):
            return self._error("forbidden", 403)
        if not os.path.isfile(full):
            return self._error("not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype)


def _free_port(preferred: int = 8765) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 40)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def create_server(port: int = 8765, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(port: Optional[int] = None, host: str = "0.0.0.0", *, open_browser: bool = True):
    """Run the server forever (blocking).  Returns the port actually bound."""
    port = port or _free_port()
    server = create_server(port, host)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"mapgen server listening on {host}:{server.server_address[1]}  ({url})")
    if open_browser:
        threading.Timer(0.8, lambda: _open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return server.server_address[1]


def _open(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # pragma: no cover
        pass


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="mapgen preview server")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    serve(args.port, args.host, open_browser=not args.no_browser)
