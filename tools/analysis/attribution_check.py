"""Is the stream attribution right? A witness it never looks at: which button the tap used.

streams.py decides which circle a press was meant for from times alone. When the player
alternates by hand, the button is a second, independent record of the same thing: a stream
that has been going L R L R puts the next tap's finger on a known slot, and a press that
lands in the "wrong" slot by timing still comes from the finger for its own. So inside an
aligned run, after an anchor of ANCHOR circles each taken by its own tap with strictly
alternating buttons, every later press has a predicted button for each slot it could have
been meant for. Two readings of one press disagree by exactly one slot (n vs n+1), which
alternation flips, so the button picks between them.

Checked, per claim the attribution makes:
  shifted     a circle judged by a neighbour's tap: does the press's button match the slot
              it was meant for (the attribution) or the circle it judged (lazer's reading)?
              This is the pushback-vs-own-late question streams.py could only settle by
              anchoring.
  origin      the press at the head of a chain, by origin label: stray-nothing, notelock and
              cursor left early (the cursor class), double and dropped (the button class)
  dropped     a slot with no press: if the fingers kept alternating and one stroke never
              came, the presses either side of the gap share a button (L R . R); a clean
              alternation across it means the count was fine and the timing drifted
  baseline    own taps after the anchor that judged their own circle, by distance: how
              often alternation itself holds, the ceiling for every row above

Hand-alternation eras only (setup-timeline.csv): from 2025-02-08 to 2025-11-18 and from
2026-01-03. Before 2025-02-08 the QMK toggle alternated the outputs itself, so the button
was the press's ordinal and witnessed nothing the times did not.

    python3 tools/analysis/attribution_check.py [--out build/attribution-check.txt]
"""
from __future__ import annotations

import bisect
import collections
import csv
import os
import pickle
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
import streams as S                 # noqa: E402
from acur_whole import chains       # noqa: E402

ANCHOR = 4


def era(day: str) -> str | None:
    if "2025-02-08" <= day < "2025-05-05":
        return "plain"
    if "2025-05-05" <= day < "2025-11-18":
        return "glidepad"
    if "2026-01-03" <= day < "2026-08-29":
        return "exclusive"
    if day >= "2026-09-05":
        return "analog"
    return None


def load_presses(replays):
    out = collections.defaultdict(list)
    with open(S.PRESSES, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["replay"] in replays:
                out[r["replay"]].append((float(r["time"]), r["outcome"],
                                         float(r["target"]) if r["target"] else None, r["action"]))
    for v in out.values():
        v.sort()
    return out


def deep_lookup():
    """(replay, map ms) -> "off" / "deep" / "untraced", from build/traces: each session's
    events file (doubletap `replay -e`) carries deep,1/deep,0 rows on CLOCK_MONOTONIC, and
    its align.json maps a replay's map clock onto the same clock."""
    import glob
    import json
    import subprocess
    spans, where = {}, {}
    for aj in glob.glob("build/traces/*.align.json"):
        stem = aj[:-len(".align.json")]
        if not os.path.exists(stem + ".events.csv.zst"):
            continue
        text = subprocess.run(["zstd", "-dc", stem + ".events.csv.zst"], capture_output=True,
                              text=True, check=True).stdout.splitlines()
        origin = next(int(l.split("=")[1]) for l in text if l.startswith("# origin_monotonic_ns=")) / 1e6
        on, edges = None, []
        for l in text:
            f = l.split(",")
            if len(f) == 3 and f[1] == "deep":
                t = origin + int(f[0]) / 1000
                if f[2] == "1":
                    on = t
                elif on is not None:
                    edges.append((on, t))
                    on = None
        spans[stem] = edges
        for a in json.load(open(aj)):
            where[a["replay"]] = (stem, a)

    def at(replay, map_ms):
        if replay not in where:
            return "untraced"
        stem, a = where[replay]
        seg = next((g for g in a.get("segments") or [] if g["from_ms"] <= map_ms < g["to_ms"]),
                   {"offset_ms": a["offset_ms"]})
        mono = seg["offset_ms"] + map_ms * (1 + a.get("drift", 0.0))
        k = bisect.bisect_right(spans[stem], (mono, float("inf"))) - 1
        return "deep" if k >= 0 and spans[stem][k][0] <= mono <= spans[stem][k][1] else "off"
    return at


def main(out_path):
    runs = [(r, ev) for r, ev in pickle.load(open(S.CACHE, "rb")) if era(r[-21:-11])]
    presses = load_presses({r for r, _ in runs})

    deep_at = deep_lookup()
    # (era, replay, kind, label, distance, verdict, map time): verdict "intent" / "judged" / "neither"
    obs = []
    base = collections.defaultdict(lambda: [0, 0])     # (era, distance) -> [agree, n]
    for replay, events in runs:
        ps = presses.get(replay)
        if not ps:
            continue
        e = era(replay[-21:-11])
        objects = [ev[0] for ev in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(ps, (objects[0] - interval,))
        hi = bisect.bisect_right(ps, (objects[-1] + interval, "~"))
        window = ps[lo:hi]
        w3 = [p[:3] for p in window]
        r = S.classify(objects, [ev[3] for ev in events], [ev[4] for ev in events], w3, snap, beat)
        if r["verdict"] != "ok":
            continue
        r["interval"] = interval
        judge, tap = r["judge"], r["tap"]
        times = [p[0] for p in window]
        pairs = S.align(objects, times, interval)
        meant = {i: j for i, j in pairs if i is not None and j is not None}
        n = len(objects)
        btn = lambda j: window[j][3]

        def anchor_before(q):
            """Latest index a < q with ANCHOR consecutive own-taken circles ending at a, all
            with strictly alternating buttons. Returns (a, button at a) or None."""
            a = q - 1
            while a >= ANCHOR - 1:
                seg = range(a - ANCHOR + 1, a + 1)
                if all(judge[i] == "self" and tap[i] == "own" and i in meant for i in seg) and \
                        all(btn(meant[i]) != btn(meant[i + 1]) for i in range(a - ANCHOR + 1, a)):
                    return a, btn(meant[a])
                a -= 1
            return None

        def predict(anchor, slot):
            a, b = anchor
            other = "RightButton" if b == "LeftButton" else "LeftButton"
            return b if (slot - a) % 2 == 0 else other

        def verdict(anchor, j, intent_slot, judged_slot):
            b = btn(j)
            pi, pj = predict(anchor, intent_slot), predict(anchor, judged_slot)
            return "intent" if b == pi and b != pj else "judged" if b == pj and b != pi else "neither"

        # Baseline: own taps after the run's first anchor, until the first circle that is not.
        a = 0
        while a + ANCHOR <= n:
            seg = range(a, a + ANCHOR)
            if all(judge[i] == "self" and tap[i] == "own" and i in meant for i in seg) and \
                    all(btn(meant[i]) != btn(meant[i + 1]) for i in range(a, a + ANCHOR - 1)):
                anc = (a + ANCHOR - 1, btn(meant[a + ANCHOR - 1]))
                for d, q in enumerate(range(a + ANCHOR, min(n, a + ANCHOR + 8)), 1):
                    if not (judge[q] == "self" and tap[q] == "own" and q in meant):
                        break
                    s = base[(e if e != "analog" else "analog-" + deep_at(replay, objects[q]), d)]
                    s[0] += btn(meant[q]) == predict(anc, q)
                    s[1] += 1
                break
            a += 1

        # Shifted circles and chain origins.
        for origin, idx in chains(objects, w3, r):
            q = idx[0]
            d = judge[q]                          # "next" (pushback) or "prev" (ahead)
            anc = anchor_before(q - 1 if d == "prev" else q)
            if anc is None:
                continue
            step = 1 if d == "next" else -1
            for i in idx:
                # The press that judged circle i was meant for circle i + step.
                j = meant.get(i + step)
                if j is None or not (0 <= i + step < n):
                    continue
                obs.append((e, replay, "shifted", origin, i - anc[0], verdict(anc, j, i + step, i), window[j][0]))
            # The origin press itself, where there is one.
            if origin in ("stray-nothing", "stray-window", "notelock") and q in meant:
                # Meant for q by time, judged nothing; the alternative reading is that it was
                # an early tap for q+1 (or a late one for q-1, judged).
                obs.append((e, replay, "origin", origin, q - anc[0], verdict(anc, meant[q], q, q + 1), window[meant[q]][0]))
            elif origin == "cursor left early" and (q - 1) in meant:
                # Meant for q-1 by time, judged q. Alternative: an early tap for q.
                obs.append((e, replay, "origin", origin, q - anc[0], verdict(anc, meant[q - 1], q - 1, q), window[meant[q - 1]][0]))
            elif origin == "dropped":
                k = next((x for x in (q, q - 1) if tap[x] == "dropped"), None) if d == "next" else \
                    next((x for x in (idx[-1], idx[-1] + 1) if x < n and tap[x] == "dropped"), None)
                if k is not None and (k - 1) in meant and (k + 1) in meant:
                    same = btn(meant[k - 1]) == btn(meant[k + 1])
                    obs.append((e, replay, "dropped", origin, k - anc[0],
                                "intent" if same else "judged", objects[k]))

    # Analog era: doubletap's deep latch ("riding") toggles the outputs, so while it is on
    # the button is the note's ordinal and witnesses nothing. Split the era by the latch
    # state at each press, where a session trace exists to say.
    obs = [o[:6] + (("analog-" + deep_at(o[1], o[6])) if o[0] == "analog" else o[0],) for o in obs]
    obs = [(o[6],) + o[1:6] for o in obs]

    lines = []
    p = lines.append
    eras = ("plain", "glidepad", "exclusive", "analog-off", "analog-deep", "analog-untraced")
    p("baseline: own taps after a 4-tap alternating anchor, share on the predicted button")
    p("  distance " + "".join(f"{x[-11:]:>12}" for x in eras))
    for dist in range(1, 9):
        p(f"  {dist:>8} " + "".join(
            f"{100 * base[(x, dist)][0] / base[(x, dist)][1]:6.1f}% {base[(x, dist)][1]:>5}" if base[(x, dist)][1] else f"{'-':>12}"
            for x in eras))

    rng = random.Random(1)

    def boot(sel):
        by = collections.defaultdict(list)
        for o in sel:
            by[o[1]].append(o)
        keys = list(by)
        vals = []
        for _ in range(300):
            s = [o for k in (rng.choice(keys) for _ in keys) for o in by[k]]
            vals.append(100 * sum(o[5] == "intent" for o in s) / max(1, len(s)))
        vals.sort()
        return vals[7], vals[292]

    def table(kind, title, labels):
        p(f"\n{title}")
        p(f"  {'label':<18}{'era':<11}{'n':>6}{'intent':>9}{'judged':>9}{'neither':>9}   95% CI (intent, replay bootstrap)")
        for lab in labels:
            for x in eras + ("all",):
                sel = [o for o in obs if o[2] == kind and (lab == "*" or o[3] == lab) and (x == "all" or o[0] == x)]
                if len(sel) < 20:
                    continue
                c = collections.Counter(o[5] for o in sel)
                lo_, hi_ = boot(sel)
                p(f"  {lab:<18}{x:<11}{len(sel):6}{100 * c['intent'] / len(sel):8.1f}%{100 * c['judged'] / len(sel):8.1f}%"
                  f"{100 * c['neither'] / len(sel):8.1f}%   [{lo_:.1f}, {hi_:.1f}]")

    table("shifted", "shifted circles: is the judging press's button the slot it was meant for, or the circle it judged?",
          ("*", "stray-nothing", "cursor left early", "double", "dropped"))
    p("  by distance from the anchor (all eras): " + "  ".join(
        f"{lo_}-{hi_}: {100 * sum(o[5] == 'intent' for o in s) / len(s):.0f}% of {len(s)}"
        for lo_, hi_ in ((1, 4), (5, 8), (9, 16), (17, 999))
        for s in [[o for o in obs if o[2] == 'shifted' and lo_ <= o[4] <= hi_]] if s))
    table("origin", "chain origin press: meant for its slot by time (intent) or the neighbouring slot?",
          ("stray-nothing", "notelock", "cursor left early"))
    table("dropped", "dropped slot: same button either side of the gap (intent: a stroke really missing) or clean alternation (judged: count fine, timing drifted)",
          ("dropped",))

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/attribution-check.txt")
