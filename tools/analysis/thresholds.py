"""What a doubletap threshold change would do, replayed over every recorded key trace.

doubletap's own `replay` runs a travel trace through the daemon's real state machine with any
config (-c), so nothing here reimplements the latch: each trace is replayed once under its own
recorded thresholds and once per candidate value, and the virtual key events are compared.

Two questions, one sweep each, every other threshold held at what the trace recorded:

  bottom_out_mm   Unintended deep latches. Nearly every press goes to full travel, so a ride
                  the player meant arms at any backplate; the arms that appear only as the
                  backplate widens are taps that happened to overlap on it. Counted as arms
                  under the candidate with no arm under the narrowest backplate (0.02 mm)
                  within MATCH_ARM ms. The 0.90 mm traces (to 2026-10-20) were played on a
                  backplate wide enough that the player met these misfires live.
  actuation_mm    Added latency on tapped presses. Each tapped press (outside a deep span under
                  the recorded thresholds) is matched to the same key's press under the
                  candidate; the shift is the time the key takes to travel the extra depth.
                  Jumps are the tapped presses that judged an object 3+ radii from the last,
                  located through the replays' align.json. The spread of the shift is what
                  adds to hit-error variance; its mean an offset can absorb.

Both also report the entry sequence: each latch's plant presses (within PLANT ms before it
arms, numbered up to 0) and ridden presses (from 1), each matched to its counterpart under the
candidate, so a threshold's effect on the gap between press k and k+1 is read directly. Ridden
presses are labelled lift or land from the trace's floor crossings.

These are the effect of the same finger movement under other thresholds, before the player
adapts. The player calibrates against the hitsound, which fires on the virtual press, so a
shift here is exactly what they would hear and correct for. Compare against riding.py's
observed timing across a real threshold change to see how much of a predicted shift survived.

    python3 tools/analysis/thresholds.py [--rebuild] [--out build/thresholds.txt]
"""
from __future__ import annotations

import bisect
import collections
import csv
import glob
import io
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from attribution_riding import sessions, PLANT                # noqa: E402

REPLAY = os.path.expanduser("~/Projects/doubletap/build/replay")
CONFIG = os.path.expanduser("~/.config/doubletap/config.yaml")
CACHE = "build/thresholds.json"
BOTTOM_OUT = (0.02, 0.05, 0.08, 0.12, 0.16, 0.20, 0.25, 0.30, 0.40, 0.50)
ACTUATION = (0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
MATCH_ARM = 30.0     # ms: an arm this close to a narrow-backplate arm is the same arm
MATCH_PRESS = 60.0   # ms: furthest a press may move and still be the same press
MATCH_ENTRY = 20.0   # ms: the same, inside a latch entry, where presses come 1/4 apart
ENTRY = range(-1, 7) # press order around a latch: <=0 the plant presses before it arms
JUMP = 3.0           # radii
KEYS = {"rapid_trigger.bottom_out_mm": "bottom_out_mm", "actuation_mm": "actuation_mm",
        "rapid_trigger.press_mm": "press_mm", "rapid_trigger.release_mm": "release_mm"}

csv.field_size_limit(10**7)


def nearest(sorted_ts, t):
    """Distance from t to the closest time in sorted_ts (inf if empty)."""
    if not sorted_ts:
        return float("inf")
    q = bisect.bisect_left(sorted_ts, t)
    return min(abs(sorted_ts[x] - t) for x in (q - 1, q) if 0 <= x < len(sorted_ts))


def recorded(stem):
    head = subprocess.run(f"zstd -dc '{stem}.csv.zst' | head -8", shell=True,
                          capture_output=True, text=True).stdout
    line = next((l for l in head.splitlines() if l.startswith("# analog ")), "")
    kv = dict(p.split("=", 1) for p in line[9:].split() if "=" in p)
    return {KEYS[k]: float(v) for k, v in kv.items() if k in KEYS}


def config_text(base, values):
    """The live config with the four analog thresholds set to `values`."""
    text = base
    for key, v in values.items():
        text, n = re.subn(rf"^(\s*{key}:\s*)[0-9.]+", rf"\g<1>{v:.3f}", text, count=1, flags=re.M)
        if not n:
            raise SystemExit(f"{CONFIG}: no '{key}:' line to override")
    return text


def run(trace_path, values, base):
    """Replay one trace under `values`: (presses [(ms, key)], arms [ms], spans [(on, off)]), ms on CLOCK_MONOTONIC."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as cfg:
        cfg.write(config_text(base, values))
    try:
        ev = subprocess.run([REPLAY, "-q", "-c", cfg.name, "-e", "-", trace_path],
                            capture_output=True, text=True).stdout.splitlines()
    finally:
        os.unlink(cfg.name)
    origin = next((int(l.split("=")[1]) for l in ev if l.startswith("# origin_monotonic_ns=")), 0) / 1e6
    presses, arms, spans, on = [], [], [], None
    for l in ev:
        f = l.split(",")
        if len(f) != 3 or not f[0].isdigit():
            continue
        t = origin + int(f[0]) / 1000
        if f[1] == "deep":
            if f[2] == "1":
                arms.append(t)
                on = t
            elif on is not None:
                spans.append((on, t))
                on = None
        elif f[2] == "1":
            presses.append((t, f[1]))
    return presses, arms, spans


def hit_presses(traced):
    """Monotonic press times of judged hits per trace stem: (all hits, hits on JUMP+ radii objects)."""
    want = {}
    for aj in glob.glob("build/traces/*.align.json"):
        for a in json.load(open(aj)):
            want[a["replay"]] = aj[:-len(".align.json")]
    out, hits = collections.defaultdict(list), collections.defaultdict(list)
    with open("build/geometry.csv", newline="") as f:
        rd = csv.reader(f)
        hdr = next(rd)
        i_t, i_k, i_h, i_s = (hdr.index(k) for k in ("startTime", "kind", "hitError", "spacingRadii"))
        for r in rd:
            if r[0] not in want or r[0] not in traced or r[i_k] not in ("circle", "slider") or not r[i_h]:
                continue
            t = traced[r[0]][0](float(r[i_t]) + float(r[i_h]))
            hits[want[r[0]]].append(t)
            if float(r[i_s] or 0) >= JUMP:
                out[want[r[0]]].append(t)
    return {k: sorted(v) for k, v in hits.items()}, {k: sorted(v) for k, v in out.items()}


def matched_shift(base, cand, window):
    """Per base press time, the shift to the nearest candidate press within window (any key), else None."""
    ts = sorted(t for t, _ in cand)
    out = []
    for t, _ in base:
        q = bisect.bisect_left(ts, t)
        near = min((x for x in (q - 1, q) if 0 <= x < len(ts)), key=lambda x: abs(ts[x] - t), default=None)
        out.append(ts[near] - t if near is not None and abs(ts[near] - t) <= window else None)
    return out


def entries(presses, spans):
    """(index into presses, order) for each latch's plant and ridden presses."""
    times = [t for t, _ in presses]
    out = []
    for on, off in spans:
        plants = list(range(bisect.bisect_left(times, on - PLANT), bisect.bisect_left(times, on)))
        ridden = list(range(bisect.bisect_left(times, on), bisect.bisect_right(times, off)))
        seq = [(i, j + 1 - len(plants)) for j, i in enumerate(plants)] + [(i, j + 1) for j, i in enumerate(ridden)]
        out.append([(i, k) for i, k in seq if k in ENTRY])
    return out


def build():
    if not os.access(REPLAY, os.X_OK):
        raise SystemExit(f"no doubletap replay at {REPLAY} (cmake --build build --target replay)")
    base = open(CONFIG).read()
    traced = sessions()
    hits, jumps = hit_presses(traced)
    crossings_of = {}
    for aj in glob.glob("build/traces/*.align.json"):
        for a in json.load(open(aj)):
            if a["replay"] in traced:
                crossings_of[aj[:-len(".align.json")]] = traced[a["replay"]][2]
    result = {"bottom_out": collections.defaultdict(lambda: collections.defaultdict(float)),
              "actuation": collections.defaultdict(lambda: collections.defaultdict(list)),
              "entry": collections.defaultdict(list),      # (period, param, value, k) -> press shifts
              "gap": collections.defaultdict(list),        # (period, param, value, k) -> shift of gap k -> k+1
              "event": collections.defaultdict(list),      # (period, value, lift/land) -> ridden press shifts
              "act_side": collections.defaultdict(lambda: collections.defaultdict(float))}
    stems = sorted(s[:-len(".csv.zst")] for s in glob.glob("build/traces/*.csv.zst")
                   if not s.endswith((".cursor.csv.zst", ".events.csv.zst")) and os.path.getsize(s) > 0)
    for n, stem in enumerate(stems, 1):
        rec = recorded(stem)
        if len(rec) < 4:
            continue
        period = f"act {rec['actuation_mm']:.2f} / bottom_out {rec['bottom_out_mm']:.2f}"
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            unpacked = subprocess.run(["zstd", "-dcf", stem + ".csv.zst", "-o", tmp.name], capture_output=True)
        if unpacked.returncode:     # a session still recording has no zstd frame end yet
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
            print(f"  {n}/{len(stems)} {os.path.basename(stem)}: unreadable, skipped (still recording?)",
                  file=sys.stderr, flush=True)
            continue
        try:
            presses0, arms0, spans0 = run(tmp.name, rec, base)
            if not presses0:
                continue
            hours = (presses0[-1][0] - presses0[0][0]) / 3.6e6
            seqs = entries(presses0, spans0)
            cx = crossings_of.get(stem, [])
            ctimes = [c[0] for c in cx]
            def label(t):
                q = bisect.bisect_left(ctimes, t)
                near = min((x for x in (q - 1, q) if 0 <= x < len(ctimes)), key=lambda x: abs(ctimes[x] - t), default=None)
                return cx[near][2] if near is not None and abs(ctimes[near] - t) <= 4.0 else None

            def entry_shifts(param, value, cand):
                shift = matched_shift(presses0, cand, MATCH_ENTRY)
                for seq in seqs:
                    for (i, k), nxt in zip(seq, seq[1:] + [None]):
                        if shift[i] is None:
                            continue
                        result["entry"][(period, param, value, k)].append(shift[i])
                        if param == "bottom_out" and k >= 1 and (lab := label(presses0[i][0])):
                            result["event"][(period, value, lab)].append(shift[i])
                        if nxt is not None and nxt[1] == k + 1 and shift[nxt[0]] is not None:
                            result["gap"][(period, param, value, k)].append(shift[nxt[0]] - shift[i])

            # bottom_out: arms beyond the narrowest backplate's
            core = run(tmp.name, {**rec, "bottom_out_mm": BOTTOM_OUT[0]}, base)[1]
            for b in BOTTOM_OUT:
                presses, arms, _ = run(tmp.name, {**rec, "bottom_out_mm": b}, base)
                entry_shifts("bottom_out", b, presses)
                extra = [t for t in arms if nearest(core, t) > MATCH_ARM]
                acc = result["bottom_out"][(period, b)]
                acc["hours"] += hours
                acc["arms"] += len(arms)
                acc["extra"] += len(extra)
                acc["presses"] += len(presses)
                acc["presses_rec"] += len(presses0)
                acc["traces"] += 1

            # actuation: shift of each tapped press
            sstarts = [s[0] for s in spans0]
            def tapped(t):
                k = bisect.bisect_right(sstarts, t) - 1
                return not (k >= 0 and spans0[k][0] <= t <= spans0[k][1])
            base_taps = [(t, key) for t, key in presses0 if tapped(t)]
            jt = jumps.get(stem, [])
            is_jump = [nearest(jt, t) <= 4.0 for t, _ in base_taps]
            for a in ACTUATION:
                if a >= 4.0 - rec["bottom_out_mm"]:   # the daemon refuses actuation at or past the backplate
                    continue
                presses, arms, _ = run(tmp.name, {**rec, "actuation_mm": a}, base)
                entry_shifts("actuation", a, presses)
                # Side effects: arms, presses that vanish or appear, and same-key repeats under 30 ms
                # (chatter on the threshold edge). A vanished press that judged an object was a hit.
                side = result["act_side"][(period, a)]
                ht = hits.get(stem, [])
                cand_by_key, base_by_key = collections.defaultdict(list), collections.defaultdict(list)
                for t, key in presses:
                    cand_by_key[key].append(t)
                for t, key in presses0:
                    base_by_key[key].append(t)
                side["hours"] += hours
                side["arms"] += len(arms)
                side["arms_rec"] += len(arms0)
                side["arms_new"] += sum(nearest(arms0, t) > MATCH_ARM for t in arms)
                side["arms_gone"] += sum(nearest(arms, t) > MATCH_ARM for t in arms0)
                for t, key in base_taps:
                    if nearest(cand_by_key[key], t) > MATCH_PRESS:
                        side["vanished"] += 1
                        side["vanished_hit"] += nearest(ht, t) <= 4.0
                for t, key in presses:
                    side["appeared"] += nearest(base_by_key[key], t) > MATCH_PRESS
                for ts in cand_by_key.values():
                    side["chatter"] += sum(b - a_ < 30.0 for a_, b in zip(ts, ts[1:]))
                for ts in base_by_key.values():
                    side["chatter_rec"] += sum(b - a_ < 30.0 for a_, b in zip(ts, ts[1:]))
                by_key = collections.defaultdict(list)
                for t, key in presses:
                    by_key[key].append(t)
                acc = result["actuation"][(period, a)]
                for (t, key), j in zip(base_taps, is_jump):
                    ts = by_key[key]
                    q = bisect.bisect_left(ts, t - MATCH_PRESS / 2)
                    shift = ts[q] - t if q < len(ts) and abs(ts[q] - t) <= MATCH_PRESS else None
                    acc["all"].append(shift)
                    if j:
                        acc["jump"].append(shift)
        finally:
            os.unlink(tmp.name)
        print(f"  {n}/{len(stems)} {os.path.basename(stem)}", file=sys.stderr, flush=True)

    ser = lambda d: {"|".join(map(str, k)): v for k, v in d.items()}
    data = {k: ser(v) for k, v in result.items()}
    json.dump(data, open(CACHE, "w"))
    return data


def main():
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/thresholds.txt"
    d = json.load(open(CACHE)) if os.path.exists(CACHE) and "--rebuild" not in sys.argv else build()
    out = io.StringIO()
    p = lambda *a: print(*a, file=out)

    p("bottom_out_mm: deep latches per hour of trace, and the ones beyond a 0.02 mm backplate's")
    p(f"  {'recorded thresholds':28}{'candidate':>10}{'traces':>8}{'hours':>7}{'arms/h':>9}{'extra/h':>9}{'extra':>7}{'Δ presses/h':>13}")
    for k in sorted(d["bottom_out"], key=lambda k: (k.split("|")[0], float(k.split("|")[1]))):
        period, b = k.split("|")
        a = d["bottom_out"][k]
        p(f"  {period:28}{float(b):>10.2f}{int(a['traces']):>8}{a['hours']:>7.1f}"
          f"{a['arms'] / a['hours']:>9.1f}{a['extra'] / a['hours']:>9.2f}{int(a['extra']):>7}"
          f"{(a['presses'] - a['presses_rec']) / a['hours']:>+13.1f}")

    p("\nactuation_mm: shift of tapped presses against the trace's own actuation (ms; + = later)")
    p(f"  {'recorded thresholds':28}{'candidate':>10}{'presses':>9}{'median':>8}{'mean':>7}{'SD':>6}"
      f"{'lost':>7}   jumps: {'n':>5}{'median':>8}{'SD':>6}{'lost':>7}")
    for k in sorted(d["actuation"], key=lambda k: (k.split("|")[0], float(k.split("|")[1]))):
        period, a = k.split("|")
        r = d["actuation"][k]
        def summary(xs):
            got = [x for x in xs if x is not None]
            if len(got) < 2:
                return len(xs), float("nan"), float("nan"), float("nan"), float("nan")
            return (len(xs), statistics.median(got), statistics.fmean(got), statistics.pstdev(got),
                    1 - len(got) / len(xs))
        n, med, mean, sd, lost = summary(r["all"])
        nj, medj, _, sdj, lostj = summary(r["jump"])
        p(f"  {period:28}{float(a):>10.2f}{n:>9,}{med:>+8.2f}{mean:>+7.2f}{sd:>6.2f}{lost:>7.2%}"
          f"          {nj:>5,}{medj:>+8.2f}{sdj:>6.2f}{lostj:>7.2%}")
    p("\nactuation_mm side effects, per hour of trace (against the trace's own actuation)")
    p(f"  {'recorded thresholds':28}{'candidate':>10}{'arms':>8}{'new':>7}{'gone':>7}"
      f"{'vanished':>10}{'of them hits':>14}{'appeared':>10}{'chatter <30ms':>15}")
    for k in sorted(d["act_side"], key=lambda k: (k.split("|")[0], float(k.split("|")[1]))):
        period, a = k.split("|")
        x = d["act_side"][k]
        h = x["hours"]
        p(f"  {period:28}{float(a):>10.2f}{x['arms'] / h:>8.1f}{x['arms_new'] / h:>7.2f}{x['arms_gone'] / h:>7.2f}"
          f"{x['vanished'] / h:>10.2f}{x['vanished_hit'] / h:>14.2f}{x['appeared'] / h:>10.2f}"
          f"{x['chatter'] / h:>8.2f} (rec {x['chatter_rec'] / h:.2f})")

    p("\nlatch entry: mean shift of the gap from press k to k+1, against the trace's own thresholds (ms)")
    p("  (k <= 0 plant presses before the latch arms, k >= 1 ridden; + = the gap lengthens)")
    groups = collections.defaultdict(dict)
    for key, v in d["gap"].items():
        period, param, value, k = key.split("|")
        if len(v) >= 20:
            groups[(period, param)][(float(value), int(k))] = statistics.fmean(v)
    for (period, param), g in sorted(groups.items()):
        ks = sorted({k for _, k in g})
        p(f"\n  {period}, sweeping {param}")
        p("    " + f"{'value':>7}" + "".join(f"{f'{k}->{k + 1}':>8}" for k in ks))
        for value in sorted({v for v, _ in g}):
            p("    " + f"{value:>7.2f}" + "".join(f"{g.get((value, k), float('nan')):>+8.2f}" for k in ks))

    p("\nridden presses under bottom_out candidates, by floor crossing: mean shift (ms)")
    ev = collections.defaultdict(dict)
    for key, v in d["event"].items():
        period, value, lab = key.split("|")
        ev[period][(float(value), lab)] = (statistics.fmean(v), len(v))
    for period, g in sorted(ev.items()):
        p(f"  {period}")
        for value in sorted({v for v, _ in g}):
            cells = "  ".join(f"{lab} {g[(value, lab)][0]:+6.2f} (n={g[(value, lab)][1]:,})" for lab in ("lift", "land") if (value, lab) in g)
            p(f"    {value:5.2f}  {cells}")
    text = out.getvalue()
    print(text)
    with open(out_path, "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
