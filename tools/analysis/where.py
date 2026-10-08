"""Does where a target sits, and which way the jump into it points, change accuracy?

The pattern bins are invariant to both by construction. These two axes exist because the
hand is not (CLAUDE.md, "Where on the screen is its own axis"): a tablet maps the playfield
onto a fixed patch under the pen, and the primary player's area-warping driver maps each
quadrant differently, and has changed that mapping several times (setup-timeline.csv).

The measurement is a residual, not a raw rate. Position and heading are both confounded with
everything else a map does: big jumps sit near edges, streams run horizontally, hard maps
are played less well. So each outcome is modelled additively as

    y = replay + pattern + region + heading + e

with ``pattern`` a cell of (kind, fromSlider, signed angle, spacing, velocity), and fitted by
backfitting group means, which is ordinary least squares for an additive model of
categoricals. The replay term absorbs the map, the mods, circle size, the day and the setup,
so what survives into ``region`` and ``heading`` is a within-play contrast: the same player
in the same run doing comparable patterns in different places and directions.

Uncertainty is a cluster bootstrap over beatmaps on the final partial residuals; repeated
plays of one map are not independent evidence about where its objects sit.

Outcomes:
  miss    every judged object; 1 if Miss
  aim     |cursor - target| at the press, in radii; presses only, so selected on outcome
  along   signed error along the jump's heading, radii; + is overshoot, - undershoot
  across  signed error across it, radii; + is to the left of travel (counter-clockwise side)
  hit     signed hit error, ms, + is late, with the per-beatmap offset in force removed
          (offsets.py: raw = hitError - offset); replays whose offset is ambiguous are
          left out of this outcome only
  hitVsCrowd  hit minus the reference corpus's median at the same object (map-bias.csv),
          so a mistimed section does not read as the player; only where the map is covered.
          Not in OUTCOMES: one more worker per analyse is more than the box holds
  missAlong  on a miss with no press, the cursor at the object's time along the heading
  centre  signed error toward the playfield's centre, radii; presses only, heading-free
  dx, dy  the press error in screen axes, radii; + is right and up

    python3 tools/analysis/where.py reduce     # once: build/where.pickle from geometry.csv
    python3 tools/analysis/where.py analyse [--era T4] [--mods HR|noflip] [--frame screen|map]
                                            [--out build/where-report.txt]
"""
from __future__ import annotations

import array
import collections
import csv
import json
import math
import os
import pickle
import random
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from flip import osr_mods  # noqa: E402
from offsets import Offsets  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bins.maptiming import _key as bias_key  # noqa: E402  the one rounding rule for the join

MAP_BIAS = "build/map-bias.csv"

GEOMETRY = "build/geometry.csv"
SCORES = "build/score-index.json"
RECENT = "build/recent"
CACHE = "build/where.pickle"
PLAYER = "zaksynack"

# Mods that move the cursor or remove a decision the player makes. Autopilot aims for the
# player; Relax taps for them, so a "miss" under it is an aim miss alone; the fun mods move
# targets through the drawable layer, so the logical position is not where the hit target
# was (CLAUDE.md, "Some mods reach hit detection through the drawable layer").
ALLOWED_MODS = {"HD", "DT", "NC", "HT", "DC", "HR", "NF", "CL", "DA", "MR", "SD", "PF"}

# Tablet regimes, from setup-timeline.csv. The driver maps quadrants differently and the
# mapping changed at these dates, so a regional finding has to be shown per regime before
# it is believed.
ERAS = (
    ("T0", "0000", "2024-10-27", "before area mapping"),
    ("T1", "2024-10-27", "2025-01-14", "area mapping changed almost daily"),
    ("T2", "2025-01-14", "2025-05-05", "BR linear; TL forward elliptical, TR inverse"),
    ("T3", "2025-05-05", "2025-10-22", "BR mapped forward; TR/BL inverse"),
    ("T4", "2025-10-22", "9999", "BR and BL linear; TL forward, TR inverse"),
)

TIMESTAMP = re.compile(r"\((\d{4}-\d{2}-\d{2})_\d{2}-\d{2}\)\.osr$")
RESULTS = {"Great": 0, "Ok": 1, "Meh": 2, "Miss": 3}
NAN = float("nan")

# Region grid over the 512x384 playfield: 4 columns x 3 rows of 128 units.
COLS, ROWS = 4, 3
SECTORS = 8                      # 45 degree heading sectors, sector 0 centred on "right"
SECTOR_NAMES = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")
MIN_SPACING = 1.0                # radii: below this the "heading" is a stack offset
BOOT = 300


def era_of(day: str) -> str | None:
    for name, lo, hi, _ in ERAS:
        if lo <= day < hi:
            return name
    return None


def mods_by_replay() -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for r in json.load(open(SCORES)):
        if r["User"] != PLAYER:
            continue
        m = r["Mods"] or ""
        out[r["Filename"] + ".osr"] = frozenset(m[i:i + 2] for i in range(0, len(m), 2))
    # The score index predates the newest replays; read their mods off the file itself.
    for name in os.listdir(RECENT):
        if name.endswith(".osr") and name not in out and name.startswith(PLAYER + " playing"):
            try:
                out[name] = frozenset(m["acronym"] for m in osr_mods(os.path.join(RECENT, name)))
            except Exception:
                pass
    return out


def reduce() -> None:
    """One pass over the 1.7GB geometry CSV, keeping the player's usable rows as columns."""
    mods = mods_by_replay()
    md5 = {os.path.basename(r["Path"]): r["BeatmapMd5"] for r in json.load(open("build/corpus.json"))}
    offsets = Offsets()
    bias: dict[tuple[str, float], float] = {}
    if os.path.exists(MAP_BIAS):
        with open(MAP_BIAS, newline="") as fh:
            for r in csv.DictReader(fh):
                bias[bias_key(r["beatmapMd5"], float(r["startTime"]))] = float(r["referenceMedian"])

    cols = {k: array.array("f") for k in
            ("t", "angle", "spacing", "vel", "aim", "hit", "tx", "ty", "heading", "cx", "cy", "crowd")}
    ints = {k: array.array("i") for k in ("rep", "kind", "slider_in", "result", "press")}
    replays: list[dict] = []
    index: dict[str, int] = {}
    skipped = collections.Counter()

    def f(text: str) -> float:
        return float(text) if text else NAN

    with open(GEOMETRY, newline="") as fh:
        for row in csv.DictReader(fh):
            name = row["replay"]
            k = index.get(name)
            if k is None:
                m = mods.get(name)
                stamp = TIMESTAMP.search(name)
                if m is None:
                    why = "not the player's" if not name.startswith(PLAYER) else "no mods known"
                elif m - ALLOWED_MODS:
                    why = "excluded mod"
                elif stamp is None or name not in md5:
                    why = "no date or md5"
                else:
                    why = None
                if why:
                    index[name] = k = -1
                    skipped[why] += 1
                else:
                    index[name] = k = len(replays)
                    off, status = offsets.at(md5[name], name)
                    replays.append({"name": name, "md5": md5[name], "day": stamp.group(1),
                                    "offset": off, "offsetStatus": status,
                                    "era": era_of(stamp.group(1)), "mods": sorted(m)})
            if k < 0 or row["result"] not in RESULTS:
                continue
            ints["rep"].append(k)
            ints["kind"].append(row["kind"] == "slider")
            ints["slider_in"].append(row["exitSlackRadii"] != "")
            ints["result"].append(RESULTS[row["result"]])
            ints["press"].append(row["cursorSource"] == "press")
            cols["t"].append(float(row["startTime"]))
            cols["angle"].append(f(row["signedAngle"]))
            cols["spacing"].append(f(row["spacingRadii"]))
            cols["vel"].append(f(row["requiredVelocity"]))
            cols["aim"].append(f(row["aimErrorRadii"]))
            cols["hit"].append(f(row["hitError"]))
            cols["crowd"].append(bias.get(bias_key(replays[k]["md5"], float(row["startTime"])), NAN))
            cols["tx"].append(f(row["targetX"]))
            cols["ty"].append(f(row["targetY"]))
            cols["heading"].append(f(row["heading"]))
            cols["cx"].append(f(row["cursorX"]))
            cols["cy"].append(f(row["cursorY"]))

    with open(CACHE, "wb") as fh:
        pickle.dump({"replays": replays, "cols": cols, "ints": ints, "skipped": dict(skipped)}, fh)
    print(f"{len(replays)} replays, {len(ints['rep'])} objects; skipped replays {dict(skipped)}")


# ------------------------------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------------------------------

def bin_of(v: float, edges: tuple[float, ...]) -> int:
    if v != v:
        return -1
    for i in range(1, len(edges)):
        if v < edges[i]:
            return i - 1
    return len(edges) - 2


ANGLE_EDGES = tuple(range(-180, 181, 30))
SPACING_EDGES = (0, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, math.inf)
VEL_EDGES = (0, 0.01, 0.02, 0.03, 0.045, 0.065, 0.09, math.inf)


def region_of(x: float, y: float) -> int:
    c = min(COLS - 1, max(0, int(x // (512 / COLS))))
    r = min(ROWS - 1, max(0, int(y // (384 / ROWS))))
    return r * COLS + c


def sector_of(h: float) -> int:
    if h != h:
        return -1
    return int(round(h / (2 * math.pi / SECTORS))) % SECTORS


def region_name(g: int) -> str:
    r, c = divmod(g, COLS)
    return "TMB"[r] + str(c + 1)




def load():
    with open(CACHE, "rb") as fh:
        return pickle.load(fh)


OUTCOMES = (("miss", 100, "pp"), ("aim", 1, "r"), ("along", 1, "r"), ("across", 1, "r"),
            ("hit", 1, "ms"), ("missAlong", 1, "r"), ("centre", 1, "r"), ("dx", 1, "r"), ("dy", 1, "r"))


def selected(replay: dict, era: str | None, mods: str | None) -> bool:
    """``mods`` is "HR" for Hard Rock without Mirror (the one flip whose axis is known from
    the score index alone), "noflip" for neither, None for everything."""
    if era and replay["era"] != era:
        return False
    m = set(replay["mods"])
    if mods == "HR":
        return "HR" in m and "MR" not in m
    if mods == "noflip":
        return not m & {"HR", "MR"}
    return True


def prepare(d, era: str | None, mods: str | None = None, frame: str = "screen") -> dict:
    """Columns of integer factor codes and float outcomes, one entry per judged object.

    The circle radius is per replay (circle size and HR change it) and is recovered from the
    presses, where aimErrorRadii and the cursor together give it exactly.

    ``frame="map"`` files a Hard Rock object under where the .osu put it rather than where it
    appeared, undoing the vertical flip on both region and heading. With it, the flipped plays
    say whether an effect follows the screen (the hand) or the map's own placement."""
    reps, cols, ints = d["replays"], d["cols"], d["ints"]
    n = len(ints["rep"])
    rep, press, aim = ints["rep"], ints["press"], cols["aim"]
    cx, cy, tx, ty = cols["cx"], cols["cy"], cols["tx"], cols["ty"]

    rs = collections.defaultdict(float)
    rn = collections.defaultdict(int)
    for i in range(n):
        a = aim[i]
        if press[i] and a == a and a > 0.05:
            rs[rep[i]] += math.hypot(cx[i] - tx[i], cy[i] - ty[i]) / a
            rn[rep[i]] += 1
    radius = {k: rs[k] / rn[k] for k in rn}

    maps: dict[str, int] = {}
    patterns: dict[tuple, int] = {}
    out = {k: array.array("i") for k in ("rep", "map", "pat", "reg", "sec")}
    ys = {k: array.array("f") for k, _, _ in OUTCOMES + (("hitVsCrowd", 1, "ms"),)}
    for i in range(n):
        k = rep[i]
        if not selected(reps[k], era, mods):
            continue
        rad = radius.get(k)
        h, sp = cols["heading"][i], cols["spacing"][i]
        directed = h == h and sp == sp and sp >= MIN_SPACING
        key = (ints["kind"][i], ints["slider_in"][i], bin_of(cols["angle"][i], ANGLE_EDGES),
               bin_of(sp, SPACING_EDGES), bin_of(cols["vel"][i], VEL_EDGES))
        out["rep"].append(k)
        out["map"].append(maps.setdefault(reps[k]["md5"], len(maps)))
        out["pat"].append(patterns.setdefault(key, len(patterns)))
        flip = frame == "map" and "HR" in reps[k]["mods"]
        out["reg"].append(region_of(tx[i], 384 - ty[i] if flip else ty[i]))
        out["sec"].append(sector_of(-h if flip else h) if directed else -1)

        along = across = miss_along = NAN
        missed = ints["result"][i] == 3
        if directed and rad and cx[i] == cx[i]:
            dx = (cx[i] - tx[i]) / rad
            dy = -(cy[i] - ty[i]) / rad           # y up, matching the heading's convention
            ux, uy = math.cos(h), math.sin(h)
            if press[i]:
                along = dx * ux + dy * uy           # + overshoot, - undershoot
                across = -dx * uy + dy * ux         # + left of travel
            elif missed:
                # No press: the cursor at the object's time, interpolated. Where it was when
                # the object went by, which separates "never got there" from "flew past".
                miss_along = dx * ux + dy * uy
        ys["miss"].append(1.0 if missed else 0.0)
        ys["aim"].append(aim[i] if press[i] else NAN)
        ys["along"].append(along)
        ys["across"].append(across)
        raw = cols["hit"][i] - reps[k]["offset"] if reps[k]["offsetStatus"] != "ambiguous" else NAN
        ys["hit"].append(raw)
        ys["hitVsCrowd"].append(raw - cols["crowd"][i])
        ys["missAlong"].append(miss_along)
        # Error toward the middle of the playfield, independent of where the jump came from:
        # + means the press landed on the centre side of the target. If corner undershoot is
        # really a pull toward the middle, this carries it with no heading needed.
        centre = NAN
        to_c = math.hypot(256 - tx[i], 192 - ty[i])
        if press[i] and rad and to_c > 32 and cx[i] == cx[i]:
            centre = ((cx[i] - tx[i]) * (256 - tx[i]) + (cy[i] - ty[i]) * (192 - ty[i])) / to_c / rad
        ys["centre"].append(centre)
        # The same error in screen axes, which a pull toward the middle and a constant
        # sideways bias cannot both fake: a pull flips sign across the centre line, a bias
        # does not. + is right and up.
        ok = press[i] and rad and cx[i] == cx[i]
        ys["dx"].append((cx[i] - tx[i]) / rad if ok else NAN)
        ys["dy"].append(-(cy[i] - ty[i]) / rad if ok else NAN)
    out["y"] = ys
    out["n_rep"] = len(reps)
    out["n_pat"] = len(patterns)
    out["n_map"] = len(maps)
    return out


def backfit(data: dict, outcome: str, terms: tuple[str, ...], directed_only: bool, rounds: int = 10):
    """y = mu + sum of terms, by backfitting group means (OLS for an additive categorical
    model). Location terms are centred, so an effect reads as "this much worse than the
    average place on screen / direction of travel"."""
    yc, sec = data["y"][outcome], data["sec"]
    idx = [i for i in range(len(yc)) if yc[i] == yc[i] and (sec[i] >= 0 or not directed_only)]
    y = [yc[i] for i in idx]
    size = {"rep": data["n_rep"], "pat": data["n_pat"], "reg": ROWS * COLS, "sec": SECTORS}
    keys = {t: [data[t][i] for i in idx] for t in terms}
    cnt = {}
    for t in terms:
        c = [0] * size[t]
        for g in keys[t]:
            c[g] += 1
        cnt[t] = c
    mu = sum(y) / len(y)
    eff = {t: [0.0] * size[t] for t in terms}
    resid = [v - mu for v in y]

    for _ in range(rounds):
        for t in terms:
            ks, c, old = keys[t], cnt[t], eff[t]
            s = [0.0] * size[t]
            for g, r in zip(ks, resid):
                s[g] += r
            new = [(s[g] / c[g] + old[g]) if c[g] else 0.0 for g in range(size[t])]
            if t in ("reg", "sec"):
                m = sum(new[g] * c[g] for g in range(size[t])) / sum(c)
                new = [v - m if c[g] else 0.0 for g, v in enumerate(new)]
                mu += m
            delta = [a - b for a, b in zip(new, old)]
            resid = [r - delta[g] for r, g in zip(resid, ks)]
            if t in ("reg", "sec"):
                resid = [r - m for r in resid]
            eff[t] = new
    maps = [data["map"][i] for i in idx]
    return {"n": len(y), "mu": mu, "eff": eff, "cnt": cnt, "keys": keys, "resid": resid, "maps": maps}


def cluster_ci(fit: dict, term: str) -> list[tuple[float, float]]:
    """Map-cluster bootstrap of a location term, on partial residuals (residual plus the
    term's own effect). Nuisance terms are held at their full-sample fit."""
    e, ks, resid = fit["eff"][term], fit["keys"][term], fit["resid"]
    L = len(e)
    per = collections.defaultdict(lambda: [[0.0] * L, [0] * L])
    for m, g, r in zip(fit["maps"], ks, resid):
        cell = per[m]
        cell[0][g] += r + e[g]
        cell[1][g] += 1
    maps = list(per.values())
    rng = random.Random(0)
    draws = [[] for _ in range(L)]
    for _ in range(BOOT):
        s, c = [0.0] * L, [0] * L
        for a, b in rng.choices(maps, k=len(maps)):
            for g in range(L):
                s[g] += a[g]
                c[g] += b[g]
        centre = sum(s) / sum(c)
        for g in range(L):
            if c[g]:
                draws[g].append(s[g] / c[g] - centre)
    ci = []
    for xs in draws:
        xs.sort()
        ci.append((xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1]) if xs else (NAN, NAN))
    return ci


def wsd(fit, term):
    e, c = fit["eff"][term], fit["cnt"][term]
    return math.sqrt(sum(ci * v * v for ci, v in zip(c, e)) / sum(c))


def run_outcome(args):
    data, outcome, scale, unit = args
    lines = []
    p = lambda s="": lines.append(s)
    res = {}
    # Joint model on directed objects: both location terms on the same rows.
    fit = backfit(data, outcome, ("rep", "pat", "reg", "sec"), directed_only=True)
    ci_r, ci_s = cluster_ci(fit, "reg"), cluster_ci(fit, "sec")
    # Position alone on every object, including stacks and streams with no heading.
    pos = backfit(data, outcome, ("rep", "pat", "reg"), directed_only=False)
    ci_p = cluster_ci(pos, "reg")
    # How much the location terms are worth against the nuisance terms: the residual
    # variance with and without them, on the directed rows.
    base = backfit(data, outcome, ("rep", "pat"), directed_only=True)
    var = lambda f: sum(r * r for r in f["resid"]) / f["n"]
    vb, vf = var(base), var(fit)

    p(f"== {outcome}: mean {fit['mu'] * scale:.4g}{unit}, directed n={fit['n']}, all n={pos['n']}")
    p(f"   residual SD after replay+pattern {math.sqrt(vb) * scale:.4g}{unit}; adding region+heading "
      f"removes {100 * (vb - vf) / vb:.2f}% of it")
    p(f"   spread (count-weighted SD) of region term {wsd(fit, 'reg') * scale:.3g}{unit}, "
      f"heading term {wsd(fit, 'sec') * scale:.3g}{unit}; region on all objects {wsd(pos, 'reg') * scale:.3g}{unit}")
    for label, f, ci in (("region | heading (directed objects)", fit, ci_r), ("region alone (all objects)", pos, ci_p)):
        p(f"   {label}: effect [95% map-bootstrap CI] n   (* = CI excludes 0)")
        for r in range(ROWS):
            cells = []
            for c in range(COLS):
                g = r * COLS + c
                lo, hi = ci[g]
                star = "*" if lo > 0 or hi < 0 else " "
                cells.append(f"{region_name(g)} {f['eff']['reg'][g] * scale:+7.3f}{star}[{lo * scale:+.3f},{hi * scale:+.3f}] {f['cnt']['reg'][g]:>7}")
            p("     " + "  ".join(cells))
    p("   heading | region (direction of travel on screen):")
    for g in range(SECTORS):
        lo, hi = ci_s[g]
        star = "*" if lo > 0 or hi < 0 else " "
        p(f"     {SECTOR_NAMES[g]:>2} {fit['eff']['sec'][g] * scale:+7.3f}{star}[{lo * scale:+.3f},{hi * scale:+.3f}] {fit['cnt']['sec'][g]:>8}")
    inter = collections.defaultdict(lambda: [0.0, 0])
    for g, s, r in zip(fit["keys"]["reg"], fit["keys"]["sec"], fit["resid"]):
        cell = inter[(g, s)]
        cell[0] += r
        cell[1] += 1
    p("   what the additive model misses: mean residual per region x heading (n>=300)")
    p("          " + " ".join(f"{s:>7}" for s in SECTOR_NAMES))
    for g in range(ROWS * COLS):
        vals = [f"{inter[(g, s)][0] / inter[(g, s)][1] * scale:+7.3f}" if inter[(g, s)][1] >= 300 else " " * 7
                for s in range(SECTORS)]
        p(f"     {region_name(g):>3}  " + " ".join(vals))
    res = {"mu": fit["mu"], "region": fit["eff"]["reg"], "sector": fit["eff"]["sec"],
           "region_all": pos["eff"]["reg"], "ci_region": ci_r, "ci_sector": ci_s, "ci_region_all": ci_p,
           "n_region": fit["cnt"]["reg"], "n_sector": fit["cnt"]["sec"], "var_base": vb, "var_full": vf,
           "inter": {f"{region_name(g)}/{SECTOR_NAMES[s]}": v for (g, s), v in inter.items()}}
    return outcome, "\n".join(lines), res


_DATA = None


def _worker(job):
    outcome, scale, unit = job
    return run_outcome((_DATA, outcome, scale, unit))


def analyse(era: str | None, out_path: str | None, mods: str | None = None, frame: str = "screen") -> None:
    global _DATA
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    d = load()
    _DATA = data = prepare(d, era, mods, frame)
    del d
    reps = set(data["rep"])
    head = (f"era {era or 'all'}, mods {mods or 'all'}, frame {frame}: {len(reps)} replays on {data['n_map']} maps, {len(data['rep'])} judged objects; "
            f"heading kept where spacing >= {MIN_SPACING} radii: {sum(s >= 0 for s in data['sec'])}")
    # Not multiprocessing.Pool: when a worker is OOM-killed it quietly replaces it and waits
    # forever for the lost task. The executor raises BrokenProcessPool instead. Each worker
    # holds several 5M-element lists, so run one analysis at a time on a 16GB box.
    with ProcessPoolExecutor(len(OUTCOMES), mp_context=mp.get_context("fork")) as pool:
        results = list(pool.map(_worker, OUTCOMES))
    text = head + "\n\n" + "\n\n".join(t for _, t, _ in results) + "\n"
    if out_path:
        open(out_path, "w").write(text)
        json.dump({o: r for o, _, r in results}, open(out_path.rsplit(".", 1)[0] + ".json", "w"))
    print(text)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "analyse"
    if cmd == "reduce":
        reduce()
    else:
        era = sys.argv[sys.argv.index("--era") + 1] if "--era" in sys.argv else None
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        mods = sys.argv[sys.argv.index("--mods") + 1] if "--mods" in sys.argv else None
        frame = sys.argv[sys.argv.index("--frame") + 1] if "--frame" in sys.argv else "screen"
        analyse(era, out, mods, frame)
