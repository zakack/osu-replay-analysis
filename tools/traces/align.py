#!/usr/bin/env python3
"""Line up a session's doubletap and cursor traces with the replays saved during it.

Both traces are on CLOCK_MONOTONIC; a replay is on the map clock. Per replay the two differ
by one constant (until a pause), found from presses: every press time in the key trace minus
every press time in the replay, histogrammed, peaks at the offset.

Key presses are the virtual key-downs doubletap's own state machine emits for the trace
(`replay -e`, which runs the daemon's code over the recorded travel under the thresholds the
trace header records). Its output, deep latches included, is kept beside the trace as
<stamp>.events.csv.zst. Without the replay tool, presses fall back to a simplified rapid
trigger that ignores the deep latch, which is good enough to find an offset and no more.

    python3 tools/traces/align.py build/traces/<stamp>
"""
import bisect, collections, csv, io, json, os, re, statistics as st, subprocess, sys, tempfile
from datetime import datetime, timezone

stem = sys.argv[1]

def zread(path):
    return io.StringIO(subprocess.run(["zstd", "-dc", path], capture_output=True, text=True, check=True).stdout)

def header(lines):
    h = {}
    for l in lines:
        for k, v in re.findall(r"(\S+?)=(\S+)", l):
            h[k] = v
    return h

# --- key trace -> press edges (monotonic us) ---
kt = zread(stem + ".csv.zst").read().splitlines()
kh = header(l for l in kt if l.startswith("#"))
t0 = int(kh["t0_monotonic_ns"]) // 1000
t0_real = int(kh["t0_realtime_ns"]) / 1e9 - t0 / 1e6      # realtime = mono_us/1e6 + this
act = float(kh.get("actuation_mm", 0.08)); rel = act - 0.02
rp, rr = float(kh.get("rapid_trigger.press_mm", 0.06)), float(kh.get("rapid_trigger.release_mm", 0.3))
REPLAY = os.path.expanduser("~/Projects/doubletap/build/replay")
presses, source = [], "approximate"
events = stem + ".events.csv.zst"
if not os.path.exists(events) and os.access(REPLAY, os.X_OK):
    with tempfile.TemporaryDirectory() as tmp:
        raw, ev = os.path.join(tmp, "trace.csv"), os.path.join(tmp, "events.csv")
        open(raw, "w").write("\n".join(kt) + "\n")
        if subprocess.run([REPLAY, "-q", "-e", ev, raw], capture_output=True).returncode == 0:
            subprocess.run(["zstd", "-q", "-f", ev, "-o", events], check=True)
if os.path.exists(events):
    el = zread(events).read().splitlines()
    origin = int(header(l for l in el if l.startswith("#"))["origin_monotonic_ns"]) // 1000
    for l in el:
        if l[:1].isdigit():
            us, ev, val = l.split(",")
            if ev.startswith("KEY_") and val == "1":
                presses.append(int(us) + origin)
    source = "doubletap replay -e"
else:
    down = [False, False]; ext = [0.0, 0.0]
    for l in kt:
        if not l[:1].isdigit():
            continue
        us, a, b = l.split(",")
        us = int(us) + t0
        for k, d in enumerate((float(a), float(b))):
            if not down[k]:
                ext[k] = min(ext[k], d)
                if d >= act and d - ext[k] >= rp:
                    down[k] = True; ext[k] = d; presses.append(us)
            else:
                ext[k] = max(ext[k], d)
                if d <= ext[k] - rr or d < rel:
                    down[k] = False; ext[k] = d
presses.sort()
print(f"{len(presses)} key presses ({source})")
if not presses:
    print("no key presses in the trace"); json.dump([], open(stem + ".align.json", "w")); sys.exit(0)

# --- cursor trace ---
import os
ct = zread(stem + ".cursor.csv.zst").read().splitlines() if os.path.exists(stem + ".cursor.csv.zst") else []
ch = header(l for l in ct if l.startswith("#"))
c0 = int(ch.get("t0_monotonic_ns", 0)) // 1000
cur_t, cur_x, cur_y = [], [], []
for l in ct:
    if l[:1].isdigit():
        us, x, y, _ = l.split(",")
        cur_t.append(int(us) + c0); cur_x.append(int(x)); cur_y.append(int(y))

def cursor_at(us):
    i = bisect.bisect_right(cur_t, us) - 1
    return (cur_x[i], cur_y[i], us - cur_t[i]) if i >= 0 else None

# --- replays saved during the session ---
idx = json.load(open("build/score-index.json"))
idx = idx if isinstance(idx, list) else idx.get("entries", list(idx.values()))
lo, hi = t0 / 1e6 + t0_real, presses[-1] / 1e6 + t0_real + 120
mine = []
for x in idx:
    if x["User"] != "zaksynack": continue
    end = datetime.fromisoformat(x["Date"].replace("Z", "+00:00")[:26] + "+00:00" if x["Date"].endswith("Z") else x["Date"]).timestamp()
    if lo <= end <= hi:
        mine.append((end, x["Filename"] + ".osr", x["Beatmap"], x["Rank"]))
mine.sort()
want = {m[1] for m in mine}
rpress = collections.defaultdict(list)
for row in csv.DictReader(open("build/presses.csv")):
    if row["replay"] in want:
        rpress[row["replay"]].append((float(row["time"]), float(row["x"]), float(row["y"])))

out = []
for end, rep, name, rank in mine:
    rp_ = sorted(rpress.get(rep, []))
    if len(rp_) < 20:
        print(f"{name[:50]:50} too few presses ({len(rp_)})"); continue
    end_mono = (end - t0_real) * 1e6
    # candidate offsets: key press (us) - replay press (ms->us), within the play's plausible window
    span = (rp_[-1][0] - rp_[0][0]) * 1000
    win = [p for p in presses if end_mono - span - 120e6 <= p <= end_mono + 5e6]
    hist = collections.Counter()
    for p in win:
        for t, _, _ in rp_[:: max(1, len(rp_) // 300)]:
            hist[round((p - t * 1000) / 1000)] += 1
    off_ms, votes = hist.most_common(1)[0]
    # refine: nearest key press to each replay press, fitting offset AND drift. The map
    # clock follows the audio device, whose crystal runs ~50-70 ppm off CLOCK_MONOTONIC on
    # this machine (2026-10-11), so a 5-minute map drifts ~20 ms end to end.
    # mono_ms = off + t * (1 + drift), per segment.
    import numpy as np

    def nearest(t, off, drift):
        tgt = (off + t * (1 + drift)) * 1000
        j = bisect.bisect_left(presses, tgt)
        return min((presses[k] - tgt for k in (j - 1, j) if 0 <= k < len(presses)), key=abs) / 1000

    def fit(times, off, drift, free_drift=True):
        tol = 8.0
        for _ in range(4):
            r = np.array([nearest(t, off, drift) for t in times])
            ok = np.abs(r) < tol
            if ok.sum() < 5:
                return off, drift
            keep = ok & (np.abs(r - np.median(r[ok])) < max(1.0, tol / 2))
            if free_drift and keep.sum() > 10:
                k, c = np.polyfit(times[keep], r[keep], 1)
            else:
                k, c = 0.0, float(np.median(r[keep]))
            off += c; drift += k; tol = 4.0
        return off, drift

    T = np.array([t for t, _, _ in rp_])
    off, drift = fit(T, float(off_ms), 0.0)

    # A pause, or a seek such as skipping an intro, moves the map clock against the system
    # clock: every press after it keeps the drift but needs a new offset. Each run of presses
    # the fit leaves unmatched gets its own, searched for over the whole play's window.
    seg = np.zeros(len(T), int); offs = [off]
    for _ in range(6):
        r = np.array([nearest(t, offs[g], drift) for t, g in zip(T, seg)])
        bad = np.abs(r) >= 2
        runs, i = [], 0
        while i < len(T):
            if bad[i]:
                j = i
                while j + 1 < len(T) and bad[j + 1]:
                    j += 1
                if j - i + 1 >= 8:
                    runs.append((i, j))
                i = j + 1
            else:
                i += 1
        if not runs:
            break
        i, j = max(runs, key=lambda r: r[1] - r[0])
        hist = collections.Counter()
        for p in win:
            for t in T[i:j + 1:max(1, (j - i) // 60)]:
                hist[round(p / 1000 - t * (1 + drift))] += 1
        o2, _ = fit(T[i:j + 1], float(hist.most_common(1)[0][0]), drift, free_drift=False)
        r2 = np.array([nearest(t, o2, drift) for t in T[i:j + 1]])
        if np.mean(np.abs(r2) < 2) < 0.8:
            break
        offs.append(o2); seg[i:j + 1] = len(offs) - 1
    res = np.array([nearest(t, offs[g], drift) for t, g in zip(T, seg)])
    matched = int((np.abs(res) < 2).sum())
    res = res[np.abs(res) < 2]
    # segments in map time: [from, to) and the offset that holds there
    segments, start_i = [], 0
    for i in range(1, len(T) + 1):
        if i == len(T) or seg[i] != seg[start_i]:
            lo = -1e12 if start_i == 0 else (T[start_i - 1] + T[start_i]) / 2
            hi = 1e12 if i == len(T) else (T[i - 1] + T[i]) / 2
            segments.append(dict(from_ms=float(lo), to_ms=float(hi), offset_ms=float(offs[seg[start_i]])))
            start_i = i
    off_last = segments[-1]["offset_ms"]
    out.append(dict(replay=rep, offset_ms=float(offs[int(np.bincount(seg).argmax())]),   # the segment holding most presses
                    drift=float(drift), segments=segments,
                    end_minus_last=(end_mono / 1000 - (off_last + rp_[-1][0] * (1 + drift))) / 1000))
    q = np.percentile(res, [25, 75]) if len(res) else (0, 0)
    print(f"{name[:46]:46} {rank:2} presses {len(rp_):4}  matched ±2ms {matched / len(rp_):5.1%}  "
          f"resid IQR {q[0]:+.2f}..{q[1]:+.2f} ms  drift {drift * 1e6:+5.1f} ppm  "
          f"{len(segments)} segment{'s' if len(segments) > 1 else ''}  "
          f"last press → score saved {out[-1]['end_minus_last']:+.1f}s")

    def mono_of(t):
        for sg in segments:
            if sg["from_ms"] <= t < sg["to_ms"]:
                return (sg["offset_ms"] + t * (1 + drift)) * 1000
    # cursor: affine fit, screen px -> playfield, at replay press positions
    pts = []
    for t, x, y in rp_:
        c = cursor_at(mono_of(t))
        if c: pts.append((c[0], c[1], x, y, c[2]))
    if len(pts) > 20:
        import numpy as np
        A = np.array([[p[0], p[1], 1] for p in pts]); X = np.array([[p[2], p[3]] for p in pts])
        # Trimmed: a press before the map's clock starts (a skip, a stray tap at load) sits
        # nowhere near its trace position and would drag a plain fit off every other press.
        keep = np.ones(len(A), bool)
        for _ in range(3):
            M, *_ = np.linalg.lstsq(A[keep], X[keep], rcond=None)
            r = np.hypot(*(A @ M - X).T)
            keep = r < max(5.0, 3 * np.median(r))
        out[-1]["outliers"] = int((~keep).sum())
        out[-1]["affine"] = M.tolist()       # [x, y, 1] screen px -> [x, y] playfield
        out[-1]["first_ms"], out[-1]["last_ms"] = rp_[0][0], rp_[-1][0]
        out[-1].update(scale=float(np.hypot(*M[0])), resid_p50=float(np.median(r)), resid_p95=float(np.percentile(r, 95)))
        gaps = sorted(p[4] for p in pts)
        print(f"{'':46}    cursor fit {1 / np.hypot(*M[0]):.3f} screen px per osu px, residual median {np.median(r):.2f} p95 {np.percentile(r, 95):.2f} osu px, {(~keep).sum()} dropped;"
              f" age of cursor sample at press median {gaps[len(gaps) // 2] / 1000:.1f} ms")
json.dump(out, open(stem + ".align.json", "w"), indent=1)
