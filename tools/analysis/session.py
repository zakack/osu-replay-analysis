"""How did the first session on tablet regime D compare with what came before?

D is the Circular Area preset 100726-circular-adjustment, applied 2026-10-07 20:43 local: the
forward elliptical grid's truncation went 0.9 -> 0.8 and its slot was enabled on Q4, which had
been linear. The regimes, from the otd-daemon journal (every settings apply is logged in full):

    A  2026-10-06 13:14-13:39  quadrant fix: forward 0.9 on Q2+Q4, inverse 0.6 on Q1(+Q3)
    B  2026-10-06 13:39-18:22  forward 0.9 on Q2 only (the Q4 slot duplicated the mapping, ignored)
    C  2026-10-06 18:22 -> 2026-10-07 20:43  forward 0.9 on Q2, inverse 0.6 on Q1+Q3
    D  2026-10-07 20:43 ->     forward 0.8 on Q2+Q4, inverse 0.6 on Q1+Q3

Which screen quadrant each Q number is has not been confirmed (OTD flips both tablet axes),
so read results by screen region, not by Q.

Two outputs. A per-play table, because the session repeats maps played under B and C and a
same-map comparison needs no model. And one joint additive model per outcome on T4 + C + D,

    y = replay + pattern + region[group] + heading[group] + e

with region and heading split by regime group and centred within each group, so a group's
cells read as "this place / direction against that group's own average". The D cells are
thirteen plays; the map-cluster bootstrap says how little that is.

Hit error is the control. A tablet mapping cannot move tap timing, and D is also the first
session launched with 24 cores rather than Lutris's 4, so a hit shift points at the client.

    python3 tools/analysis/session.py [--out build/session-D.txt]
"""
from __future__ import annotations

import collections
import math
import os
import random
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
from where import (BOOT, COLS, NAN, ROWS, SECTOR_NAMES, SECTORS, load, prepare,  # noqa: E402
                   region_name)

STAMP = re.compile(r"\((\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\)\.osr$")
TITLE = re.compile(r" playing (.*) \(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\)\.osr$")

REGIMES = (
    ("A", "2026-10-06 13:14", "2026-10-06 13:39"),
    ("B", "2026-10-06 13:39", "2026-10-06 18:22"),
    ("C", "2026-10-06 18:22", "2026-10-07 20:43"),
    ("D", "2026-10-07 20:43", "9999"),
)
GROUPS = ("T4", "C", "D")
NREG = ROWS * COLS
OUTCOMES = (("miss", 100, "pp"), ("aim", 1, "r"), ("along", 1, "r"), ("across", 1, "r"),
            ("centre", 1, "r"), ("dx", 1, "r"), ("dy", 1, "r"), ("hit", 1, "ms"))
THIN = 200


def regime_of(replay: dict) -> str | None:
    m = STAMP.search(replay["name"])
    if not m:
        return None
    when = f"{m.group(1)} {m.group(2)}:{m.group(3)}"
    for name, lo, hi in REGIMES:
        if lo <= when < hi:
            return name
    return "T4" if replay["era"] == "T4" else None


def short(replay: dict, width: int = 46) -> str:
    m = TITLE.search(replay["name"])
    t = m.group(1) if m else replay["name"]
    if len(t) <= width:
        return t
    # Keep the difficulty: several training maps share one title.
    diff = t[t.rfind(" ["):] if t.endswith("]") and " [" in t else ""
    diff = diff if len(diff) < width - 12 else diff[:width - 13] + "…]"
    return t[:width - len(diff) - 1] + "…" + diff


def local_time(replay: dict) -> str:
    m = STAMP.search(replay["name"])
    return f"{m.group(1)[5:]} {m.group(2)}:{m.group(3)}"


# ------------------------------------------------------------------------------------------
# Per play
# ------------------------------------------------------------------------------------------

def per_play(d, regime: dict[int, str]):
    ints, cols = d["ints"], d["cols"]
    acc = collections.defaultdict(lambda: {"res": [0, 0, 0, 0], "aim": [], "hit": []})
    for i, k in enumerate(ints["rep"]):
        if k not in regime:
            continue
        a = acc[k]
        a["res"][ints["result"][i]] += 1
        if ints["press"][i]:
            v = cols["aim"][i]
            if v == v:
                a["aim"].append(v)
        # Hit error with the per-beatmap offset in force removed (offsets.py); a replay whose
        # offset is ambiguous contributes no timing at all rather than a guessed one.
        rep = d["replays"][k]
        h = cols["hit"][i] - rep["offset"] if rep["offsetStatus"] != "ambiguous" else NAN
        if h == h:
            a["hit"].append(h)
    return acc


def play_row(stats) -> tuple:
    res = stats["res"]
    n = sum(res)
    aim = statistics.fmean(stats["aim"]) if stats["aim"] else NAN
    hit = statistics.fmean(stats["hit"]) if stats["hit"] else NAN
    sd = statistics.pstdev(stats["hit"]) if len(stats["hit"]) > 1 else NAN
    acc = (300 * res[0] + 100 * res[1] + 50 * res[2]) / (300 * n) if n else NAN
    return n, res, acc, aim, hit, sd


def table(d, regime, p):
    reps = d["replays"]
    stats = per_play(d, regime)
    d_maps = {reps[k]["md5"] for k, g in regime.items() if g == "D"}
    bc_maps = {reps[k]["md5"] for k, g in regime.items() if g in ("B", "C")}

    p("Per play. acc is the ScoreV1-style 300/100/50 ratio over judged objects; aim is mean")
    p("|cursor - target| at the press in radii; hit is mean signed error (ms, + late) net of the per-map offset, blank where that offset is ambiguous; UR = 10 x SD.")
    p("* = a D map also played under B or C.")
    p(f"  {'map':<46} {'time':>11} {'reg':>3} {'n':>5} {'300/100/50/X':>16} {'acc':>6} {'aim':>6} {'hit':>6} {'UR':>6}")
    rows = sorted((k for k, g in regime.items() if g in ("A", "B", "C", "D")), key=lambda k: local_time(reps[k]))
    for k in rows:
        n, res, acc, aim, hit, sd = play_row(stats[k])
        star = "*" if regime[k] == "D" and reps[k]["md5"] in bc_maps else " "
        p(f" {star}{short(reps[k]):<46} {local_time(reps[k]):>11} {regime[k]:>3} {n:>5} "
          f"{'/'.join(map(str, res)):>16} {100 * acc:6.2f} {aim:6.3f} {hit:+6.1f} {10 * sd:6.1f}")

    # T4 history of the D maps, one row per map.
    p("")
    p("Earlier (T4) plays of the D maps, pooled per map:")
    by_map = collections.defaultdict(list)
    for k, g in regime.items():
        if g == "T4" and reps[k]["md5"] in d_maps:
            by_map[reps[k]["md5"]].append(k)
    if not by_map:
        p("  none")
    for md5, ks in sorted(by_map.items(), key=lambda kv: short(d["replays"][kv[1][0]])):
        rs = [play_row(stats[k]) for k in ks]
        mean = lambda j: statistics.fmean(r[j] for r in rs if r[j] == r[j])
        misses = statistics.fmean(r[1][3] for r in rs)
        p(f"  {short(reps[ks[0]]):<46} {len(ks):>3} plays  miss {misses:5.1f}  acc {100 * mean(2):6.2f}  "
          f"aim {mean(3):6.3f}  hit {mean(4):+6.1f}  UR {10 * mean(5):6.1f}")

    # Same-map summary: each D map against its B/C plays and T4 plays.
    p("")
    p("Same map, D against earlier regimes (means over plays):")
    p(f"  {'map':<46} {'regime':>6} {'plays':>5} {'miss':>5} {'acc':>6} {'aim':>6} {'hit':>6} {'UR':>6}")
    for md5 in sorted(d_maps, key=lambda m: min(local_time(reps[k]) for k, g in regime.items() if reps[k]["md5"] == m and g == "D")):
        for g in ("T4", "B", "C", "D"):
            ks = [k for k, gg in regime.items() if gg == g and reps[k]["md5"] == md5]
            if not ks:
                continue
            rs = [play_row(stats[k]) for k in ks]
            mean = lambda j: statistics.fmean(r[j] for r in rs if r[j] == r[j])
            p(f"  {short(reps[ks[0]]):<46} {g:>6} {len(ks):>5} {statistics.fmean(r[1][3] for r in rs):5.1f} "
              f"{100 * mean(2):6.2f} {mean(3):6.3f} {mean(4):+6.1f} {10 * mean(5):6.1f}")


# ------------------------------------------------------------------------------------------
# Location model, split by regime group
# ------------------------------------------------------------------------------------------

def fit(data, outcome: str, groups: list[int], rounds: int = 10):
    """where.backfit with region and heading split by regime group, each group's block
    centred on its own count-weighted mean. Directed objects only, as where's joint model."""
    yc, sec, reg = data["y"][outcome], data["sec"], data["reg"]
    idx = [i for i in range(len(yc)) if yc[i] == yc[i] and sec[i] >= 0]
    y = [yc[i] for i in idx]
    keys = {
        "rep": [data["rep"][i] for i in idx],
        "pat": [data["pat"][i] for i in idx],
        "reg": [reg[i] + NREG * groups[i] for i in idx],
        "sec": [sec[i] + SECTORS * groups[i] for i in idx],
    }
    block = {"reg": NREG, "sec": SECTORS}
    size = {"rep": data["n_rep"], "pat": data["n_pat"], "reg": NREG * len(GROUPS), "sec": SECTORS * len(GROUPS)}
    cnt = {}
    for t, ks in keys.items():
        c = [0] * size[t]
        for g in ks:
            c[g] += 1
        cnt[t] = c
    mu = sum(y) / len(y)
    eff = {t: [0.0] * size[t] for t in keys}
    resid = [v - mu for v in y]

    # Each group's location blocks are centred; the removed mean goes to a per-group
    # intercept, which the group's (nested) replay effects would otherwise carry.
    grp = [groups[i] for i in idx]
    eff["grp"] = [0.0] * len(GROUPS)
    for _ in range(rounds):
        for t in ("rep", "pat", "reg", "sec"):
            ks, c, old = keys[t], cnt[t], eff[t]
            s = [0.0] * size[t]
            for g, r in zip(ks, resid):
                s[g] += r
            new = [(s[g] / c[g] + old[g]) if c[g] else 0.0 for g in range(size[t])]
            m = [0.0] * len(GROUPS)
            if t in block:
                b = block[t]
                for gi in range(len(GROUPS)):
                    sl = range(gi * b, (gi + 1) * b)
                    tot = sum(c[g] for g in sl)
                    if tot:
                        m[gi] = sum(new[g] * c[g] for g in sl) / tot
                        for g in sl:
                            new[g] = new[g] - m[gi] if c[g] else 0.0
                    eff["grp"][gi] += m[gi]
            delta = [a - b_ for a, b_ in zip(new, old)]
            resid = [r - delta[g] - m[q] for r, g, q in zip(resid, ks, grp)]
            eff[t] = new
    maps = [data["map"][i] for i in idx]
    return {"n": len(y), "mu": mu, "eff": eff, "cnt": cnt, "keys": keys, "resid": resid, "maps": maps}


def block_ci(f, term: str, gi: int) -> list[tuple[float, float]]:
    """Map-cluster bootstrap of one group's block, on partial residuals, centred within the
    block. Nuisance terms are held at their full-sample fit, as in where.cluster_ci."""
    b = NREG if term == "reg" else SECTORS
    lo_code = gi * b
    e, ks, resid = f["eff"][term], f["keys"][term], f["resid"]
    per = collections.defaultdict(lambda: [[0.0] * b, [0] * b])
    for m, g, r in zip(f["maps"], ks, resid):
        if lo_code <= g < lo_code + b:
            cell = per[m]
            cell[0][g - lo_code] += r + e[g]
            cell[1][g - lo_code] += 1
    maps = list(per.values())
    rng = random.Random(0)
    draws = [[] for _ in range(b)]
    for _ in range(BOOT):
        s, c = [0.0] * b, [0] * b
        for a, n in rng.choices(maps, k=len(maps)):
            for g in range(b):
                s[g] += a[g]
                c[g] += n[g]
        centre = sum(s) / sum(c)
        for g in range(b):
            if c[g]:
                draws[g].append(s[g] / c[g] - centre)
    out = []
    for xs in draws:
        xs.sort()
        out.append((xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1]) if xs else (NAN, NAN))
    return out


def location(data, groups, n_maps_d, p):
    for outcome, scale, unit in OUTCOMES:
        f = fit(data, outcome, groups)
        e, c = f["eff"], f["cnt"]
        role = "  (CONTROL: a tablet mapping cannot move tap timing)" if outcome == "hit" else ""
        p("")
        p(f"== {outcome} ({unit}), directed n={f['n']}{role}")
        for term, b, names in (("reg", NREG, [region_name(g) for g in range(NREG)]),
                               ("sec", SECTORS, list(SECTOR_NAMES))):
            ci_d = block_ci(f, term, GROUPS.index("D"))
            ci_t4 = block_ci(f, term, GROUPS.index("T4"))
            p(f"   {'region' if term == 'reg' else 'heading'}:  T4 [CI]  |  C  |  D [CI]  |  D-T4   n(T4/C/D)"
              f"   (* D-T4 outside both CIs' combined width; ~ n_D < {THIN})")
            for g in range(b):
                t4, cc, dd = (e[term][g + gi * b] for gi in range(3))
                nt4, nc, nd = (c[term][g + gi * b] for gi in range(3))
                (dl, dh), (tl, th) = ci_d[g], ci_t4[g]
                diff = dd - t4
                # Rough test: the D-T4 difference against the half-widths added in quadrature.
                half = math.hypot((dh - dl) / 2, (th - tl) / 2) if dh == dh and th == th else NAN
                flag = "~" if nd < THIN else ("*" if half == half and abs(diff) > half else " ")
                p(f"     {names[g]:>3} {t4 * scale:+7.3f} [{tl * scale:+.3f},{th * scale:+.3f}] | {cc * scale:+7.3f} | "
                  f"{dd * scale:+7.3f} [{dl * scale:+.3f},{dh * scale:+.3f}] | {diff * scale:+7.3f}{flag}  "
                  f"{nt4}/{nc}/{nd}")
        if outcome == "hit":
            # The level, not the location profile: centring removes it from the cells.
            rep_d = [k for k in set(f["keys"]["rep"]) if groups_of_rep.get(k) == "D"]
            rep_c = [k for k in set(f["keys"]["rep"]) if groups_of_rep.get(k) == "C"]
            mean = lambda ks: statistics.fmean(e["rep"][k] for k in ks) if ks else NAN
            gl = lambda name: f["mu"] + e["grp"][GROUPS.index(name)]
            p(f"   hit level (replay effect, pattern-adjusted): C {mean(rep_c) + gl('C'):+.2f} ms over {len(rep_c)} plays, "
              f"D {mean(rep_d) + gl('D'):+.2f} ms over {len(rep_d)} plays, T4 overall {gl('T4') + mean([k for k in set(f['keys']['rep']) if groups_of_rep.get(k) == 'T4']):+.2f} ms")


groups_of_rep: dict[int, str] = {}


def main():
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/session-D.txt"
    lines: list[str] = []

    def p(s=""):
        lines.append(s)
        print(s, flush=True)

    d = load()
    reps = d["replays"]
    regime = {}
    for k, r in enumerate(reps):
        g = regime_of(r)
        if g:
            regime[k] = g
    groups_of_rep.update(regime)
    counts = collections.Counter(regime.values())

    p("Regime D session vs earlier play. D = 100726-circular-adjustment, applied 2026-10-07 20:43:")
    p("forward elliptical grid truncation 0.9 -> 0.8, and enabled on Q4 (linear before). Which screen")
    p("quadrant each Q is has not been confirmed (OTD flips both tablet axes): read by screen region.")
    p("D is also the first 24-core launch on record, so hit timing is a control for the client.")
    p(f"plays by regime: {dict(sorted(counts.items()))}")
    p("")
    table(d, regime, p)

    data = prepare(d, None)
    del d
    keep = [i for i, k in enumerate(data["rep"]) if regime.get(k) in GROUPS]
    sub = {"y": {o: [data["y"][o][i] for i in keep] for o in data["y"]}}
    for t in ("rep", "map", "pat", "reg", "sec"):
        sub[t] = [data[t][i] for i in keep]
    sub["n_rep"], sub["n_pat"] = data["n_rep"], data["n_pat"]
    groups = [GROUPS.index(regime[k]) for k in sub["rep"]]
    n_maps_d = len({sub["map"][i] for i, g in enumerate(groups) if g == 2})
    del data
    p("")
    p(f"Location model on T4 + C + D: {len(keep)} judged objects; D spans {n_maps_d} maps.")
    p("Region cells are a 4x3 grid of the playfield (T/M/B rows, columns 1-4 left to right);")
    p("heading is the direction of travel into the object on screen. Effects are centred within")
    p("each regime, so they compare places, not overall level.")
    location(sub, groups, n_maps_d, p)

    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"written: {out}")


if __name__ == "__main__":
    main()
