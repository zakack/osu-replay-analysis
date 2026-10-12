"""Fetch the beatmaps listed in build/missing-beatmaps.csv from community mirrors.

Mirrors only -- osu.direct first, catboy.best as fallback -- never ppy's own endpoints.
Only the `.osu` is fetched, not the `.osz`: analysis needs the geometry, and the audio and
images are exactly what this project never holds.

A mirror serves the map's *current* version. If it was updated after the replays were set,
its md5 will not match the one they reference, and those replays are judged against a
different beatmap. So every file is hashed on arrival; a mismatch is kept, aside, and
reported, never filed under the md5 it does not have.

Files land as build/mirror-beatmaps/<md5>.osu, and build/mirror-index.json carries the
same shape as build/beatmap-index.json so the two can be merged by whatever reads them.
Resumable: a file already on disk with the right hash is not fetched again.

    python3 tools/reference/mirror.py [--csv build/missing-beatmaps.csv] [--workers 4]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "build", "mirror-beatmaps")
INDEX = os.path.join(ROOT, "build", "mirror-index.json")
UA = "osu-replay-analysis/0.1 (non-commercial replay research)"
MIRRORS = ["https://osu.direct/api/osu/{id}", "https://catboy.best/osu/{id}"]


def get(url: str) -> bytes | None:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt * 2)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(2 ** attempt)
    return None


def fetch(row: dict) -> tuple[dict, str, str | None]:
    md5, bid = row["md5"], row["beatmapId"]
    path = os.path.join(OUT, f"{md5}.osu")
    if os.path.exists(path):
        return row, "held", path
    stale = None
    for template in MIRRORS:
        body = get(template.format(id=bid))
        if not body or not body.lstrip(b"\xef\xbb\xbf").startswith(b"osu file format"):
            continue
        got = hashlib.md5(body).hexdigest()
        if got == md5:
            with open(path + ".part", "wb") as f:
                f.write(body)
            os.replace(path + ".part", path)
            return row, "ok", path
        stale = os.path.join(OUT, "stale", f"{bid}-{got}.osu")
        with open(stale, "wb") as f:
            f.write(body)
    return row, ("stale" if stale else "failed"), stale


def header(path: str) -> dict:
    meta = {}
    for line in open(path, encoding="utf-8-sig", errors="replace"):
        if line.startswith("[") and meta.get("_in") and not line.startswith("[Metadata]"):
            break
        if line.startswith("[Metadata]"):
            meta["_in"] = True
        elif meta.get("_in") and ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="build/missing-beatmaps.csv")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    os.makedirs(os.path.join(OUT, "stale"), exist_ok=True)
    rows = [r for r in csv.DictReader(open(os.path.join(ROOT, args.csv))) if r["beatmapId"]]
    index = json.load(open(INDEX)) if os.path.exists(INDEX) else {}
    outcome: dict[str, list[dict]] = {"ok": [], "held": [], "stale": [], "failed": []}

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for n, (row, status, path) in enumerate(pool.map(fetch, rows), 1):
            outcome[status].append(row)
            if status in ("ok", "held"):
                m = header(path)
                index[row["md5"]] = {
                    "Md5": row["md5"], "Sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
                    "Path": path, "Artist": m.get("Artist", ""), "Title": m.get("Title", ""),
                    "Difficulty": m.get("Version", ""), "OnlineId": int(row["beatmapId"]),
                }
            if n % 50 == 0:
                print(f"  {n}/{len(rows)}", file=sys.stderr)

    with open(INDEX, "w") as f:
        json.dump(index, f, indent=1)

    worth = lambda rs: sum(int(r["replays"]) for r in rs)
    for k, rs in outcome.items():
        print(f"{k:>7}: {len(rs):>4} maps, {worth(rs):>5,} replays")
    for r in outcome["stale"] + outcome["failed"]:
        print(f"  not matched: {r['beatmapId']:>9} {r['md5']} ({r['replays']} replays) {r['title'][:50]}")
    print(f"\nindex: {os.path.relpath(INDEX, ROOT)} ({len(index):,} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
