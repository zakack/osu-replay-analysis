"""Choose the sampling interval and depth from a complete capture, offline.

A gapless capture is a superset of every (interval, depth) configuration, so the whole grid
can be evaluated by replaying the log rather than by running the configurations live. One
capture answers all of them.

What is being optimised is not "scores seen". It is **(player, map) pairs with repeated
attempts**, because that is the unit the variance study consumes, and a configuration that
maximises scores can still yield no pairs at all. Two things decide whether a pair is caught:

  * the window must span the player's inter-attempt gap, which is the map's length plus a
    retry, so depth is really a duration and not a page count; and
  * the yield is *linear* in depth past that gap, not quadratic. Attempts cluster — a grind
    is consecutive — so once the window covers the gap the pair is caught, and further depth
    only admits more grinds.

That is also why the length distribution is reported next to the yield and not as an
afterthought. A shallow configuration catches only short maps, and since the hypothesis this
data is meant to test is *about* map length, a configuration that silently drops long maps
will produce a clean and completely wrong answer. Compare the captured pairs' lengths against
the stream's own lengths; a configuration is only usable where those two agree.

The stream carries no beatmap object, so lengths are resolved afterwards through the bulk
endpoint, for the maps that reach the analysis rather than for every map that passed by.

    python3 tools/reference/depth.py [--log build/firehose/complete.jsonl] [--baseline 1500]
"""
import collections, json, os, random, statistics, sys, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import call, token

LOG = "build/firehose/complete.jsonl"
MAPS = "build/firehose/beatmaps.json"
PAGE = 1000
INTERVALS = (300, 600, 900, 1800, 3600)
DEPTHS = (1, 2, 4, 6, 8, 12)


def arg(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


def resolve(ids, bearer):
    """Bulk-resolve beatmap metadata, fifty at a time, cached across runs."""
    known = json.load(open(MAPS)) if os.path.exists(MAPS) else {}
    missing = sorted({str(i) for i in ids} - set(known))

    for i in range(0, len(missing), 50):
        chunk = missing[i:i + 50]
        qs = urllib.parse.urlencode([("ids[]", c) for c in chunk])
        r = call(f"https://osu.ppy.sh/api/v2/beatmaps?{qs}", bearer)

        for m in (r or {}).get("beatmaps", []):
            known[str(m["id"])] = {k: m.get(k) for k in
                                   ("total_length", "hit_length", "difficulty_rating", "bpm",
                                    "count_circles", "count_sliders", "status")}

        with open(MAPS, "w") as f:
            json.dump(known, f)
        print(f"  resolved {min(i+50, len(missing))}/{len(missing)}", end="\r", flush=True)

    if missing:
        print()
    return known


def simulate(rows, interval, depth):
    """Which scores a reader polling every `interval` seconds at `depth` pages would see.

    The log is ordered and gapless, so a poll at time t sees the `depth * PAGE` scores
    immediately preceding the live edge at t. Stamps come from the capture's own batches,
    which is when a reader would have seen a score rather than when it was set.
    """
    stamps = sorted({r["fetched_at"] for r in rows})
    if not stamps:
        return []

    # index of the live edge at each poll instant
    edge, seen, last = {}, set(), 0
    for i, r in enumerate(rows):
        edge[r["fetched_at"]] = i

    polls = stamps[::max(1, round(interval / 60))]      # capture cycles once a minute
    for t in polls:
        hi = edge[t]
        seen.update(range(max(0, hi - depth * PAGE + 1), hi + 1))
    return [rows[i] for i in sorted(seen)]


def pairs(sample):
    p = collections.Counter((r["user_id"], r["beatmap_id"]) for r in sample)
    return {k: v for k, v in p.items() if v > 1}


def lengths(ids, maps):
    out = [maps.get(str(i), {}).get("total_length") for i in ids]
    return [x for x in out if x]


def main():
    log = arg("--log", LOG)
    rows = [json.loads(l) for l in open(log)]
    rows.sort(key=lambda r: r["id"])
    span = len({r["fetched_at"] for r in rows})
    print(f"{len(rows)} scores over {span} capture cycles (~{span} min)\n")

    # every map in any repeat pair, plus a random baseline for the stream's own distribution
    need = {b for (_, b) in pairs(rows)}
    baseline_ids = random.sample([r["beatmap_id"] for r in rows],
                                 min(arg("--baseline", 1500), len(rows)))
    print(f"resolving {len(need | set(baseline_ids))} beatmaps")
    maps = resolve(need | set(baseline_ids), token())
    base = lengths(baseline_ids, maps)
    base_med = statistics.median(base) if base else 0
    print(f"stream baseline: {len(base)} maps, median length {base_med:.0f}s\n")

    print(f"{'interval':>9} {'depth':>6} {'req/day':>8} {'scores':>8} {'pairs>=2':>9} "
          f"{'pairs>=3':>9} {'med len':>8} {'vs base':>8}")
    for interval in INTERVALS:
        for depth in DEPTHS:
            sample = simulate(rows, interval, depth)
            p = pairs(sample)
            ln = lengths([b for (_, b) in p], maps)
            med = statistics.median(ln) if ln else 0
            print(f"{interval//60:7d}m {depth:6d} {depth*86400//interval:8d} {len(sample):8d} "
                  f"{len(p):9d} {sum(1 for v in p.values() if v >= 3):9d} "
                  f"{med:7.0f}s {(med/base_med if base_med else 0):7.2f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
