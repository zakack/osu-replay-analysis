"""Does flipping a map make it harder, and if so whose fault is it — the hand's or the map's?

Hard Rock reflects the playfield top to bottom and Mirror defaults to left to right. Either
reverses every turn's direction and leaves its magnitude, spacing and timing alone, so a flip
can only cost a player through an asymmetry of their own. That makes the forum claim — "HR's
mirroring makes some patterns much harder" — a statement about handedness, and testable.

The measurement is a within-run contrast: error on turns that read clockwise on screen minus
error on turns that read counter-clockwise, in the same run. Anything that affects both
directions equally cancels inside one run — circle size, approach rate, a player's skill on
the day, the audio latency step from 2026-10-05 — which is why HR's other changes do not need
modelling to ask about its flip.

What does not cancel is the map. A map's clockwise turns are not its counter-clockwise turns:
they sit in different places, at different spacings and rhythms. So a contrast taken on one
map mixes the player's asymmetry P with the map's own imbalance M. The flip separates them,
because it reverses the second and not the first:

    unflipped run:  c = P + M
    flipped run:    c = P - M
    so              P = (c_unflipped + c_flipped) / 2,   M = (c_unflipped - c_flipped) / 2

That is only clean when the same player played both, which is why it is computed on one
player's runs and not across the boards. Two boards are two populations — a top-50 no-mod
board and a daily board that runs from rank one to rank three thousand — and both P and M
move with skill. Across boards the decomposition is reported as two separate contrasts, and
the only claim drawn from them is the sign pattern: with no population asymmetry, the flipped
board's contrast should be the negative of the unflipped one's.

Aim error exists only where a press landed, so the surviving hits are selected on the
outcome: if one direction costs more misses, its surviving hits are its better ones and the
aim contrast shrinks. The miss-rate contrast is reported beside it every time, and it is the
closer of the two to what "much harder" meant on a forum.

Sign convention, pinned by tests/Tests/GeometryTests.cs: a negative signedAngle reads
clockwise on screen. Turns within 20 degrees of straight or of a full reversal are dropped,
because a straight line has no direction and its sign is a signed zero's.

    python3 tools/analysis/flip.py
"""
import collections, csv, glob, json, lzma, math, random, statistics, struct

DIR = "build/mirrorweek"
LO, HI = math.radians(20), math.radians(160)
# Both legs of a turn must be a real movement. Through a stack the cursor barely moves and
# the "turn" takes its direction from lazer's stack offset, which is always up and to the
# left whether or not the map is flipped — so under a vertical flip three stacked turns on
# Dear You kept their direction while 129 others swapped. They are not aim turns.
MIN_LEG = 0.5
BOOT = 2000


def osr_mods(path):
    """Mods with their settings, from a lazer replay's score-info blob. The board listing
    carries acronyms only, and Mirror's axis lives in its settings."""
    b = open(path, "rb").read()
    i = 5

    def skip_string():
        nonlocal i
        if b[i] == 0:
            i += 1
            return
        i += 1
        n = shift = 0
        while True:
            c = b[i]
            i += 1
            n |= (c & 0x7f) << shift
            shift += 7
            if c < 0x80:
                break
        i += n

    skip_string(); skip_string(); skip_string()
    i += 2 * 6 + 4 + 2 + 1 + 4
    skip_string()
    i += 8
    i += 4 + struct.unpack_from("<i", b, i)[0]
    i += 8
    length = struct.unpack_from("<i", b, i)[0]
    i += 4
    return json.loads(lzma.decompress(b[i:i + length])).get("mods", [])


def label(mods):
    """A condition name. Mirror's axis is spelled out because an unset reflection is the
    default, horizontal, and HR's flip is the other one."""
    out = []
    for m in sorted(mods, key=lambda m: m["acronym"]):
        a, st = m["acronym"], m.get("settings") or {}
        if a == "MR":
            a = {0: "MR-H", 1: "MR-V", 2: "MR-HV"}[st.get("reflection", 0)]
        elif a == "DA":
            a = "DA(" + ",".join(f"{k[0].upper()}{k.split('_')[1][0].upper()}{v:g}" for k, v in sorted(st.items())) + ")"
        out.append(a)
    return " ".join(out) or "NM"


def flipped(cond):
    return "MR-H" in cond or "MR-V" in cond or "HR" in cond.split()


def runs(name):
    """Per run: the clockwise-minus-counter-clockwise aim and miss contrasts, plus the per
    object signs so the flip itself can be checked."""
    corpus = {r["Path"].split("/")[-1]: r for r in json.load(open(f"{DIR}/corpus-{name}.json"))}
    by = collections.defaultdict(list)
    with open(f"{DIR}/geometry-{name}.csv") as f:
        for r in csv.DictReader(f):
            if r["kind"] == "circle" and r["signedAngle"]:
                by[r["replay"]].append(r)
            elif r["kind"] != "circle":
                by[r["replay"]].append(None)

    out = []
    for replay, rows in by.items():
        aim = {True: [], False: []}
        miss = {True: [0, 0], False: [0, 0]}
        signs = {}
        for k, r in enumerate(rows):
            if r is None:
                continue
            nxt = rows[k + 1] if k + 1 < len(rows) else None
            out_leg = float(nxt["spacingRadii"]) if nxt else 0.0
            a = float(r["signedAngle"])
            if not LO <= abs(a) <= HI or float(r["spacingRadii"]) < MIN_LEG or out_leg < MIN_LEG:
                continue
            cw = a < 0
            signs[round(float(r["startTime"]))] = cw
            if r["result"] == "Miss":
                miss[cw][0] += 1
            if r["result"] != "Unjudged":
                miss[cw][1] += 1
            if r["aimErrorRadii"]:
                aim[cw].append(float(r["aimErrorRadii"]))
        if min(len(aim[True]), len(aim[False])) < 10:
            continue
        rec = corpus[replay]
        out.append({
            "replay": replay, "md5": rec["BeatmapMd5"], "path": rec["Path"],
            "aim": statistics.mean(aim[True]) - statistics.mean(aim[False]),
            "miss": miss[True][0] / max(miss[True][1], 1) - miss[False][0] / max(miss[False][1], 1),
            "n": (len(aim[True]), len(aim[False])), "signs": signs,
        })
    return out


def mean_ci(xs):
    if not xs:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(0)
    boots = sorted(statistics.mean(rng.choices(xs, k=len(xs))) for _ in range(BOOT))
    return statistics.mean(xs), boots[int(.025 * BOOT)], boots[int(.975 * BOOT)]


def fmt(xs, scale=1.0, unit=""):
    m, lo, hi = mean_ci([x * scale for x in xs])
    return f"{m:+.4f}{unit} [{lo:+.4f}, {hi:+.4f}]  n={len(xs)}"


def player():
    zak = runs("zak")
    for r in zak:
        r["cond"] = label(osr_mods(r["path"]))
        r["time"] = r["replay"][:15]
    zak.sort(key=lambda r: r["time"])

    # The flip has to reach the extractor, or every flipped run is read as unflipped. Every
    # object that keeps a direction must swap it between a no-mod run and a flipped one.
    base = next(r for r in zak if r["cond"] == "NM")
    for r in zak:
        if r["cond"] == "NM":
            continue
        common = set(base["signs"]) & set(r["signs"])
        swapped = sum(base["signs"][t] != r["signs"][t] for t in common)
        want = len(common) if flipped(r["cond"]) else 0
        assert swapped == want, f"{r['cond']} {r['replay']}: {swapped} of {len(common)} turns swapped, expected {want}"

    print("Your Dear You runs, in order (aim in radii, miss in percentage points; + means clockwise is worse)")
    for r in zak:
        print(f"  {r['time'][9:]}  {r['cond']:<34} aim {r['aim']:+.4f}  miss {r['miss'] * 100:+5.1f}  n={r['n']}")

    cells = collections.defaultdict(list)
    for r in zak:
        cells[r["cond"]].append(r)
    print("\n  by condition")
    for c, rs in sorted(cells.items(), key=lambda kv: -len(kv[1])):
        print(f"    {c:<34} aim {fmt([r['aim'] for r in rs])}   miss {fmt([r['miss'] for r in rs], 100)}")

    def decompose(plain, flip):
        a, b = [r["aim"] for r in cells[plain]], [r["aim"] for r in cells[flip]]
        if not a or not b:
            return None
        ca, cb = statistics.mean(a), statistics.mean(b)
        return (ca + cb) / 2, (ca - cb) / 2

    print("\n  decomposition (P: your asymmetry, M: the map's)")
    for plain, flip in (("NM", "MR-H"), ("DA(AR10,OD9) HD", "DA(AR10,OD9) HD MR-V"),
                        ("DA(AR10,OD9) HD", "DA(AR10,OD9) HD MR-H")):
        d = decompose(plain, flip)
        if d:
            print(f"    {plain:>16} vs {flip:<22}  P {d[0]:+.4f}  M {d[1]:+.4f}")


def boards():
    names = {}
    for p in glob.glob("build/daily/room-*.jsonl"):
        if p.endswith(".snapshots.jsonl"):
            continue
        m = json.loads(open(p).readline())["meta"]
        names[m["beatmap_id"]] = f"{m['title']} [{m['version']}]"

    daily_meta = {}
    for line in open("build/daily/replays.jsonl"):
        r = json.loads(line)
        daily_meta[r["file"]] = r
    nm_meta = {v["file"]: v for v in json.load(open("build/reference-mirrorweek/manifest.json")).values()}

    by_map = collections.defaultdict(lambda: {"nm": [], "mr": [], "mr_top": []})
    md5_to_id = {}
    for r in runs("nm"):
        meta = nm_meta[r["replay"]]
        md5_to_id[r["md5"]] = meta["beatmap_id"]
        by_map[r["md5"]]["nm"].append(r["aim"])
    for r in runs("daily"):
        meta = daily_meta[r["replay"]]
        acr = {m if isinstance(m, str) else m.get("acronym") for m in meta.get("mods") or []}
        if acr - {"MR", "CL"}:
            continue
        by_map[r["md5"]]["mr"].append(r["aim"])
        if meta["stratum"] < 50:
            by_map[r["md5"]]["mr_top"].append(r["aim"])

    print("\nThe boards (aim contrast in radii, + means clockwise is worse). With no population")
    print("asymmetry, a flipped board's contrast should be the negative of the unflipped one's.")
    for md5, v in by_map.items():
        name = names.get(md5_to_id.get(md5), md5[:8])
        print(f"  {name}")
        print(f"    no-mod top 50     {fmt(v['nm'])}")
        print(f"    Mirror, all       {fmt(v['mr'])}")
        print(f"    Mirror, top strata {fmt(v['mr_top'])}")


if __name__ == "__main__":
    player()
    boards()
