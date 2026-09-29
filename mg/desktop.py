"""Desktop entry point — this is what the .exe runs.

Startup order:

1. pick a free port and start the local server in a background thread;
2. try to open a **native window** (``pywebview``) so the app looks like a real desktop
   program rather than a browser tab;
3. if no GUI toolkit is available (or ``--browser`` was passed) fall back to the system
   default browser;
4. if neither works, print the URL so it can be opened by hand.

Nothing here is required to *use* the generator — ``python -m mg.server.app`` gives the
same experience in a browser — this module only wraps it in a window.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time

from .config import default_config
from .server.app import create_server


def _port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def start_server_background(port: int, host: str = "0.0.0.0"):
    """Start the HTTP server on a daemon thread; returns ``(server, port)``."""
    server = create_server(port, host)
    actual = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="mapgen-http", daemon=True)
    thread.start()
    for _ in range(50):  # wait until it actually accepts connections
        if _port_is_open(actual):
            break
        time.sleep(0.05)
    return server, actual


def run_native_window(url: str, title: str = "mapgen — terrain studio") -> bool:
    """Open a native window if pywebview is installed.  Returns True on success."""
    try:
        import webview  # type: ignore
    except Exception:
        return False
    try:
        window = webview.create_window(title, url, width=1600, height=980,
                                       min_size=(1100, 700), background_color="#0e1116")
        webview.start(debug=False)
        return True
    except Exception as exc:  # pragma: no cover - platform dependent
        print(f"native window unavailable ({exc}); falling back to the browser")
        return False


VERSION = "1.0"


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="mapgen", description="Terrain studio for Minecraft")
    parser.add_argument("--version", action="version",
                        version=f"mapgen {VERSION} (python {sys.version.split()[0]})")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MAPGEN_PORT", 8765)))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--browser", action="store_true", help="always use the browser")
    parser.add_argument("--no-window", action="store_true", help="never open a window")
    parser.add_argument("--serve-only", action="store_true",
                        help="just run the server (no window, no browser)")
    args = parser.parse_args(argv)

    try:
        server, port = start_server_background(args.port, args.host)
    except OSError as exc:
        if "--port" in raw or "-p" in raw:
            print(f"cannot listen on port {args.port}: {exc}", file=sys.stderr)
            return 2
        print(f"port {args.port} is busy ({exc}); picking a free one")
        server, port = start_server_background(0, args.host)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 66)
    print(f"  mapgen {VERSION} — World Machine / World Painter / Streams style terrain studio")
    print("=" * 66)
    print(f"  server : {args.host}:{port}")
    print(f"  open   : {url}")
    print("  stop   : close the window, or press Ctrl+C in this console")
    print()

    if args.serve_only:
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        return 0

    if not args.no_window and not args.browser:
        if run_native_window(url):
            try:
                server.shutdown()
            except Exception:
                pass
            return 0

    if not args.no_window:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    # keep the console alive so the server keeps serving
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
