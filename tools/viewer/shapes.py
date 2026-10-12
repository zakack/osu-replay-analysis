"""A contact sheet of the shapes the corpus actually contains, drawn from their objects.

The vocabulary comes out of `patterns.py` as symbol strings, which is the right form for
counting and a poor one for believing. This draws real instances so the spelling can be
checked against the thing it claims to describe: if `a1|s` repeated is a star, it should
look like a star.

Each tile is one instance's object positions, in playfield coordinates, centred and scaled
to its own box. No rotation and no reflection -- the point is to see the shape as it was
played, including whether a type's instances actually agree.

    PYTHONPATH=tools python3 tools/viewer/shapes.py --cache <cache.json> --out build/viewer/shapes.html
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis"))

import patterns as P  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# What to show, and what a player would call it. The names are presentation only -- nothing
# downstream reads them, and the charter keeps naming away from membership.
WANTED = [
    ("0.5|6:8|a1|s",     "Star / rosette", "sharp turns, all the same way round, 6-8 radii"),
    ("0.5|4.5:6|a1|s",   "Star, tighter", "the same figure at 4.5-6 radii"),
    ("0.5|4.5:6|a2|s",   "Square / circle jumps", "right-angle turns, all the same way round"),
    ("0.25|1.5:2|a2|s",  "Small square", "right angles at stream spacing"),
    ("0.5|6:8|a1|o",     "Zigzag jumps", "the same sharp turns, alternating -- not a star"),
    ("0.25|0:1|a2|o",    "Right-angle back-and-forth", "alternating, nearly stacked"),
    ("0.25|1:1.5|a3|s",  "Curved stream", "gentle turns, one way round"),
    ("0.25|0:1|a4|s",    "Straight stream", "no turn to speak of"),
]
RUN = 4          # consecutive identical symbols that make an instance
PER = 8          # tiles per pattern


def instances(blob, symbol, limit):
    """Up to `limit` instances of `symbol` repeated RUN times, each from a different map
    where possible, as lists of (x, y)."""
    edges = P.ANGLE_BANDS["schema4"]
    found, seen = [], collections.Counter()
    for md5, rows in blob["objects"].items():
        for start, syms in P.segments(rows, edges, False, with_flow=True):
            for i in range(len(syms) - RUN + 1):
                if any(s != symbol for s in syms[i:i + RUN]):
                    continue
                # A turn at object j is drawn by j-1, j, j+1, so a run of RUN turns spans
                # the apexes plus one neighbour at each end.
                lo, hi = start + i - 1, start + i + RUN + 1
                if lo < 0 or hi > len(rows):
                    continue
                pts = []
                for r in rows[lo:hi]:
                    x, y = r.get("targetX"), r.get("targetY")
                    if x in ("", None) or y in ("", None):
                        pts = []
                        break
                    pts.append((float(x), float(y)))
                if len(pts) >= RUN + 2:
                    found.append((md5, pts))
    found.sort(key=lambda kv: seen[kv[0]])
    out = []
    for md5, pts in found:
        if seen[md5] >= 2 and len(out) < limit:      # spread across maps where possible
            continue
        seen[md5] += 1
        out.append(pts)
        if len(out) >= limit:
            break
    return out


def svg(pts, size=104, pad=13):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    k = (size - 2 * pad) / span
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    at = [((x - cx) * k + size / 2, (y - cy) * k + size / 2) for x, y in pts]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in at)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{3.4 if i in (0, len(at)-1) else 2.4}" '
                   f'class="{"end" if i in (0, len(at)-1) else "mid"}"/>'
                   for i, (x, y) in enumerate(at))
    return (f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
            f'<polyline points="{line}"/>{dots}</svg>')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="build/viewer/shapes.html")
    args = ap.parse_args()

    blob = json.load(open(args.cache))
    blocks = []
    for symbol, title, note in WANTED:
        got = instances(blob, symbol, PER)
        print(f"{symbol:<22}{len(got):>3} drawn   {title}")
        if not got:
            continue
        tiles = "".join(f'<div class="tile">{svg(p)}</div>' for p in got)
        blocks.append(f'<section><h2>{title}</h2>'
                      f'<p class="sym"><code>{symbol}</code> &middot; {note}</p>'
                      f'<div class="row">{tiles}</div></section>')

    html = f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pattern Shapes</title>
<style>
  :root {{ --ink:#E7E9E8; --dim:#8293A0; --scope:#05080A; --line:#1E2A33; --hot:#C96A5B; }}
  body {{ margin:0; padding:30px 16px 60px; background:#0A0E11; color:var(--ink);
         font:15px/1.55 ui-sans-serif,system-ui,sans-serif; }}
  .shell {{ max-width:980px; margin:0 auto; }}
  h1 {{ font-size:25px; margin:0 0 4px; letter-spacing:-.015em; }}
  .lede {{ color:var(--dim); margin:0 0 32px; max-width:62ch; }}
  h2 {{ font-size:16px; margin:30px 0 2px; }}
  .sym {{ color:var(--dim); font-size:13px; margin:0 0 12px; }}
  code {{ font:12px ui-monospace,monospace; color:var(--ink); background:#131A20;
          padding:2px 6px; border-radius:4px; }}
  .row {{ display:flex; flex-wrap:wrap; gap:10px; }}
  .tile {{ background:var(--scope); border:1px solid var(--line); border-radius:7px;
           line-height:0; }}
  polyline {{ fill:none; stroke:#7FA8C9; stroke-width:1.5; stroke-linejoin:round; }}
  circle.mid {{ fill:#9FB4C4; }}
  circle.end {{ fill:var(--hot); }}
</style>
<div class="shell">
  <h1>Pattern Shapes</h1>
  <p class="lede">Instances of the corpus-wide vocabulary, drawn from the objects
  themselves &mdash; centred and scaled per tile, never rotated or reflected, so the shape
  is the one that was played. Red marks the first and last object. Every tile is a
  different place in the corpus, spread across beatmaps where the pattern allows.</p>
  {''.join(blocks)}
</div>
"""
    os.makedirs(os.path.dirname(os.path.join(ROOT, args.out)), exist_ok=True)
    with open(os.path.join(ROOT, args.out), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
