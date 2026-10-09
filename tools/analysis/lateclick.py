"""Does the click come after the hand has already left?

The player's own account of how fast jump sections fail: the cursor gets to the circle and
the press comes as it is already heading for the next one. The higher the BPM and the longer
the jump, the more often that lands outside the circle as a miss rather than inside it as a
100 or a 50. This tests that, from ``build/clicks.csv`` (``ora clicks``).

Per judged circle or slider head, with the cursor's closest approach to the centre inside
the object's meh window either side of its start:

  never-arrived     the cursor never came within a radius
  late-press        a hit whose press came more than LATE ms after the closest approach:
                    still inside, already on the way out
  early-press       a hit pressed more than LATE ms before the closest approach
  on-arrival        a hit pressed within LATE ms of it
  late-miss         a miss with a real press attributed to it after the cursor left the
                    circle: the simulator logs every press that judged nothing (hovered
                    nothing, refused by notelock, or outside the window), and ``ora clicks``
                    hands each to the most recent object the cursor had been inside, within
                    start + meh + 100 ms and before the next object's window closes
  stray-inside      a miss whose attributed press came while the cursor was still inside but
                    judged nothing (the object was already resolved, or notelock refused it)
  went-elsewhere    a miss where the cursor was inside and the next press judged another object
  no-press          a miss where the cursor was inside and no press followed at all

"Late" for the claim is late-press plus late-miss: a real press that came after the hand's
closest approach. The question is whether, as required cursor velocity rises, a late press is more
often a miss. Velocity is geometry's requiredVelocity (radii per real ms), binned as where.py
bins it, and only replays where.py keeps (its mod filter) are used. Uncertainty is a
bootstrap over beatmaps, since repeated plays of one map are not independent.

The what-if moves every press that came after the object's time (on a hit or a late-miss)
up to D ms earlier, never earlier than the object's time, and re-judges it against the object's own hit windows (from lazer, carried in the
CSV) with the cursor's inside span. **The hand path is held fixed**: this asks what the same
movement would have scored with a better-timed finger. Notelock is ignored, so a moved press
is never refused for an earlier object still being open. No-press misses have no press to
move; what a press at the closest approach would have scored is reported separately.

Where the top-50 no-mod reference covers an object, its press-minus-closest-approach on
hits is set against the player's on the same objects, restricted to the player's plays at
normal rate, so the what-if's D can be read against the gap to top players.

Turn point (``turnTime``, ``turnDist``, ``turnSide`` from ``ora clicks``): the moment cursor
speed is lowest around the object, where the hand stops coming in and starts leaving. On fast
jumps (>= FAST radii/ms) the press is set against it for hits and late-miss misses, player
and reference, and fast-jump misses are split three ways:

  overshoot     the turn was outside the circle (turnDist > 1) and the click came within one
                recorder frame of it: the hand stopped in the wrong place and clicked there
  late click    the turn was inside the circle and the click came more than one frame after
                it: the hand was there and the finger missed the moment
  other         everything else, including no click at all

    python3 tools/analysis/lateclick.py [--out build/lateclick.txt]
"""
from __future__ import annotations

import collections
import csv
import json
import math
import os
import pickle
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
from where import VEL_EDGES, bin_of  # noqa: E402

CLICKS = "build/clicks.csv"
REFERENCE = "build/clicks-reference-nm.csv"
CACHE = "build/where.pickle"
PLAYER = "zaksynack playing"
LATE = 8.0                       # ms; about half a recorder frame either side of the approach
SHIFTS = (5, 10, 15, 20, 30)
FAST = 0.045                     # radii per real ms: the top two velocity bins and up
FRAME = 1000 / 60
BOOT = 300
RATE_MODS = {"DT", "NC", "HT", "DC"}
RESULTS = ("Great", "Ok", "Meh", "Miss")


def num(text: str) -> float | None:
    return float(text) if text else None


def load_velocity():
    """(replay, startTime) -> required velocity, and replay -> (md5, mods), for the replays
    where.py keeps."""
    with open(CACHE, "rb") as fh:
        d = pickle.load(fh)
    reps = d["replays"]
    vel, rep, t = d["cols"]["vel"], d["ints"]["rep"], d["cols"]["t"]
    out = {}
    for i in range(len(rep)):
        v = vel[i]
        if v == v:
            out[(reps[rep[i]]["name"], round(t[i], 1))] = v
    meta = {r["name"]: (r["md5"], frozenset(r["mods"])) for r in reps}
    return out, meta


def classify(r: dict) -> str | None:
    res = r["result"]
    if res not in RESULTS:
        return None
    closest, dist = num(r["closestTime"]), num(r["closestDist"])
    if closest is None or dist is None:
        return None
    press = num(r["pressTime"])
    if res != "Miss":
        if press is None:
            return None
        if press > closest + LATE:
            return "late-press"
        if press < closest - LATE:
            return "early-press"
        return "on-arrival"
    enter, exit_ = num(r["enterTime"]), num(r["exitTime"])
    if enter is None or dist > 1:
        return "never-arrived"
    stray = num(r["strayPressTime"])
    if stray is not None:
        return "late-miss" if stray > exit_ else "stray-inside"
    if num(r["firstPressAfter"]) is not None:
        return "went-elsewhere"
    return "no-press"


def judge(t: float, r: dict) -> str:
    """Re-judge a press at map time t against the object's lazer hit windows."""
    e = abs(t - float(r["startTime"]))
    if e <= float(r["great"]):
        return "Great"
    if e <= float(r["ok"]):
        return "Ok"
    if e <= float(r["meh"]):
        return "Meh"
    return "Miss"


def what_if(r: dict, kind: str, shift: float) -> str:
    """The result had a press that came after the object's time come up to ``shift`` ms
    earlier, never earlier than the object's time itself, with the hand where it was.

    Beat-relative, not hand-relative: a press after the closest approach can still be dead on
    the beat when the hand arrived early, and pulling it earlier only spoils it. The finger
    being modelled is one that is less late, not one that fires on arrival."""
    start = float(r["startTime"])
    press = float(r["pressTime"]) if kind != "late-miss" else float(r["strayPressTime"])
    if press <= start:
        return r["result"]
    t = max(start, press - shift)
    enter, exit_ = num(r["enterTime"]), num(r["exitTime"])
    if enter is None or not (enter <= t <= exit_):
        return r["result"]
    return judge(t, r)


SCORE = {"Great": 300, "Ok": 100, "Meh": 50, "Miss": 0}


def boot_ci(groups: dict, stat, n=BOOT):
    """Percentile CI of stat(list of groups) over a bootstrap of groups (beatmaps)."""
    keys = list(groups)
    rng = random.Random(0)
    draws = []
    for _ in range(n):
        sample = [groups[k] for k in rng.choices(keys, k=len(keys))]
        v = stat(sample)
        if v == v:
            draws.append(v)
    draws.sort()
    if not draws:
        return (math.nan, math.nan)
    return draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


def turn_stats(path: str, keep, fast) -> dict:
    """Press minus turn time and press-to-turn distance on fast jumps, for hits and for
    late-miss misses, and the three-way split of fast misses."""
    out = {"hit_dt": [], "hit_d": [], "miss_dt": [], "miss_d": [], "split": collections.Counter()}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not keep(r) or not r["turnTime"] or r["result"] not in RESULTS:
                continue
            if not fast(r):
                continue
            turn = float(r["turnTime"])
            tx, ty = float(r["turnX"]), float(r["turnY"])
            kind = classify(r)
            press = None
            if r["result"] != "Miss":
                press = num(r["pressTime"])
            else:
                press = num(r["strayPressTime"])
            if press is not None and kind in ("late-press", "early-press", "on-arrival", "late-miss"):
                key = "hit" if r["result"] != "Miss" else "miss"
                if key == "hit" or kind == "late-miss":
                    out[f"{key}_dt"].append(press - turn)
                    px, py = (r["pressX"], r["pressY"]) if key == "hit" else (r["strayX"], r["strayY"])
                    if px and py:
                        out[f"{key}_d"].append(math.hypot(float(px) - tx, float(py) - ty) / float(r["radius"]))
            if r["result"] == "Miss":
                td = float(r["turnDist"])
                if press is not None and td > 1 and abs(press - turn) <= FRAME:
                    out["split"]["overshoot"] += 1
                elif press is not None and td <= 1 and press - turn > FRAME:
                    out["split"]["late click"] += 1
                else:
                    out["split"]["other"] += 1
    return out


def turn_report(p, vel, meta) -> None:
    p("")
    p(f"turn point (minimum cursor speed) on fast jumps (>= {FAST} radii/ms): press minus turn (dt)")
    p("and the cursor's distance at the press from the turn point (dist, radii):")

    def fast_player(r):
        v = vel.get((r["replay"], round(float(r["startTime"]), 1)))
        return v is not None and v >= FAST

    def q(xs, unit="ms", fmt="+.1f"):
        if len(xs) < 4:
            return "n<4"
        a = statistics.quantiles(xs, n=4)
        return (f"median {format(statistics.median(xs), fmt)} {unit} "
                f"(IQR {format(a[0], fmt)}..{format(a[2], fmt)}), n={len(xs):,}")

    def both(label, st):
        p(f"  {label} hits         dt {q(st['hit_dt'])}; dist {q(st['hit_d'], 'r', '.2f')}")
        p(f"  {label} late-misses  dt {q(st['miss_dt'])}; dist {q(st['miss_d'], 'r', '.2f')}")

    player = turn_stats(CLICKS, lambda r: r["replay"].startswith(PLAYER) and r["replay"] in meta, fast_player)
    both("player   ", player)
    split = player["split"]
    tot = sum(split.values())
    if tot:
        p(f"  player fast-jump misses ({tot:,}): " + ", ".join(
            f"{k} {100 * split[k] / tot:.1f}%" for k in ("overshoot", "late click", "other")))

    if os.path.exists(REFERENCE):
        # The reference has no where.py velocity; use the player's for the same object.
        fast_objects = set()
        for (name, t), v in vel.items():
            if v >= FAST and name in meta:
                fast_objects.add((meta[name][0], t))
        ref_md5 = {os.path.basename(e["Path"]): e["BeatmapMd5"] for e in json.load(open("build/corpus-nm.json"))}
        ref = turn_stats(REFERENCE, lambda r: r["replay"] in ref_md5,
                         lambda r: (ref_md5[r["replay"]], round(float(r["startTime"]), 1)) in fast_objects)
        both("reference", ref)
        rs = ref["split"]
        rt = sum(rs.values())
        if rt:
            p(f"  reference fast-jump misses ({rt:,}): " + ", ".join(
                f"{k} {100 * rs[k] / rt:.1f}%" for k in ("overshoot", "late click", "other")))


def main() -> None:
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/lateclick.txt"
    lines: list[str] = []
    p = lines.append

    vel, meta = load_velocity()
    kinds = collections.Counter()
    # per beatmap: per velocity bin: [late, late_miss]
    per_map = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    late_rows: list[tuple[str, dict, str]] = []
    totals = collections.defaultdict(lambda: collections.Counter())   # md5 -> result counts
    lag_player = collections.defaultdict(list)                         # (md5, t) -> press - closest
    obj_bin: dict = {}                                                 # (md5, t) -> velocity bin
    by_bin_kind = collections.defaultdict(collections.Counter)
    late_lag: list[float] = []
    no_press_rows: list[dict] = []

    with open(CLICKS, newline="") as fh:
        for r in csv.DictReader(fh):
            name = r["replay"]
            if not name.startswith(PLAYER) or name not in meta:
                continue
            kind = classify(r)
            if kind is None:
                continue
            md5, mods = meta[name]
            kinds[kind] += 1
            totals[md5][r["result"]] += 1
            v = vel.get((name, round(float(r["startTime"]), 1)))
            b = bin_of(v, VEL_EDGES) if v is not None else -1
            by_bin_kind[b][kind] += 1
            if kind == "late-miss":
                late_lag.append(float(r["strayPressTime"]) - float(r["exitTime"]))
            if kind == "no-press":
                no_press_rows.append(r)
            if kind in ("late-press", "early-press", "on-arrival", "late-miss"):
                late_rows.append((md5, r, kind))
            if kind in ("late-press", "late-miss"):
                cell = per_map[md5][b]
                cell[0] += 1
                cell[1] += kind == "late-miss"
            if r["pressTime"] and r["result"] != "Miss" and not mods & RATE_MODS:
                key = (md5, round(float(r["startTime"]), 1))
                lag_player[key].append(float(r["pressTime"]) - float(r["closestTime"]))
                if b >= 0:
                    obj_bin[key] = b

    n = sum(kinds.values())
    p(f"judged circles and slider heads, player, where.py's mod filter: {n:,}")
    p(f"late = press more than {LATE:g} ms after the cursor's closest approach to the centre")
    for k in ("on-arrival", "early-press", "late-press", "late-miss", "stray-inside", "went-elsewhere",
              "no-press", "never-arrived"):
        p(f"  {k:<14}{kinds[k]:>10,}  {100 * kinds[k] / n:6.2f}%")
    miss_kinds = ("late-miss", "stray-inside", "went-elsewhere", "no-press", "never-arrived")
    misses = sum(kinds[k] for k in miss_kinds)
    p(f"  of {misses:,} misses: " + ", ".join(f"{k} {100 * kinds[k] / misses:.1f}%" for k in miss_kinds))
    if late_lag:
        q = statistics.quantiles(late_lag, n=4)
        p(f"  late-miss press after the cursor left the circle: median {statistics.median(late_lag):.0f} ms "
          f"(IQR {q[0]:.0f}-{q[2]:.0f})")

    # The claim: among late presses, the miss share rises with required velocity.
    p("")
    p("late presses that became misses, by required cursor velocity (radii/ms, real time):")
    p("  bin            late    miss%   [95% map-bootstrap CI]   late share of all judged")
    bins = sorted(b for b in by_bin_kind if b >= 0)
    groups = {m: dict(c) for m, c in per_map.items()}
    for b in bins:
        late = sum(c[b][0] for c in per_map.values() if b in c)
        lm = sum(c[b][1] for c in per_map.values() if b in c)
        if late == 0:
            continue
        lo, hi = boot_ci(groups, lambda s, b=b: (sum(g[b][1] for g in s if b in g) /
                                                 max(1, sum(g[b][0] for g in s if b in g))))
        allb = sum(by_bin_kind[b].values())
        label = f"{VEL_EDGES[b]:.3f}-{VEL_EDGES[b + 1]:.3f}" if VEL_EDGES[b + 1] != math.inf else f">{VEL_EDGES[b]:.3f}"
        p(f"  {label:<13}{late:>8,}  {100 * lm / late:6.2f}%   [{100 * lo:5.2f}, {100 * hi:5.2f}]"
          f"   {100 * late / allb:5.1f}%")

    # Trend: slope of the per-object miss indicator on log velocity, among late presses.
    def slope(sample) -> float:
        xs, ys = [], []
        for g in sample:
            for b, (late, lm) in g.items():
                if b < 0 or late == 0:
                    continue
                lo_, hi_ = VEL_EDGES[b], VEL_EDGES[b + 1] if VEL_EDGES[b + 1] != math.inf else VEL_EDGES[b] * 1.5
                x = math.log(max(1e-4, (lo_ + hi_) / 2))
                xs.append((x, late))
                ys.append(lm)
        w = sum(k for _, k in xs)
        if w == 0:
            return math.nan
        mx = sum(x * k for x, k in xs) / w
        my = sum(ys) / w
        sxx = sum(k * (x - mx) ** 2 for x, k in xs)
        sxy = sum((x - mx) * (y - my * k) for (x, k), y in zip(xs, ys))
        return sxy / sxx if sxx else math.nan

    s = slope(list(groups.values()))
    lo, hi = boot_ci(groups, slope)
    p(f"  trend: miss share per unit log-velocity {100 * s:+.2f} pp [{100 * lo:+.2f}, {100 * hi:+.2f}]"
      f" ({'holds' if lo > 0 else 'does not hold' if hi < 0 else 'inconclusive'}: CI {'excludes' if lo > 0 or hi < 0 else 'includes'} 0)")

    # What-if.
    p("")
    p("what-if: every press after the object's time up to D ms earlier, never before that time.")
    p("The hand path is held fixed; notelock is ignored. Click accuracy is over circles and")
    p("slider heads only (300/100/50/0), not lazer's full accuracy.")
    base_score = sum(SCORE[res] * c for t in totals.values() for res, c in t.items())
    base_n = sum(sum(t.values()) for t in totals.values())
    p(f"  baseline click accuracy {100 * base_score / (300 * base_n):.2f}% over {base_n:,} objects")
    per_map_gain = collections.defaultdict(lambda: [0, 0])
    for shift in SHIFTS:
        conv = collections.Counter()
        gain = 0
        for md5, r, kind in late_rows:
            new = what_if(r, kind, shift)
            old = r["result"]
            if new != old:
                conv[f"{old}->{new}"] += 1
                d = SCORE[new] - SCORE[old]
                gain += d
                if shift == 10:
                    per_map_gain[md5][0] += d
        top = ", ".join(f"{k} {v:,}" for k, v in conv.most_common(4))
        p(f"  D={shift:>2} ms: +{100 * gain / (300 * base_n):.2f} pp click accuracy; {top}")
    hyp = collections.Counter(judge(float(r["closestTime"]), r) for r in no_press_rows)
    p(f"  separately, the {len(no_press_rows):,} no-press misses with a press placed at the closest approach: "
      + ", ".join(f"{k} {v:,}" for k, v in hyp.most_common()))
    p("  biggest gains at D=10 ms, by beatmap (click accuracy on that map):")
    names = {}
    try:
        for k, v in json.load(open("build/beatmap-index.json")).items():
            names[k] = f"{v['Title'][:34]} [{v['Difficulty'][:22]}]"
    except OSError:
        pass
    ranked = sorted(per_map_gain.items(), key=lambda kv: -kv[1][0] / max(1, sum(totals[kv[0]].values())))
    for md5, (g, _) in ranked[:10]:
        nn = sum(totals[md5].values())
        p(f"    +{100 * g / (300 * nn):5.2f} pp  {names.get(md5, md5[:12])}  ({nn:,} objects)")

    turn_report(p, vel, meta)

    # Reference gap.
    p("")
    if os.path.exists(REFERENCE):
        ref = collections.defaultdict(list)
        ref_md5 = {os.path.basename(e["Path"]): e["BeatmapMd5"] for e in json.load(open("build/corpus-nm.json"))}
        with open(REFERENCE, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["pressTime"] and r["result"] in ("Great", "Ok", "Meh") and r["closestTime"]:
                    md5 = ref_md5.get(r["replay"])
                    if md5:
                        ref[(md5, round(float(r["startTime"]), 1))].append(float(r["pressTime"]) - float(r["closestTime"]))
        shared = [k for k in lag_player if k in ref and len(ref[k]) >= 5]
        diffs = collections.defaultdict(list)
        maps = collections.defaultdict(list)
        for k in shared:
            dlt = statistics.median(lag_player[k]) - statistics.median(ref[k])
            v = None
            diffs["all"].append(dlt)
            maps[k[0]].append(dlt)
        p(f"press minus closest approach on hits, player (normal rate) vs top-50 no-mod reference,")
        p(f"on {len(shared):,} shared objects across {len(maps)} beatmaps (>=5 reference presses each):")
        if shared:
            pm = statistics.median(statistics.median(lag_player[k]) for k in shared)
            rm = statistics.median(statistics.median(ref[k]) for k in shared)
            d = statistics.median(diffs["all"])
            lo, hi = boot_ci(maps, lambda s: statistics.median([x for g in s for x in g]))
            p(f"  player median {pm:+.1f} ms, reference {rm:+.1f} ms; per-object gap {d:+.1f} ms [{lo:+.1f}, {hi:+.1f}]")
            p("  by required velocity: objects, player, reference, gap [95% map-bootstrap CI]")
            for b in sorted({obj_bin[k] for k in shared if k in obj_bin}):
                ks = [k for k in shared if obj_bin.get(k) == b]
                if len(ks) < 50:
                    continue
                bm = collections.defaultdict(list)
                for k in ks:
                    bm[k[0]].append(statistics.median(lag_player[k]) - statistics.median(ref[k]))
                lo, hi = boot_ci(bm, lambda s: statistics.median([x for g in s for x in g]))
                label = f"{VEL_EDGES[b]:.3f}-{VEL_EDGES[b + 1]:.3f}" if VEL_EDGES[b + 1] != math.inf else f">{VEL_EDGES[b]:.3f}"
                p(f"    {label:<13}{len(ks):>7,}  {statistics.median(statistics.median(lag_player[k]) for k in ks):+6.1f}"
                  f"  {statistics.median(statistics.median(ref[k]) for k in ks):+6.1f}"
                  f"  {statistics.median([x for g in bm.values() for x in g]):+6.1f} [{lo:+.1f}, {hi:+.1f}]")
    else:
        p(f"no reference clicks file ({REFERENCE}); run `ora clicks --corpus build/corpus-nm.json --out {REFERENCE}`")

    text = "\n".join(lines) + "\n"
    open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
