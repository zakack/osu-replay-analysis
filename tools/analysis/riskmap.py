"""Can a player's own cells predict where they will miss on a map they have not played?

The measurement behind a "personal strain graph": fit miss rates per cell on one half of the
player's beatmaps, predict every object on the other half, and see whether the predictions
find the misses. Nothing about the prediction is a claim until this says it is.

Train and test are disjoint *beatmaps*, both directions, pooled -- the same split as
`unitsize.py` and `patternpredict.py`, for the same reason: a diagnosis is a claim about the
player, so it has to survive on maps it was not fitted to.

**The model.** An object's predicted risk is its cell's miss rate on the training maps. A
cell with fewer than `--min-train` training objects backs off to its parent -- the same key
with the last axis dropped -- and so on up to the training base rate, never straight to the
base rate: a rare cell still knows its snap. Keys are ordered so the drop goes finest axis
first: kind, snap, spacing, run position, angle.

**What is reported**, all on held-out maps:

    capture   share of the misses that fall in the riskiest 1/5/10/20% of objects. Chance is
              the percentage itself, so "10%: 31%" is a lift of 3.1.
    AUC       the chance a random miss was rated riskier than a random hit.
    calibration  predicted against observed miss rate, by band of predicted risk.
    first     the same capture, counting only a miss with no miss in the eight judged objects
              before it, against only the objects that could be one. Misses cascade: a broken
              stream is five misses and the model that flagged the stream would be credited
              five times. If lift survives here, it predicts the failure, not its aftermath.
    map       across replays, does the predicted miss rate track the actual one? This is the
              aggregate the risk map would claim, "this map will go badly for you".
    section   within one replay, across its ten-second sections, does predicted risk find the
              sections where the misses were? This is the strain graph's claim, and the hard
              one: the map-level answer can be right from difficulty alone, this cannot.
              Correlation after centring within each replay, so a hard map's sections do not
              all score for being on a hard map.

Two traps this was designed around, from earlier work here. Run position is in the best
cells, and press attribution in a stream can move a miss by one object; comparing the
schemes with and without run position shows how much of the lift is "streams break", which
is real but narrower than "the player misses here". And the training floor is swept,
because at a few percent base rate a cell of fifty has a standard error the size of the
spread between cells.

**Plays kept**: the player's, at the map's own rate and difficulty -- nomod plus HD, NF, MR,
SD and PF. Rate mods change what a snap demands, HR/EZ/DA change the windows and the read,
RX/AP play half of it for you, NS hides the cursor, Classic is unported. A replay whose mods
are unknown is dropped rather than guessed.

    PYTHONPATH=tools python3 tools/analysis/riskmap.py [--min-train 50] [--limit N]

Measured 2026-10-13 on 13,731 of the player's replays: 2,930 beatmaps, 6,203,378 objects,
244,252 misses (3.94%). Training floors 50, 200 and 500 agree to a tenth of a point, so at
this size the floor does not matter.

**Yes, and the lift is in the geometry.** Share of held-out misses in the riskiest objects:

    scheme                                   top 1%  top 5%  top 10%  top 20%   AUC
    constant (base rate)                       1.0%    5.0%    10.0%    20.0%  0.500
    kind x snap                                1.4%    6.9%    13.9%    27.7%  0.576
    kind x snap x run position                 2.0%    9.6%    17.9%    32.2%  0.607
    kind x snap x spacing                      3.4%   11.5%    20.4%    35.0%  0.625
    kind x snap x spacing x |angle|            3.6%   11.6%    20.8%    35.6%  0.628
    kind x snap x spacing x run position       3.9%   12.6%    21.9%    36.9%  0.641
    kind x snap x spacing x run x |angle|      3.9%   12.6%    21.5%    37.2%  0.640

The riskiest tenth of objects on a map the model has never seen holds 22% of the misses,
a lift of 2.2; the riskiest hundredth holds 3.9%. Spacing does most of it -- snap alone
to snap x spacing is 13.9% to 20.4% -- and run position adds 1.5 points on top. So the
lift is not "streams break": run position on its own reaches 17.9%, and with it removed the
model keeps 20.4%. |angle| buys almost nothing, the third measurement here to say so.

**It predicts the failure, not the aftermath.** On first misses only, the best scheme puts
23.0% in its top tenth, AUC 0.648 -- slightly *better* than on all misses. A cascade is not
what it learned.

**It is calibrated.** Predicted against observed, by band of predicted risk:

    predicted   1.60%  2.48%  3.45%  4.81%  7.02%  9.00%  14.78%
    observed    1.92%  2.54%  3.32%  4.82%  6.71%  8.58%  14.32%

Within a few tenths of a point from 1.6% to 15%; only the thin ends drift (0.5% predicted
is 1.9% observed on 15k objects, 24% is 21% on 8k). A risk the strain graph shows is a
risk the player actually runs.

**It finds the bad sections better than the bad maps.** Within a replay, across ten-second
sections, the best scheme's mean predicted risk correlates 0.33 with the section's actual
miss rate (the constant baseline: 0). On counts it is 0.47 against density's 0.37. Across
replays the map-level correlation is only 0.21, and that is expected: the same map played
on different days gets the same prediction and very different results, so form, fatigue
and attempt number sit entirely outside it. The strain graph's claim -- *where* on this
map -- is the stronger of the two.

What it is not: an AUC of 0.64 leaves most misses outside the riskiest tenth. Shown per
object it would mostly be wrong; shown as a curve over the map, aggregated, it is
calibrated and finds the sections. That is the form the strain graph should take.
"""
from __future__ import annotations

import argparse
import array
import collections
import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bins import load  # noqa: E402
from bins.schema import ANGLE, RUN_POSITION, SPACING  # noqa: E402
import where  # noqa: E402

ALLOWED = frozenset({"HD", "NF", "MR", "SD", "PF"})
PLAYER = "zaksynack"
CLEAN = 8          # judged objects without a miss before one counts as a first miss
SECTION = 10_000.0
CAPTURE = (.01, .05, .10, .20)

# Each scheme is the axes it keys on, in back-off order: the last is dropped first.
SCHEMES = {
    # Every object at the training base rate. It captures exactly its share at every depth,
    # and its section correlation is what object density alone produces -- the floor every
    # real scheme has to clear.
    "constant (base rate)":                  (),
    "kind x snap":                           ("kind", "snap"),
    "kind x snap x spacing":                 ("kind", "snap", "spacing"),
    "kind x snap x spacing x |angle|":       ("kind", "snap", "spacing", "angle"),
    "kind x snap x run position":            ("kind", "snap", "run"),
    "kind x snap x spacing x run position":  ("kind", "snap", "spacing", "run"),
    "kind x snap x spacing x run x |angle|": ("kind", "snap", "spacing", "run", "angle"),
}


def fold(md5: str) -> int:
    return int(hashlib.blake2b(md5.encode(), digest_size=8).hexdigest(), 16) & 1


def axes(o) -> dict[str, str]:
    """Every axis an object can be keyed on, as strings; '-' where it has no value."""
    def s(v):
        return "-" if v is None else str(v)
    return {
        "kind": o.target,
        "snap": s(o.snap),
        "spacing": s(SPACING.bin(o.spacing) if o.spacing is not None else None),
        "run": s(RUN_POSITION.bin(o.run_index) if o.run_index is not None else None),
        "angle": s(ANGLE.bin(abs(o.angle)) if o.angle is not None else None),
    }


def load_objects(limit: int | None):
    """One pass over the player's geometry: per object its replay, time, fold, miss, and a
    key id per scheme. Keys are interned to ids so four million objects fit in arrays."""
    mods = where.mods_by_replay()
    keep_mods = collections.Counter()

    replays: list[str] = []
    replay_id: dict[str, int] = {}
    maps: list[str] = []
    rid = array.array("i")
    when = array.array("d")
    halves = array.array("b")
    missed = array.array("b")
    key_ids = {name: array.array("i") for name in SCHEMES}
    keys = {name: {} for name in SCHEMES}

    dropped = collections.Counter()
    for o in load.observations(load.PLAYER, players={PLAYER}):
        if o.target not in ("circle", "slider"):
            continue
        m = mods.get(o.replay)
        if m is None:
            dropped["mods unknown"] += 1
            continue
        if not m <= ALLOWED:
            dropped["mods not kept"] += 1
            continue
        if o.replay not in replay_id:
            if limit and len(replays) >= limit:
                continue
            replay_id[o.replay] = len(replays)
            replays.append(o.replay)
            maps.append(o.beatmap_md5)
            keep_mods[" ".join(sorted(m)) or "nomod"] += 1
        rid.append(replay_id[o.replay])
        when.append(o.start_time)
        halves.append(fold(o.beatmap_md5))
        missed.append(1 if o.result == "Miss" else 0)
        a = axes(o)
        for name, wanted in SCHEMES.items():
            k = tuple(a[x] for x in wanted)
            table = keys[name]
            if k not in table:
                table[k] = len(table)
            key_ids[name].append(table[k])
    return {"replays": replays, "maps": maps, "rid": rid, "t": when, "fold": halves,
            "miss": missed, "key_ids": key_ids, "keys": keys,
            "dropped": dropped, "mods": keep_mods}


def first_misses(d):
    """Per object: is it eligible to be a first miss (no miss among the CLEAN judged objects
    before it in its replay), and is it one? Objects are put in time order per replay first;
    the geometry file's order is not relied on."""
    n = len(d["miss"])
    order = sorted(range(n), key=lambda i: (d["rid"][i], d["t"][i]))
    eligible = array.array("b", bytes(n))
    first = array.array("b", bytes(n))
    last_replay, since = -1, CLEAN
    for i in order:
        if d["rid"][i] != last_replay:
            last_replay, since = d["rid"][i], CLEAN
        if since >= CLEAN:
            eligible[i] = 1
            first[i] = d["miss"][i]
        since = 0 if d["miss"][i] else since + 1
    return eligible, first


def key_counts(d, name, eligible, first):
    """One pass per scheme: per key id, per fold, [objects, misses, eligible, first misses].
    Everything that varies with the training floor is computed from these, not from objects."""
    ids = d["key_ids"][name]
    miss, halves = d["miss"], d["fold"]
    per_key = collections.defaultdict(lambda: [0] * 8)
    for i in range(len(ids)):
        c = per_key[ids[i]]
        h = 4 * halves[i]
        c[h] += 1
        c[h + 1] += miss[i]
        c[h + 2] += eligible[i]
        c[h + 3] += first[i]
    return per_key


def predictions(d, name, per_key, min_train):
    """Per training fold, key id -> predicted risk, backing off along the scheme's axes."""
    by_id = {v: k for k, v in d["keys"][name].items()}
    counts = collections.defaultdict(lambda: [0, 0, 0, 0])
    for kid, c in per_key.items():
        k = by_id[kid]
        for depth in range(1, len(k) + 1):
            p = counts[k[:depth]]
            p[0] += c[0]
            p[1] += c[1]
            p[2] += c[4]
            p[3] += c[5]
    base = []
    for h in (0, 1):
        n = sum(c[4 * h] for c in per_key.values())
        m = sum(c[4 * h + 1] for c in per_key.values())
        base.append(m / n if n else 0.0)
    out = []
    for train in (0, 1):
        pred = {}
        for kid in per_key:
            k = by_id[kid]
            value = base[train]
            for depth in range(len(k), 0, -1):
                c = counts[k[:depth]]
                if c[2 * train] >= min_train:
                    value = c[2 * train + 1] / c[2 * train]
                    break
            pred[kid] = value
        out.append(pred)
    return out


def grouped(per_key, preds, first=False):
    """Held-out objects grouped by predicted risk, one table per held-out fold:
    [{p: [n, positives]}, {p: [n, positives]}]. With first, only the objects that could be a
    first miss, and only first misses count.

    Per fold, because each fold's predictions come from a different training half with its
    own base rate. Pooled into one ranking, every object of the fold whose training half
    ran hotter outranks every object of the other, and a model with no information at all
    scores below chance -- which the constant baseline did, before this.
    """
    tables = [collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])]
    for kid, c in per_key.items():
        for test in (0, 1):
            n, m = (c[4 * test + 2], c[4 * test + 3]) if first else (c[4 * test], c[4 * test + 1])
            if n == 0:
                continue
            g = tables[test][preds[1 - test][kid]]
            g[0] += n
            g[1] += m
    return tables


def capture(tables, q):
    """Share of positives among the riskiest q of objects, ranked within each held-out fold
    and pooled. A tied block at the boundary is shared pro rata."""
    got = positives = 0.0
    for groups in tables:
        total = sum(g[0] for g in groups.values())
        positives += sum(g[1] for g in groups.values())
        budget = q * total
        for p in sorted(groups, reverse=True):
            n, m = groups[p]
            take = min(n, budget)
            got += m * (take / n)
            budget -= take
            if budget <= 0:
                break
    return got / positives if positives else 0.0


def auc(tables):
    """P(a random positive outranks a random negative from the same fold), ties half,
    pooled over folds by their number of pairs."""
    area = pairs = 0.0
    for groups in tables:
        pos = sum(g[1] for g in groups.values())
        neg = sum(g[0] - g[1] for g in groups.values())
        below = 0
        for p in sorted(groups):
            n, m = groups[p]
            area += m * (below + .5 * (n - m))
            below += n - m
        pairs += pos * neg
    return area / pairs if pairs else float("nan")


# Bands of predicted risk for the calibration table. Deciles of objects do not work here:
# a cell's whole held-out population shares one predicted risk, a block of a million objects
# can span several deciles, and it cannot honestly be split, since every object in it was
# predicted the same.
BANDS = (0.0, .01, .02, .03, .04, .06, .08, .12, .20, 1.01)


def calibration(groups):
    """(mean predicted, observed, n) per band of predicted risk."""
    rows = []
    for lo, hi in zip(BANDS, BANDS[1:]):
        n = m = 0
        p_sum = 0.0
        for p, (count, misses) in groups.items():
            if lo <= p < hi:
                n += count
                m += misses
                p_sum += p * count
        if n:
            rows.append((p_sum / n, m / n, n))
    return rows


def correlation(pairs_by_group):
    """Pooled within-group correlation of (x, y) pairs, each group centred on itself."""
    sxx = syy = sxy = 0.0
    for pairs in pairs_by_group.values():
        if len(pairs) < 2:
            continue
        mx = sum(x for x, _ in pairs) / len(pairs)
        my = sum(y for _, y in pairs) / len(pairs)
        for x, y in pairs:
            sxx += (x - mx) ** 2
            syy += (y - my) ** 2
            sxy += (x - mx) * (y - my)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")


def aggregates(d, name, preds):
    """Map level: across replays, predicted vs actual miss rate. Section level: within each
    replay, across its ten-second sections, two ways -- predicted against actual misses
    (counts, which object density inflates for every model alike) and mean predicted risk
    against actual miss rate (rates, which density cannot)."""
    ids = d["key_ids"][name]
    per_replay = collections.defaultdict(lambda: [0, 0.0, 0, 0])
    sections = collections.defaultdict(lambda: [0, 0.0, 0])
    for i in range(len(ids)):
        p = preds[1 - d["fold"][i]][ids[i]]
        r = d["rid"][i]
        c = per_replay[r]
        c[0] += 1
        c[1] += p
        c[2] += d["miss"][i]
        c[3] = d["fold"][i]
        s = sections[(r, int(d["t"][i] // SECTION))]
        s[0] += 1
        s[1] += p
        s[2] += d["miss"][i]
    # Map level is one group, so the correlation is plain across replays. Replays shorter
    # than fifty judged objects say little about a rate and are left out.
    #
    # Within each fold, because the two folds' predictions come from training halves with
    # different base rates; pooled, the correlation reads which half ran hotter. Rounded,
    # because identical risks summed and divided by different counts differ in the last
    # bit, the count correlates with the miss rate, and a model with no information then
    # scores a correlation out of floating-point noise -- the constant baseline did.
    across = collections.defaultdict(list)
    for n, p, m, h in per_replay.values():
        if n >= 50:
            across[h].append((round(p / n, 9), m / n))
    counts, rates = collections.defaultdict(list), collections.defaultdict(list)
    for (r, _), (n, p, m) in sections.items():
        counts[r].append((round(p, 9), m))
        if n >= 5:
            rates[r].append((round(p / n, 9), m / n))
    return correlation(across), correlation(counts), correlation(rates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-train", type=int, nargs="+", default=[50, 200, 500],
                        help="training floors to sweep (default 50 200 500)")
    parser.add_argument("--limit", type=int, help="only the first N replays, for a quick run")
    args = parser.parse_args()

    d = load_objects(args.limit)
    n = len(d["miss"])
    misses = sum(d["miss"])
    print(f"{len(d['replays']):,} replays, {len(set(d['maps'])):,} beatmaps, {n:,} objects, "
          f"{misses:,} misses ({misses / n:.2%})")
    print("  kept by mods: " + ", ".join(f"{k} {v:,}" for k, v in d["mods"].most_common()))
    print("  objects dropped: " + ", ".join(f"{k} {v:,}" for k, v in d["dropped"].items()))
    eligible, first = first_misses(d)
    print(f"  first misses: {sum(first):,} among {sum(eligible):,} eligible objects "
          f"({sum(first) / max(1, sum(eligible)):.2%})\n")

    head = "".join(f"{f'top {q:.0%}':>9}" for q in CAPTURE)
    counted = {name: key_counts(d, name, eligible, first) for name in SCHEMES}
    for floor in args.min_train:
        print(f"== training floor {floor}: share of held-out misses in the riskiest objects\n")
        print(f"  {'scheme':<40}{head}{'AUC':>8}  {'first top 10%':>14}{'AUC':>7}")
        for name in SCHEMES:
            preds = predictions(d, name, counted[name], floor)
            g = grouped(counted[name], preds)
            gf = grouped(counted[name], preds, first=True)
            line = f"  {name:<40}" + "".join(f"{capture(g, q):>9.1%}" for q in CAPTURE)
            line += f"{auc(g):>8.3f}  {capture(gf, .10):>14.1%}{auc(gf):>7.3f}"
            print(line)
        print()

    # The aggregates at the lowest floor only: they move little with it and cost a pass
    # over every object each.
    floor = args.min_train[0]
    print(f"== aggregates, floor {floor}: does it find the bad maps, and the bad sections?\n")
    print(f"  {'scheme':<40}{'map r':>8}{'section r, counts':>19}{'section r, rates':>18}")
    for name in SCHEMES:
        across, within, rates = aggregates(d, name, predictions(d, name, counted[name], floor))
        def f(v, w):
            return f"{'-':>{w}}" if math.isnan(v) else f"{v:>{w}.3f}"
        print(f"  {name:<40}{f(across, 8)}{f(within, 19)}{f(rates, 18)}")
    print()

    # Calibration for the scheme the earlier work found best on misses, at the lowest floor.
    best = "kind x snap x spacing x run position"
    preds = predictions(d, best, counted[best], args.min_train[0])
    print(f"== calibration, {best}, floor {args.min_train[0]}: held-out objects by band of "
          f"predicted risk\n")
    print(f"  {'predicted':>10}{'observed':>10}{'objects':>11}")
    merged = collections.defaultdict(lambda: [0, 0])
    for table in grouped(counted[best], preds):
        for p, (n, m) in table.items():
            merged[p][0] += n
            merged[p][1] += m
    for p, m, count in calibration(merged):
        print(f"  {p:>10.2%}{m:>10.2%}{count:>11,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
