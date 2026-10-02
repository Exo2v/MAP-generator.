"""Turn the game's own world decoration on or off in an exported save.

The exporter writes ``Data.WorldGenSettings.generate_features`` to match the decoration
mode: ``mods`` (the Ashenfall default) writes 0 so Lithosphere and Still Life place their
own features, ``engine`` writes 1 so the chunk is finished in-engine.  This tool flips the
flag in a save that already exists, without touching a single chunk.

    python3 -m mg.tools.set_world_decoration ~/.minecraft/saves/Ashenfall --mods
    python3 -m mg.tools.set_world_decoration ~/.minecraft/saves/Ashenfall --vanilla

``level.dat_old`` is kept in step, and the new file is parsed back before it is written, so
a failed patch leaves the save exactly as it was.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mg.export.nbt import read_nbt  # noqa: E402


def read_flag(level_dat: str) -> Optional[int]:
    with open(level_dat, "rb") as fh:
        data = fh.read()
    parsed = read_nbt(data)
    try:
        return int(parsed["value"]["Data"][1]["WorldGenSettings"][1]["generate_features"][1])
    except (KeyError, TypeError):
        return None


def patch_level_dat(level_dat: str, value: int) -> bool:
    """Rewrite ``generate_features`` in place.  Returns False if the key is absent."""
    with open(level_dat, "rb") as fh:
        raw = fh.read()
    body = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw

    name = b"generate_features"
    marker = len(name).to_bytes(2, "big") + name
    index = body.find(marker)
    if index < 0:
        return False
    at = index + len(marker)
    if at >= len(body):
        return False
    patched = bytearray(body)
    patched[at] = value & 0xFF

    out = bytes(patched)
    check = read_nbt(out)["value"]["Data"][1]["WorldGenSettings"][1]["generate_features"][1]
    if int(check) != (value & 0xFF):
        return False                      # refuse to write something we cannot read back
    with open(level_dat, "wb") as fh:
        fh.write(gzip.compress(out, compresslevel=6, mtime=0))
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="set_world_decoration",
                                 description="Flip generate_features in a saved world.")
    ap.add_argument("world", help="the save directory (holds level.dat)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--mods", action="store_true",
                       help="generate_features = 0: leave chunks to the mods (Still Life)")
    group.add_argument("--vanilla", action="store_true",
                       help="generate_features = 1: let the game decorate the chunks")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args(argv)

    world = os.path.abspath(args.world)
    level = os.path.join(world, "level.dat")
    if not os.path.isfile(level):
        print(f"error: {level} not found", file=sys.stderr)
        return 2

    before = read_flag(level)
    if before is None:
        print("error: level.dat has no WorldGenSettings.generate_features", file=sys.stderr)
        return 2
    wanted = 0 if args.mods else 1
    who = "the mods (Still Life / Lithosphere)" if wanted == 0 else "vanilla decorators"
    print(f"{world}")
    print(f"  generate_features {before} -> {wanted}   ({who})")
    if before == wanted:
        print("  already correct, nothing to do")
        return 0
    if args.dry_run:
        print("  --dry-run: not written")
        return 0

    shutil.copy2(level, level + ".bak")
    if not patch_level_dat(level, wanted):
        shutil.move(level + ".bak", level)
        print("error: could not patch level.dat (left unchanged)", file=sys.stderr)
        return 3
    old = os.path.join(world, "level.dat_old")
    if os.path.isfile(old) and not patch_level_dat(old, wanted):
        shutil.copy2(level, old)          # keep the two copies consistent
    os.remove(level + ".bak")
    print(f"  written; backup removed (level.dat was parseable before and after)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
