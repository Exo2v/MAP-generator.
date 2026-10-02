"""Build a single-file executable for Windows (and Linux/macOS) with PyInstaller.

    python tools/build_exe.py

Produces ``dist/mapgen.exe`` on Windows, ``dist/mapgen`` elsewhere.  The build bundles
the static UI files plus numpy/scipy/Pillow, and optionally ``pywebview`` so the exe
opens a native window instead of a browser tab.

Notes for whoever runs this next:
* ``--onefile`` is slower to start (it unpacks to a temp dir) but is what people expect
  from "an exe"; ``--onedir`` starts faster.  Both are offered.
* numpy/scipy ship their own DLLs; on Windows the ``--collect-all`` flags keep MKL/OpenBLAS
  from being missed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "mg", "server", "static")
ENTRY = os.path.join(ROOT, "tools", "mapgen_entry.py")


def ensure_entry_point() -> str:
    """PyInstaller needs a plain script as the entry point, not a package module."""
    os.makedirs(os.path.dirname(ENTRY), exist_ok=True)
    with open(ENTRY, "w", encoding="utf-8") as fh:
        fh.write(
            '"""Generated launcher used by tools/build_exe.py (safe to delete)."""\n'
            "import multiprocessing\n"
            "import os\n"
            "import sys\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    multiprocessing.freeze_support()\n"
            "    # make the bundled package importable when frozen\n"
            "    here = os.path.dirname(os.path.abspath(__file__))\n"
            "    if here not in sys.path:\n"
            "        sys.path.insert(0, here)\n"
            "    from mg.desktop import main\n"
            "    raise SystemExit(main())\n"
        )
    return ENTRY


def check_environment() -> List[str]:
    """Return a list of problems that will stop PyInstaller from producing a working exe."""
    problems: List[str] = []
    try:
        import PyInstaller  # noqa: F401
    except Exception:
        problems.append("PyInstaller is not installed "
                        "(python -m pip install pyinstaller)")

    if os.name != "nt":
        # PyInstaller links the bootloader against the shared libpython; Debian's
        # python3.11 only ships the static archive, which fails late and cryptically.
        import sysconfig

        shared = sysconfig.get_config_var("Py_ENABLE_SHARED")
        libdir = sysconfig.get_config_var("LIBDIR") or ""
        ldlib = sysconfig.get_config_var("LDLIBRARY") or ""
        if not shared or not os.path.exists(os.path.join(libdir, ldlib)):
            problems.append(
                f"this Python ({sys.executable}) has no shared libpython "
                f"({ldlib or 'libpython*.so'} missing from {libdir or '?'}); "
                "PyInstaller needs one - install the matching libpython package "
                "or use a python.org/pyenv/conda interpreter")
        problems.append(
            "PyInstaller cannot cross-compile: building mapgen.exe requires "
            "running this script on Windows")
    for mod in ("numpy", "PIL"):
        try:
            __import__(mod)
        except Exception:
            problems.append(f"missing runtime dependency: {mod}")
    if not os.path.isdir(STATIC) or not os.path.isfile(os.path.join(STATIC, "index.html")):
        problems.append(f"UI assets missing under {STATIC}")
    return problems


def check_entry_point() -> None:
    """Make sure the generated launcher is importable before freezing it."""
    entry = ensure_entry_point()
    proc = subprocess.run([sys.executable, entry, "--help"], cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"launcher {entry} failed --help:\n{proc.stdout}")


def build(onefile: bool = True, with_webview: bool = True, clean: bool = True) -> int:
    entry = ensure_entry_point()
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "mapgen",
        "--onefile" if onefile else "--onedir",
        "--console",
        "--collect-all", "numpy",
        "--collect-all", "scipy",
        "--collect-all", "PIL",
        "--hidden-import", "mg.desktop",
        "--hidden-import", "mg.server.app",
        "--exclude-module", "matplotlib",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        # the UI is served straight off disk, so it must be inside the bundle
        "--add-data", f"{STATIC}{os.pathsep}mg/server/static",
        entry,
    ]
    if with_webview:
        args[3:3] = ["--collect-all", "webview"]
        args[3:3] = ["--collect-all", "pywebview"]
        args[3:3] = ["--hidden-import", "webview"]
    if clean:
        for d in ("build", "dist"):
            shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)
        spec = os.path.join(ROOT, "mapgen.spec")
        if os.path.exists(spec):
            os.remove(spec)

    print("running:", " ".join(args[:8]), "...")
    proc = subprocess.run(args, cwd=ROOT)
    if proc.returncode != 0:
        print("PyInstaller failed", file=sys.stderr)
        return proc.returncode
    exe = os.path.join(ROOT, "dist", "mapgen.exe" if os.name == "nt" else "mapgen")
    print()
    print("=" * 60)
    print(f"built: {exe}")
    print("run it, then tune a world and hit Generate.")
    print("=" * 60)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the mapgen executable")
    ap.add_argument("--onedir", action="store_true", help="folder build (faster start)")
    ap.add_argument("--no-webview", action="store_true", help="skip the native window")
    ap.add_argument("--no-clean", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="run the pre-flight checks and exit without building")
    args = ap.parse_args()

    print(f"platform: {sys.platform} / {sys.version.split()[0]} / {sys.executable}")
    problems = check_environment()
    blocking = [p for p in problems if "cross-compile" not in p]
    if blocking:
        print("pre-flight problems:", file=sys.stderr)
        for p in blocking:
            print(f"  - {p}", file=sys.stderr)
        print(f"\nnote: {problems[-1] if problems else ''}", file=sys.stderr)
        return 2
    for p in problems:
        print(f"note: {p}")
    check_entry_point()
    print("pre-flight ok")
    if args.check:
        return 0
    return build(onefile=not args.onedir, with_webview=not args.no_webview,
                 clean=not args.no_clean)


if __name__ == "__main__":
    raise SystemExit(main())
