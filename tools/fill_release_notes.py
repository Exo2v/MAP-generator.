#!/usr/bin/env python3
"""Fill the Ashenfall release notes with the real shard list, sizes and links.

The notes are written once, in ``release/ashenfall-world/RELEASE_NOTES.md``, with
placeholders; this script replaces them with what is actually on disk and on the branch, so
the published text can never disagree with the download.

    python3 tools/fill_release_notes.py --tag v1.1.0 \
        --repo Exo2v/MAP-generator. --branch arena/01a0ece3-map-generator \
        --out out/release_notes_v1.1.0.md
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

DEFAULT_TEMPLATE = os.path.join("release", "ashenfall-world", "RELEASE_NOTES.md")
REPO_SLUG = "Exo2v/MAP-generator."


def human(size: int) -> str:
    return f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{size / 1e3:.0f} KB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fill_release_notes.py")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--repo", default=REPO_SLUG)
    ap.add_argument("--branch", default="arena/01a0ece3-map-generator")
    ap.add_argument("--shards", default="release/ashenfall-world",
                    help="where the shards are on disk")
    ap.add_argument("--repo-path", default="release/ashenfall-world",
                    help="the same folder inside the repository, used for the links")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE)
    ap.add_argument("--out", required=True)
    ap.add_argument("--verify", default="", help="verifier output to paste in")
    ap.add_argument("--tests", default="", help="test summary to paste in")
    args = ap.parse_args(argv)

    shards = sorted(glob.glob(os.path.join(args.shards, "*.zip")))
    if not shards:
        print(f"error: no shards in {args.shards}", file=sys.stderr)
        return 2

    table = ["| shard | size | contents |", "| --- | --- | --- |"]
    links = []
    total = 0
    for path in shards:
        name = os.path.basename(path)
        size = os.path.getsize(path)
        total += size
        what = "metadata + region files" if "meta" in name else "region files"
        table.append(f"| `{name}` | {human(size)} | {what} |")
    table.append(f"| `SHA256SUMS.txt` | — | checksums |")
    table.append("")
    table.append(f"Total download: **{human(total)}**.")

    for path in shards:
        name = os.path.basename(path)
        url = (f"https://github.com/{args.repo}/blob/{args.branch}/"
               f"{args.repo_path}/{name}?raw=1")
        links.append(f"* [`{name}`]({url})")

    text = open(args.template, encoding="utf-8").read()
    text = text.replace("@@SHARDS@@", "\n".join(table))
    text = text.replace("@@LINKS@@", "\n".join(links))
    text = text.replace("@@VERIFY@@", args.verify.strip() or
                        "(run `mg.tools.verify_ashenfall_world` on the extracted save)")
    text = text.replace("@@TESTS@@", args.tests.strip())
    text = text.replace("@@REGIONS@@", os.environ.get("ASF_REGIONS", "250"))
    text = text.replace("@@CHUNKS@@", os.environ.get("ASF_CHUNKS", "250,000"))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {args.out}  ({len(shards)} shards, {human(total)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
