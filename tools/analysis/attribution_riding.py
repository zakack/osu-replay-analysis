"""The riding witness: stream attribution checked against the physical key travel.

attribution_check.py uses the replay's button as an independent record of which circle a tap
was meant for. Under doubtap's deep latch that fails: riding toggles the outputs, so the
button is the note's ordinal. The key trace still knows the hand. Riding is finger
alternation with a negative edge: both keys rest bottomed out on the backplate, and every
note is one key leaving the floor or landing back on it, in the cycle

    k1 lifts, k1 lands, k2 lifts, k2 lands         (two circles to a finger, AABB)

so each press in a riding burst has a physical event, and after an anchor of ANCHOR own-taken
circles whose events step through the cycle, every slot has a predicted event. Adjacent slots
always differ, in key or in direction, so the event picks between "meant for the circle the
timing says" (intent) and "meant for the circle lazer judged" (judged), as the button does
when the player alternates by hand.

Traced sessions only (build/traces). A replay press is put on CLOCK_MONOTONIC with the
session's align.json and matched to the nearest floor crossing (travel_mm - bottom_out_mm,
from the trace header) within MATCH ms. Presses outside the deep latch use the replay's
button, which follows the finger there, so tapped quads are checked too.

Also: presses the stream alignment leaves unmatched (its "extra" taps), split by whether one
came in tapping mode just before the latch armed, where planting both keys to ride a double
or a quad leaves a note nobody meant.

    python3 tools/analysis/attribution_riding.py [--out build/attribution-riding.txt]
"""
from __future__ import annotations

import bisect
import collections
import glob
import json
import os
import pickle
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import streams as S                                         # noqa: E402
from acur_whole import chains                               # noqa: E402
from attribution_check import ANCHOR, load_presses          # noqa: E402

MATCH = 4.0         # ms between a riding press and its floor crossing
PLANT = 200.0       # ms before deep,1 in which an unmatched press counts as the plant
CYCLE = (("k1", "lift"), ("k1", "land"), ("k2", "lift"), ("k2", "land"))


def zlines(path):
    return subprocess.run(["zstd", "-dc", path], capture_output=True, text=True, check=True).stdout.splitlines()


def sessions():
    """Per traced replay: (to_mono(map_ms), deep spans, floor crossings) on monotonic ms."""
    out = {}
    for aj in sorted(glob.glob("build/traces/*.align.json")):
        stem = aj[:-len(".align.json")]
        if not (os.path.exists(stem + ".events.csv.zst") and os.path.getsize(stem + ".csv.zst")):
            continue
        ev = zlines(stem + ".events.csv.zst")
        origin = next(int(l.split("=")[1]) for l in ev if l.startswith("# origin_monotonic_ns=")) / 1e6
        spans, on = [], None
        for l in ev:
            f = l.split(",")
            if len(f) == 3 and f[1] == "deep":
                t = origin + int(f[0]) / 1000
                if f[2] == "1":
                    on = t
                elif on is not None:
                    spans.append((on, t))
                    on = None
        raw = zlines(stem + ".csv.zst")
        hdr = {}
        for l in raw:
            if l.startswith("#"):
                for part in l[1:].split():
                    if "=" in part:
                        k, v = part.split("=", 1)
                        hdr[k] = v
        t0 = int(hdr["t0_monotonic_ns"]) / 1e6
        floor = float(hdr["travel_mm"]) - float(hdr["rapid_trigger.bottom_out_mm"])
        crossings = []
        prev = None
        for l in raw:
            if not l[:1].isdigit():
                continue
            u, a, b = l.split(",")
            state = (float(a) >= floor, float(b) >= floor)
            if prev is not None:
                t = t0 + int(u) / 1000
                for key, was, now in (("k1", prev[0], state[0]), ("k2", prev[1], state[1])):
                    if was != now:
                        crossings.append((t, key, "land" if now else "lift"))
            prev = state
        crossings.sort()
        for a in json.load(open(aj)):
            def to_mono(ms, a=a):
                seg = next((g for g in a.get("segments") or [] if g["from_ms"] <= ms < g["to_ms"]),
                           {"offset_ms": a["offset_ms"]})
                return seg["offset_ms"] + ms * (1 + a.get("drift", 0.0))
            out[a["replay"]] = (to_mono, spans, crossings)
    return out


def main(out_path):
    traced = sessions()
    runs = [(r, ev) for r, ev in pickle.load(open(S.CACHE, "rb")) if r in traced]
    presses = load_presses({r for r, _ in runs})

    match_resid, unmatched_phys = [], 0
    base = collections.defaultdict(lambda: [0, 0])      # (mode, distance) -> [agree, n]
    obs = []                                            # (mode, replay, kind, label, verdict)
    extras = collections.Counter()
    for replay, events in runs:
        ps = presses.get(replay)
        if not ps:
            continue
        to_mono, spans, crossings = traced[replay]
        ctimes = [c[0] for c in crossings]
        objects = [e[0] for e in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(ps, (objects[0] - interval,))
        hi = bisect.bisect_right(ps, (objects[-1] + interval, "~"))
        window = ps[lo:hi]
        w3 = [p[:3] for p in window]
        r = S.classify(objects, [e[3] for e in events], [e[4] for e in events], w3, snap, beat)
        if r["verdict"] != "ok":
            continue
        r["interval"] = interval
        judge, tap = r["judge"], r["tap"]
        times = [p[0] for p in window]
        pairs = r["pairs"]
        meant = {i: j for i, j in pairs if i is not None and j is not None}
        n = len(objects)

        # Physical label per press: a cycle position while riding, the button otherwise.
        label, mode = {}, {}
        for j, p in enumerate(window):
            m = to_mono(p[0])
            k = bisect.bisect_right(spans, (m, float("inf"))) - 1
            deep = k >= 0 and spans[k][0] <= m <= spans[k][1] + MATCH
            if deep:
                q = bisect.bisect_left(ctimes, m)
                near = min((x for x in (q - 1, q) if 0 <= x < len(ctimes)),
                           key=lambda x: abs(ctimes[x] - m), default=None)
                if near is not None and abs(ctimes[near] - m) <= MATCH:
                    match_resid.append(ctimes[near] - m)
                    label[j] = CYCLE.index(crossings[near][1:])
                    mode[j] = "riding"
                else:
                    unmatched_phys += 1
            else:
                label[j] = 0 if p[3] == "LeftButton" else 1
                mode[j] = "tapping"
        period = {"riding": 4, "tapping": 2}

        def anchor_before(q):
            a = q - 1
            while a >= ANCHOR - 1:
                seg = list(range(a - ANCHOR + 1, a + 1))
                if all(judge[i] == "self" and tap[i] == "own" and i in meant and meant[i] in label
                       for i in seg):
                    md = {mode[meant[i]] for i in seg}
                    if len(md) == 1:
                        md = md.pop()
                        P = period[md]
                        if all((label[meant[i + 1]] - label[meant[i]]) % P == 1 for i in seg[:-1]):
                            return a, label[meant[a]], md
                a -= 1
            return None

        def verdict(anc, j, intent_slot, judged_slot):
            a, la, md = anc
            if j not in label or mode[j] != md:
                return None
            P = period[md]
            pi, pj = (la + intent_slot - a) % P, (la + judged_slot - a) % P
            b = label[j]
            return "intent" if b == pi and b != pj else "judged" if b == pj and b != pi else "neither"

        a = 0
        while a + ANCHOR <= n:
            anc = anchor_before(a + ANCHOR)
            if anc and anc[0] == a + ANCHOR - 1:
                for d, q in enumerate(range(a + ANCHOR, min(n, a + ANCHOR + 8)), 1):
                    if not (judge[q] == "self" and tap[q] == "own" and q in meant):
                        break
                    v = verdict(anc, meant[q], q, q - 1)
                    if v is None:
                        break
                    s = base[(anc[2], d)]
                    s[0] += v == "intent"
                    s[1] += 1
                break
            a += 1

        for origin, idx in chains(objects, w3, r):
            q = idx[0]
            dirn = judge[q]
            anc = anchor_before(q - 1 if dirn == "prev" else q)
            if anc is None:
                continue
            step = 1 if dirn == "next" else -1
            for i in idx:
                j = meant.get(i + step)
                if j is None or not (0 <= i + step < n):
                    continue
                v = verdict(anc, j, i + step, i)
                if v:
                    obs.append((anc[2], replay, "shifted", origin, v))
            if origin in ("stray-nothing", "notelock") and q in meant:
                v = verdict(anc, meant[q], q, q + 1)
                if v:
                    obs.append((anc[2], replay, "origin", origin, v))
            elif origin == "cursor left early" and (q - 1) in meant:
                v = verdict(anc, meant[q - 1], q - 1, q)
                if v:
                    obs.append((anc[2], replay, "origin", origin, v))
            elif origin == "dropped":
                k = next((x for x in (q, q - 1) if tap[x] == "dropped"), None) if dirn == "next" else \
                    next((x for x in (idx[-1], idx[-1] + 1) if x < n and tap[x] == "dropped"), None)
                if k is not None and (k - 1) in meant and (k + 1) in meant:
                    j1, j2 = meant[k - 1], meant[k + 1]
                    if j1 in label and j2 in label and mode[j1] == mode[j2] == anc[2]:
                        gap = (label[j2] - label[j1]) % period[anc[2]]
                        # One cycle step across the gap: no physical event is missing (drift).
                        # Two: an event happened with no press for its slot (a real drop).
                        obs.append((anc[2], replay, "dropped", origin,
                                    "judged" if gap == 1 else "intent" if gap == 2 else "neither"))

        # Unmatched presses: is one the plant before the latch armed?
        for i, j in pairs:
            if i is not None:
                continue
            m = to_mono(window[j][0])
            k = bisect.bisect_left(spans, (m,))
            plant = k < len(spans) and 0 <= spans[k][0] - m <= PLANT
            extras[(mode.get(j, "riding-unmatched"), "plant" if plant else "other")] += 1

    lines = []
    p = lines.append
    p(f"traced replays with stream runs: {len({o[1] for o in obs} | set())} with checks; "
      f"riding presses matched to a floor crossing: {len(match_resid)}, unmatched {unmatched_phys}")
    if match_resid:
        sr = sorted(abs(x) for x in match_resid)
        p(f"  |crossing - press| ms: median {sr[len(sr) // 2]:.2f}, p95 {sr[int(.95 * len(sr))]:.2f}")
    p("\nbaseline: own taps after the anchor, share on the predicted event (riding: 4-cycle; tapping: button)")
    for md in ("riding", "tapping"):
        p(f"  {md:<8} " + "  ".join(f"{d}: {100 * base[(md, d)][0] / base[(md, d)][1]:.1f}% of {base[(md, d)][1]}"
                                    for d in range(1, 9) if base[(md, d)][1]))
    for kind, title in (("shifted", "shifted circles: the judging press's physical event is the slot it was meant for (intent) or the circle it judged?"),
                        ("origin", "chain origin press"),
                        ("dropped", "dropped slot: one cycle step across the gap (judged: nothing missing) or two (intent: a real drop)")):
        p(f"\n{title}")
        for md in ("riding", "tapping"):
            for lab in sorted({o[3] for o in obs if o[2] == kind}):
                sel = [o for o in obs if o[0] == md and o[2] == kind and o[3] == lab]
                if not sel:
                    continue
                c = collections.Counter(o[4] for o in sel)
                p(f"  {md:<8} {lab:<18} n {len(sel):5}  intent {100 * c['intent'] / len(sel):5.1f}%  "
                  f"judged {100 * c['judged'] / len(sel):5.1f}%  neither {100 * c['neither'] / len(sel):5.1f}%"
                  f"  ({len({o[1] for o in sel})} replays)")
    p("\npresses the alignment left unmatched, by mode and whether the latch armed within "
      f"{PLANT:.0f} ms after:")
    for k, v in sorted(extras.items()):
        p(f"  {k[0]:<18} {k[1]:<6} {v}")

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/attribution-riding.txt")
