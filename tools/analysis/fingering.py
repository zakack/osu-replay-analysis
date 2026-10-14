"""Which finger takes a 1/2 jump, in the analog doubletap era, and what decides it.

Analog doubletap (2026-09-05 on, setup-timeline.csv) runs a plain remap outside the deep latch,
so the replay button is the physical key: LeftButton = k1 = pool[0], RightButton = k2. The key
traces confirm it (section 1). The player reports their middle finger at over 60%, and k1 is
the key at ~70%, so k1 is read as the middle finger and k2 as the index; that labelling is
the player's, not measured.

A "1/2 jump" is a press judging a circle or slider head whose gap from the previous tapped
object is 1/2 beat (+-0.03) and whose spacing is at least 2 radii. 2026-09-05, the day analog
and the key pool merged, is left out: its transition matrix differs from every later day.
The plain-buttons era (2025-02-08..05-05, alternation by hand) is the reference.

Sections: (1) the key traces against the replay button, and how many 1/2 jumps fall in the
deep latch; (2) the transition matrix, plain era against analog; (3) index repeats by spacing;
(4) the mode a 1/2 run is played in, by tempo x spacing, and what ends middle-only play;
(5) whether middle-only presses are more or less accurate than alternated ones in the same
cell (tempo x spacing x run position); (6) the weekly drift, with and without reweighting to a fixed cell mix.

    python3 tools/analysis/fingering.py [--rebuild]
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from attribution_check import deep_lookup                   # noqa: E402

CACHE = "build/fingering.pickle"
HALF = 0.03          # beats either side of 1/2
JUMP = 2.0           # radii
PLAIN = ("2025-02-08", "2025-05-05")
ANALOG = "2026-09-06"
TEMPO = [0, 150, 165, 175, 190, 200, 250, 1e9]          # ms per 1/2
SPACING = [2, 3, 4, 5, 6, 8, 1e9]                        # radii


def era(replay):
    m = re.search(r"\((\d{4}-\d{2}-\d{2})_\d{2}-\d{2}\)\.osr$", replay)
    if not replay.startswith("zaksynack playing") or not m:
        return None
    d = m.group(1)
    if PLAIN[0] <= d < PLAIN[1]:
        return "plain"
    return "analog" if d >= ANALOG else None


def keep(df):
    df = df.assign(era=df.replay.map(era))
    return df[df.era.notna()]


def build():
    pr = pd.concat(keep(c) for c in pd.read_csv("build/presses.csv", chunksize=1_000_000))
    pr = pr[pr.action.isin(["LeftButton", "RightButton"])].sort_values(["replay", "time"])
    pr["L"] = (pr.action == "LeftButton").astype(int)
    g = pr.groupby("replay")
    pr["prevL"] = g.L.shift(1)
    for b, v in (("L", 1), ("R", 0)):
        pr["since" + b] = pr.time - pr.time.where(pr.L == v).groupby(pr.replay).transform(lambda s: s.shift(1).ffill())

    rs = []
    for c in pd.read_csv("build/rhythm.csv", chunksize=2_000_000,
                         usecols=["replay", "groupGap", "tolerance", "runIndex", "time", "beatLength", "kind"]):
        rs.append(keep(c[(c.tolerance == 0.02) & (c.groupGap == 1) & (c.kind != "sliderEnd")]))
    ev = pd.concat(rs).drop(columns="era").sort_values(["replay", "time"])
    eg = ev.groupby("replay")
    ev["gapPrev"] = (ev.time - eg.time.shift(1)) / ev.beatLength
    ev["gapNext"] = (eg.time.shift(-1) - ev.time) / ev.beatLength
    ev["prevKind"] = eg.kind.shift(1)

    geo = pd.concat(keep(c) for c in pd.read_csv(
        "build/geometry.csv", chunksize=1_000_000,
        usecols=["replay", "startTime", "spacingRadii", "heading", "hitError", "aimErrorRadii", "result"]))
    geo = geo.drop(columns="era").rename(columns={"startTime": "time"})

    h = pr[pr.outcome == "hit"].drop(columns=["result"])
    h = h.merge(ev.rename(columns={"time": "target"}), on=["replay", "target"])
    h = h.merge(geo.rename(columns={"time": "target"}), on=["replay", "target"], how="left")
    at = deep_lookup()
    h["deep"] = [at(r, t) if e == "analog" else "-" for r, t, e in zip(h.replay, h.time, h.era)]
    h["ms"] = h.gapPrev * h.beatLength
    h["date"] = h.replay.str.extract(r"\((\d{4}-\d{2}-\d{2})")[0]
    h.to_pickle(CACHE)
    return h


def trace_check(h):
    """Replay button against the key whose travel rose most in the 12 ms before each press."""
    rows = []
    a = h[h.era == "analog"]
    for aj in sorted(glob.glob("build/traces/*.align.json")):
        stem = aj[:-len(".align.json")]
        if not os.path.exists(stem + ".csv.zst"):
            continue
        txt = subprocess.run(["zstd", "-dc", stem + ".csv.zst"], capture_output=True, text=True).stdout
        t0 = next(int(p.split("=")[1]) for l in txt.splitlines()[:8] if l.startswith("#")
                  for p in l[1:].split() if p.startswith("t0_monotonic_ns=")) / 1e6
        tr = pd.read_csv(io.StringIO(txt), comment="#")
        t = t0 + tr.us.values / 1000
        for al in json.load(open(aj)):
            sub = a[a.replay == al["replay"]]
            for i, mt in zip(sub.index, sub.time):
                seg = next((s for s in al.get("segments") or [] if s["from_ms"] <= mt < s["to_ms"]),
                           {"offset_ms": al["offset_ms"]})
                m = seg["offset_ms"] + mt * (1 + al.get("drift", 0.0))
                lo, hi = np.searchsorted(t, m - 12), np.searchsorted(t, m + 2)
                if lo == 0 or hi <= lo:
                    continue
                d1 = tr.k1_mm.values[hi - 1] - tr.k1_mm.values[lo - 1]
                d2 = tr.k2_mm.values[hi - 1] - tr.k2_mm.values[lo - 1]
                rows.append((i, "k1" if d1 > d2 else "k2"))
    p = pd.DataFrame(rows, columns=["i", "phys"]).set_index("i")
    return h.join(p, how="inner")


def main():
    h = pd.read_pickle(CACHE) if os.path.exists(CACHE) and "--rebuild" not in sys.argv else build()
    jumps = h[(h.gapPrev.sub(0.5).abs() < HALF) & (h.spacingRadii >= JUMP) & (h.deep != "deep")]
    an, pl = jumps[jumps.era == "analog"], jumps[jumps.era == "plain"]

    print("== 1. key traces against the replay button (deep latch off)")
    b = trace_check(h[h.deep == "off"])
    print(pd.crosstab(b.action, b.phys).to_string())
    aj = h[(h.era == "analog") & (h.gapPrev.sub(0.5).abs() < HALF) & (h.spacingRadii >= JUMP)]
    print("analog 1/2 jumps by latch state:", aj.deep.value_counts().to_dict())

    print("\n== 2. P(this press is k1 | previous press), 1/2 jumps")
    for name, d in (("plain", pl), ("analog", an)):
        t = d.groupby("prevL").L.agg(["size", "mean"])
        print(f"  {name:7s} n={len(d):7d}  P(k1)={d.L.mean():.3f}   after k2: {t.loc[0, 'mean']:.3f}"
              f"   after k1: {t.loc[1, 'mean']:.3f}")

    print("\n== 3. analog: P(k1 | previous k2) by spacing, every 1/2 press")
    x = h[(h.era == "analog") & (h.gapPrev.sub(0.5).abs() < HALF) & (h.prevL == 0) & (h.deep != "deep")]
    print(x.groupby(pd.cut(x.spacingRadii, [-0.01, 0.5, 1, 2, 3, 5, 1e9]), observed=True)
          .L.agg(["size", "mean"]).round(3).T.to_string())

    print("\n== 4. analog 1/2 runs (>= 4 jump presses): the mode they are played in")
    a = h[(h.era == "analog") & (h.deep != "deep")].sort_values(["replay", "time"]).copy()
    a["half"] = a.gapPrev.sub(0.5).abs() < HALF
    a["rid"] = ((~a.half) | (a.replay != a.replay.shift())).cumsum()     # lead-in press opens a run
    a["isjump"] = a.half & (a.spacingRadii >= JUMP)
    a["mm"] = (a.L == 1) & (a.prevL == 1)
    j = a[a.isjump]
    runs = j.groupby("rid").agg(n=("L", "size"), ms=("ms", "median"), sp=("spacingRadii", "median"),
                                mm=("mm", "mean"), nI=("L", lambda s: (s == 0).sum()))
    runs = runs[runs.n >= 4]
    runs["allM"] = runs.nI == 0
    runs["T"] = pd.cut(runs.ms, TEMPO)
    runs["S"] = pd.cut(runs.sp, SPACING)
    print(f"  {len(runs)} runs; share with no k2 press: {runs.allM.mean():.3f}")
    print("  share of runs played on k1 alone, tempo (ms per 1/2) x spacing (radii); n in brackets")
    piv = runs.groupby(["T", "S"], observed=True).allM.agg(["mean", "size"])
    tab = piv.apply(lambda r: f"{r['mean']:.2f} ({int(r['size'])})" if r["size"] >= 20 else "", axis=1).unstack()
    print(tab.to_string())
    print("  by run length:", runs.groupby(pd.cut(runs.n, [3, 5, 8, 16, 1000]), observed=True)
          .allM.mean().round(3).to_dict())

    m = an[(an.prevL == 1)].copy()
    print("\n== 4b. analog: after a k1 press, P(k2) by what the gap after this note is")
    print(m.groupby(pd.cut(m.gapNext, [0, 0.3, 0.55, 1.05, 1e9]), observed=True)
          .L.agg(size="size", pk2=lambda s: 1 - s.mean()).round(3).T.to_string())
    print("  by previous object:", m.groupby("prevKind").L.apply(lambda s: round(1 - s.mean(), 3)).to_dict())

    print("\n== 5. accuracy: k1-after-k1 against alternated presses, same tempo x spacing x run-position cell")
    an2 = an.copy()
    an2["pat"] = np.where((an2.L == 1) & (an2.prevL == 1), "k1k1", np.where(an2.L != an2.prevL, "alt", "other"))
    an2 = an2[an2.pat != "other"]
    # run position too: k1k1 clusters in the first notes of a run, which are less accurate anyway
    an2["cell"] = (pd.cut(an2.ms, TEMPO).astype(str) + "|" + pd.cut(an2.spacingRadii, SPACING).astype(str)
                   + "|" + pd.cut(an2.runIndex, [-1, 1, 3, 7, 1000]).astype(str))
    an2["nonGreat"] = (an2.result != "Great").astype(float)
    an2["abserr"] = an2.hitError.abs()
    rows = []
    for c, d in an2.groupby("cell"):
        g = d.groupby("pat")
        if set(g.groups) != {"k1k1", "alt"} or g.size().min() < 200:
            continue
        s = g.agg(err_sd=("hitError", "std"), aim=("aimErrorRadii", "mean"), ng=("nonGreat", "mean"),
                  n=("L", "size"))
        rows.append((c, *s.loc["k1k1"], *s.loc["alt"]))
    r = pd.DataFrame(rows, columns=["cell", "sd_mm", "aim_mm", "ng_mm", "n_mm", "sd_alt", "aim_alt", "ng_alt", "n_alt"])
    w = np.minimum(r.n_mm, r.n_alt)
    print(f"  {len(r)} cells with >= 200 of each; weighted mean of (k1k1 - alt):")
    print(f"    hit error SD {np.average(r.sd_mm - r.sd_alt, weights=w):+.2f} ms   "
          f"aim error {np.average(r.aim_mm - r.aim_alt, weights=w):+.4f} radii   "
          f"non-Great {100 * np.average(r.ng_mm - r.ng_alt, weights=w):+.2f} pp")
    print(f"    cells where k1k1 has the lower SD: {(r.sd_mm < r.sd_alt).sum()} of {len(r)}")
    print(r.round(3).to_string(index=False))

    print("\n== 6. weekly P(k1) on 1/2 jumps, raw and reweighted to the overall cell mix")
    an3 = an.copy()
    an3["cell"] = pd.cut(an3.ms, TEMPO).astype(str) + "|" + pd.cut(an3.spacingRadii, SPACING).astype(str)
    an3["week"] = pd.to_datetime(an3.date).dt.isocalendar().week
    mix = an3.cell.value_counts(normalize=True)
    for wk, d in an3.groupby("week"):
        pc = d.groupby("cell").L.mean()
        common = pc.index.intersection(mix.index)
        adj = np.average(pc[common], weights=mix[common])
        print(f"  week {wk}: n={len(d):6d}  raw {d.L.mean():.3f}  cell-adjusted {adj:.3f}")


if __name__ == "__main__":
    main()
