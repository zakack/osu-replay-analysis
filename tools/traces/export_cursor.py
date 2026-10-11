#!/usr/bin/env python3
"""Write each aligned replay's cursor trace in its own map time and playfield coordinates,
for the simulator to read in place of the replay's 60 Hz cursor (src/Sim/CursorTrace.cs).

    python3 tools/traces/export_cursor.py build/traces/<stamp>   # -> build/traces/cursor/
    ORA_CURSOR_DIR=build/traces/cursor ora verify --corpus ...

Rows are kept from 2 s before the replay's first press to 2 s after its last; the trace is
piecewise constant, so the row before a window still holds at its start.
"""
import bisect, io, json, os, subprocess, sys

stem = sys.argv[1]
outdir = os.path.join(os.path.dirname(stem), "cursor")
os.makedirs(outdir, exist_ok=True)
lines = subprocess.run(["zstd", "-dc", stem + ".cursor.csv.zst"], capture_output=True, text=True, check=True).stdout.splitlines()
t0 = next(int(l.split("=")[1]) for l in lines if l.startswith("# t0_monotonic_ns=")) // 1000
rows = [l.split(",") for l in lines if l[:1].isdigit()]
us = [int(r[0]) + t0 for r in rows]
for a in json.load(open(stem + ".align.json")):
    if "affine" not in a:
        continue
    (ax, ay), (bx, by), (cx, cy) = a["affine"]
    k = 1 + a.get("drift", 0.0)                           # mono_ms = offset + map_ms * k
    segs = a.get("segments") or [dict(from_ms=-1e12, to_ms=1e12, offset_ms=a["offset_ms"])]
    n = 0
    with open(os.path.join(outdir, a["replay"] + ".cursor.csv"), "w") as f:
        f.write("t,x,y\n")
        # one block per segment (a pause or seek starts a new one), each clipped to the
        # play's own span; rows stay in map-time order because segments are
        for sg in segs:
            t_lo = max(sg["from_ms"], a["first_ms"] - 2000)
            t_hi = min(sg["to_ms"], a["last_ms"] + 2000)
            if t_hi <= t_lo:
                continue
            off = sg["offset_ms"]
            lo = bisect.bisect_right(us, (off + t_lo * k) * 1000) - 1
            hi = bisect.bisect_right(us, (off + t_hi * k) * 1000)
            for i in range(max(lo, 0), hi):
                x, y = int(rows[i][1]), int(rows[i][2])
                f.write(f"{max((us[i] / 1000 - off) / k, t_lo):.3f},{ax * x + bx * y + cx:.3f},{ay * x + by * y + cy:.3f}\n")
                n += 1
    print(a["replay"][:70], n, "rows")
