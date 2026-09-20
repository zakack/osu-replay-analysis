"""How much does tap timing follow the cursor rather than the beat?

Measured 2026-09-20 on one map, cursor deceleration was the largest single predictor of late
tapping found anywhere in this corpus: a 12.75ms swing from hardest acceleration to hardest
deceleration, monotone across five quintiles. Then fourteen top-50 replays on a second map
showed the same coupling at the same strength -- their range brackets ours, and correlation
between a player's accuracy and their coupling is +0.005. So it is not a defect. It is how
hands work, and the reason to measure it corpus-wide is to characterise it, not to fix it.

Deceleration is cursor speed over a 60ms window before the object minus the same after. That
is a velocity difference, and CLAUDE.md rules acceleration marginal at a 17ms sampling floor
-- which is why the window spans several samples rather than adjacent frames, and why nothing
here should carry a finding on the strength of one number. What earns the result is that it is
monotone across quintiles, consistent between maps, and replicated on other players' replays.

Reads scene documents rather than the geometry CSV, because only the scene carries cursor
frames. Angles are computed from the scene's own stacked positions, which is arithmetic over
numbers the shim produced -- not a reimplementation of the geometry.

    python3 tools/analysis/coupling.py [--player zaksynack] [--window 60] [--min-objects 200]
"""
import bisect, glob, json, math, os, statistics, sys

W_DEFAULT = 60.0
HAIRPIN = math.radians(50)


def arg(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


def corr(a, b):
    if len(a) < 20:
        return float("nan")
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else float("nan")


def angle(a, b, c):
    v1 = (a["x"] - b["x"], a["y"] - b["y"])
    v2 = (c["x"] - b["x"], c["y"] - b["y"])
    return abs(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1]))


def measure(path, window):
    """Per judged object: how the cursor's speed changed through it, and how late the tap was."""
    d = json.load(open(path))
    fr = d.get("frames") or []
    if len(fr) < 40:
        return None

    T, X, Y = fr[0::4], fr[1::4], fr[2::4]

    def speed(t, lo, hi):
        i = bisect.bisect_left(T, t + lo)
        j = bisect.bisect_right(T, t + hi)
        if j - i < 2:
            return None
        travelled = sum(math.hypot(X[k + 1] - X[k], Y[k + 1] - Y[k]) for k in range(i, j - 1))
        span = T[j - 1] - T[i]
        return travelled / span if span > 0 else None

    objects = d["objects"]
    out = []

    for i, o in enumerate(objects):
        if o.get("error") is None or o.get("result") == "Miss":
            continue

        before, after = speed(o["t"], -window, 0), speed(o["t"], 0, window)
        if before is None or after is None:
            continue

        # An angle only means anything when the neighbours are close enough in time to be
        # one movement; across a break it is two unrelated aims sharing a vertex.
        bend = None
        if 0 < i < len(objects) - 1:
            if o["t"] - objects[i - 1]["t"] < 250 and objects[i + 1]["t"] - o["t"] < 250:
                bend = angle(objects[i - 1], o, objects[i + 1])

        out.append((before - after, o["error"], bend))

    return {"map": f"{d['beatmap'].get('title','?')} [{d['beatmap'].get('difficulty','?')}]",
            "rate": d.get("rate", 1.0), "mods": d.get("mods") or [], "rows": out}


def quintiles(rows, label):
    if len(rows) < 200:
        return
    cuts = sorted(r[0] for r in rows)
    edge = [cuts[int(len(cuts) * f)] for f in (0.2, 0.4, 0.6, 0.8)]
    names = ("accelerating hard", "accelerating", "steady", "decelerating", "decelerating hard")
    print(f"\n{label}  ({len(rows):,} objects)")
    print(f"  {'cursor':>22} {'n':>7} {'mean err':>10}")
    for k, name in enumerate(names):
        lo = -1e9 if k == 0 else edge[k - 1]
        hi = 1e9 if k == 4 else edge[k]
        sel = [r[1] for r in rows if lo <= r[0] < hi]
        if len(sel) >= 30:
            print(f"  {name:>22} {len(sel):7,d} {statistics.mean(sel):+9.2f}ms")
    print(f"  corr(deceleration, late) = {corr([r[0] for r in rows], [r[1] for r in rows]):+.3f}")


def main():
    player = arg("--player", "zaksynack")
    window = arg("--window", W_DEFAULT)
    floor = arg("--min-objects", 200)

    files = [p for p in sorted(glob.glob("build/scene/*.json"))
             if os.path.basename(p).startswith(player)]
    print(f"{len(files)} scene documents for {player!r}\n")

    everything, per_map = [], {}
    for p in files:
        m = measure(p, window)
        if not m or len(m["rows"]) < 50:
            continue
        everything.extend(m["rows"])
        per_map.setdefault(m["map"], []).extend(m["rows"])

    quintiles(everything, "WHOLE CORPUS")

    bands = ((0, HAIRPIN, "hairpin  <50 deg"), (HAIRPIN, 1.9, "50-109"),
             (1.9, 2.5, "109-143"), (2.5, 3.15, "143-180 straight"))
    print(f"\ncoupling by the geometry at the object:")
    print(f"  {'angle':>20} {'n':>8} {'corr':>8} {'mean err':>10}")
    for lo, hi, name in bands:
        sel = [r for r in everything if r[2] is not None and lo <= r[2] < hi]
        if len(sel) < 100:
            continue
        print(f"  {name:>20} {len(sel):8,d} {corr([r[0] for r in sel], [r[1] for r in sel]):+7.3f} "
              f"{statistics.mean(r[1] for r in sel):+9.2f}ms")

    ranked = [(corr([r[0] for r in v], [r[1] for r in v]), k, len(v))
              for k, v in per_map.items() if len(v) >= floor]
    ranked = [r for r in ranked if r[0] == r[0]]
    ranked.sort(reverse=True)
    print(f"\nper map ({len(ranked)} with {floor}+ objects): "
          f"median {statistics.median(r[0] for r in ranked):+.3f}, "
          f"range {ranked[-1][0]:+.3f} to {ranked[0][0]:+.3f}")
    for c, name, n in ranked[:5]:
        print(f"   strongest {c:+.3f}  {n:6,d}  {name[:54]}")
    for c, name, n in ranked[-3:]:
        print(f"   weakest   {c:+.3f}  {n:6,d}  {name[:54]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
