"""Stream timing drift, as a regression monitor for the tapping hand.

Step 5 question 1 (``step5.py``, commit 6206b43) found timing spread growing with position inside
all-circle 1/4 runs while aim error stayed flat, and read it as the tapping hand. Since then
``streams.py`` showed that some of what lazer records inside a stream is not timing at all: a
circle judged by its neighbour's tap ("pushback" / "ahead" chains) carries a whole-interval
error. This module redoes the measurement with those circles removed and turns it into
something to run after each session, so weeks spent drilling aim can be checked for a tapping
regression.

Runs. The step-5 run definition, through ``streams.py``'s run builder: rhythm.csv at tolerance
0.02, maximal stretches of tapped objects (circles, slider heads) exactly one snap apart, five or
longer, kept here only at snap 1/4 with every interior object a circle. Presses are aligned to
circles by ``streams.classify``. A circle enters the drift statistics only when it was judged by
the tap meant for it (judge "self") inside a run whose press rate checked "ok"; everything else
is counted by reason and reported.

Units. Hit error is map time; it is divided by the playback rate here, so every number is real
milliseconds and BPM is the rate-applied tempo the hand actually played. Raw timing is
``hitError - offset`` (offsets.py), with "ambiguous" offsets dropped.

Two kinds of number, kept apart:
  slope          ms per tap: the pooled within-run least-squares slope of raw error on position
                 (each run centred on its own means). Offsets, setup and map bias are constant
                 inside a run and cancel. + means the taps slide later through the run.
  spread growth  ms per tap: the same within-run slope fitted to sqrt(pi/2)*|residual| (the
                 residual after the run's mean and the pooled slope, clipped at CLIP ms), so a
                 unit is one ms of standard deviation gained per tap.
  within spread  robust SD (1.4826 MAD) of error about its own run's mean, corrected for the
                 mean's degree of freedom. Offset-free consistency.
  level          mean raw error with the known setup steps removed: 4-core Lutris launches
                 (+2.3 ms, 2025-10-21 to 2026-10-07 20:46), speakers (+1.7 ms, per session in
                 build/sessions.jsonl). 2026-10-07 20:46 to 2026-10-08 01:52 mixed core counts
                 and is left out of levels. Secondary: it moves with every setup change.
The evclack latency bug (2026-10-09 04:40-04:58) is excluded from everything.

CIs are 95% percentile bootstraps over beatmaps (md5), seed fixed, so a rerun prints the same.

    python3 tools/analysis/drift.py cache              # once, ~minutes: from rhythm/presses/onset/geometry CSVs
    python3 tools/analysis/drift.py report [--out build/drift.txt]
    python3 tools/analysis/drift.py latest [--sessions N] [--days D]
                                                       # after a session: the last N sessions vs the D days before
    python3 tools/analysis/drift.py sensitivity [--out build/drift-sensitivity.txt]
                                                       # every number above under other stream labellings

Label sensitivity (`sensitivity`, measured 2026-10-14). Which circles are kept rests on the
stream alignment's labels, so every number is recomputed under the first, flat-cost alignment,
seven one-at-a-time nudges to the current costs, and two labellings with no alignment at all
(keep every circle; drop lazer's Mehs). What held:
  headline       slope +0.49..+0.57 and growth +0.10..+0.13 ms/tap under every alignment variant,
                 +0.41 and +0.13..+0.15 with none. Label-free.
  months         the sign flips between alignments (2025-01, -03, -05) are all months whose CI
                 includes zero under every variant: noise around zero, not a label effect.
  step 5         own-tap SD slope by position: current +0.69/+0.80/+0.56 (runs 5-8/9-12/13+),
                 dropping Mehs with no alignment +0.72/+0.86/+0.60, the flat-cost alignment
                 +0.22/+0.43/+0.28. The current reading is backed by a label-free check; runs
                 of 13+ move +0.45..+0.66 with the gap wall (FAR 4..16).
  latest         a single session rarely reaches MIN_FLAG_MAPS, so it rarely flags at all.
                 Pooled by week over 30 weeks, the current costs flag 9; a one-parameter nudge
                 changes the verdict in 4, the flat-cost alignment in 5. A lone weekly flag sits
                 near its CI edge: a prompt to look, not a verdict.

``report`` and ``latest`` first extend the cache with any of the player's replays in
build/corpus.json it lacks, by running ``ora rhythm/presses/onset`` on just those (seconds a
session), since sync.sh keeps geometry incremental but not rhythm or presses.
"""
from __future__ import annotations

import bisect
import collections
import csv
import datetime as dt
import json
import math
import os
import pickle
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from offsets import offset_at, play_time  # noqa: E402
from streams import MAX_SNAP, MIN_RUN, classify  # noqa: E402

CACHE = "build/drift.pickle"
NEW = "build/drift-new"
ORA = "src/Cli/bin/Release/net10.0/ora"
PLAYER = "zaksynack playing"
VERSION = 3            # 2: judge labels from the evidence-gated stream alignment (streams.py)
                       # 3: each run keeps its press window, so `sensitivity` can relabel it

SNAP = 0.25
CLIP = 50.0          # ms; a residual beyond this is a 50 or a stray, not drift
BOOT = 2000
MIN_N = 200          # step 5's floor for a per-position cell
MIN_RUN_POINTS = 3   # a run contributes to a slope with this many kept circles
# Slopes are fitted over positions 4-16. The entry is a transient: the first tap about +4 ms
# against the run, the second and third -4 and -3 as the hand overcorrects, settled by the
# fourth. A line through 2-16 reads that settling as drift (+0.66 ms/tap in the analog era, falling
# with run length as the transient is diluted); from 4 on it is +0.4 and the entry column owns
# taps 1-3. Some decay with run length remains, so read slope beside the length split. And
# a run's weight in a pooled slope grows as its length cubed, so without a cap a handful of
# 64-tap marathon streams decide the number and the beatmap bootstrap swings with them.
FIT_POS = (4, 16)
MIN_FLAG_MAPS = 8    # `latest` flags nothing on fewer beatmaps: the bootstrap resamples maps

EDT = dt.timezone(dt.timedelta(hours=-4))
T = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=EDT)
EVCLACK = (T("2026-10-09T04:40"), T("2026-10-09T04:59"))          # setup-timeline.csv, audio
CORES4 = (T("2025-10-21T00:00"), T("2026-10-07T20:46"))            # setup-timeline.csv, client
CORES_MIXED = (T("2026-10-07T20:46"), T("2026-10-08T01:53"))
CORES_SHIFT, SPEAKER_SHIFT = 2.3, 1.7

# setup-timeline.csv "buttons" rows, as tools/analysis/stream_eras.py cuts them.
ERAS = [("plain, before QMK", "0000", "2024-11-01"), ("QMK toggle", "2024-11-01", "2025-02-08"),
        ("plain by choice", "2025-02-08", "2025-05-05"), ("glidepad", "2025-05-05", "2025-09-12"),
        ("glidepad + hold threshold", "2025-09-12", "2025-11-04"),
        ("state-machine rewrite", "2025-11-04", "2026-01-03"),
        ("doubletap, exclusive", "2026-01-03", "2026-08-29"), ("doubletap, analog", "2026-08-29", "9999")]
CURRENT_ERA = len(ERAS) - 1

REASONS = ["kept", "unalignable run", "phase-shift run", "shifted, anchored", "shifted, from run start",
           "double tap", "offset ambiguous", "evclack bug"]
JUDGE = {"self": 0, "next": 1, "prev": 2, "far": 3, "extra": 4, "none": 5}


# ------------------------------------------------------------------------------------------
# Extraction: rhythm rows -> runs -> classified circles
# ------------------------------------------------------------------------------------------

def _runs(rows: list[tuple]) -> list[list[tuple]]:
    """streams.cache's run builder for one replay, keeping each object's kind.
    rows: (runId, time, beat, snap, result, error, kind)."""
    by_run = collections.defaultdict(list)
    for run_id, *ev in rows:
        by_run[run_id].append(tuple(ev))
    out = []
    for events in by_run.values():
        events.sort()
        cur = [events[0]]
        for ev in events[1:]:
            if abs((ev[0] - cur[-1][0]) / ev[1] - ev[2]) <= 0.02:
                cur.append(ev)
            else:
                if len(cur) >= MIN_RUN:
                    out.append(cur)
                cur = [ev]
        if len(cur) >= MIN_RUN:
            out.append(cur)
    return out


def _rhythm_row(r: dict):
    if r["tolerance"] != "0.02" or not r["snap"] or float(r["snap"]) > MAX_SNAP:
        return None
    if r["kind"] not in ("circle", "sliderHead") or int(r["runSize"]) < MIN_RUN:
        return None
    return (r["runId"], float(r["time"]), float(r["beatLength"]), float(r["snap"]), r["result"],
            float(r["error"]) if r["error"] else None, r["kind"])


def _player_lines(path: str, extra=None):
    """csv.DictReader over the player's lines only, filtered as text before parsing."""
    with open(path, newline="") as fh:
        header = next(csv.reader([fh.readline()]))
        tag = '"' + PLAYER
        lines = (ln for ln in fh if ln.startswith(tag) and (extra is None or extra in ln))
        yield from csv.DictReader(lines, fieldnames=header)


def _classify_replay(name: str, rows: list[tuple], presses: list[tuple], aim: dict) -> list[dict]:
    out = []
    for events in _runs(rows):
        if abs(events[0][2] - SNAP) > 1e-6:
            continue
        kinds = [e[5] for e in events]
        if any(k != "circle" for k in kinds[1:-1]):
            continue
        objects = [e[0] for e in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(presses, (objects[0] - interval,))
        hi = bisect.bisect_right(presses, (objects[-1] + interval, "~"))
        res = classify(objects, [e[3] for e in events], [e[4] for e in events], presses[lo:hi], snap, beat)
        out.append({"beat": beat, "snap": snap, "presses": presses[lo:hi],
                    "verdict": res["verdict"], "times": objects, "kinds": kinds,
                    "results": [e[3] for e in events], "errors": [e[4] for e in events],
                    "judge": res["judge"], "aim": [aim.get((name, round(t, 1))) for t in objects]})
    return out


def _corpus() -> dict[str, dict]:
    return {os.path.basename(e["Path"]): e for e in json.load(open("build/corpus.json"))
            if e["Paired"] and os.path.basename(e["Path"]).startswith(PLAYER)}


def _extract(rhythm: str, presses: str, onset: str, geometry: str | None, names: set[str]) -> dict:
    rows = collections.defaultdict(list)
    for r in _player_lines(rhythm, '",1,0.02,'):
        if r["replay"] in names and (row := _rhythm_row(r)) is not None:
            rows[r["replay"]].append(row)
    ps = collections.defaultdict(list)
    for r in _player_lines(presses):
        if r["replay"] in names:
            ps[r["replay"]].append((float(r["time"]), r["outcome"], float(r["target"]) if r["target"] else None))
    for v in ps.values():
        v.sort()
    rate, last = {}, None
    for r in _player_lines(onset):
        if r["replay"] != last:
            rate[r["replay"]] = float(r["rate"] or 1)
            last = r["replay"]
    aim = {}
    if geometry:
        for r in _player_lines(geometry, ",circle,"):
            if r["kind"] == "circle" and r["aimErrorRadii"]:
                aim[(r["replay"], round(float(r["startTime"]), 1))] = float(r["aimErrorRadii"])
    corpus = _corpus()
    out = {}
    for name in names:
        out[name] = {"md5": corpus[name]["BeatmapMd5"] if name in corpus else None,
                     "rate": rate.get(name, 1.0),
                     "runs": _classify_replay(name, rows[name], ps.get(name, []), aim) if name in rows else []}
    return out


def cache() -> None:
    names = set(_corpus())
    data = _extract("build/rhythm.csv", "build/presses.csv", "build/onset.csv", "build/geometry.csv", names)
    pickle.dump({"version": VERSION, "replays": data}, open(CACHE, "wb"))
    runs = sum(len(v["runs"]) for v in data.values())
    print(f"{len(data)} replays, {runs} all-circle 1/4 stream runs -> {CACHE}")


def update() -> dict:
    if not os.path.exists(CACHE):
        sys.exit(f"no {CACHE}; run `python3 tools/analysis/drift.py cache` once")
    store = pickle.load(open(CACHE, "rb"))
    if store.get("version") != VERSION:
        sys.exit(f"{CACHE} is from another version; rerun `drift.py cache`")
    corpus = _corpus()
    missing = sorted(set(corpus) - set(store["replays"]))
    if not missing:
        return store
    if len(missing) > 300:
        sys.exit(f"{len(missing)} replays missing from {CACHE}; rerun `drift.py cache` instead")
    os.makedirs(NEW, exist_ok=True)
    sub = f"{NEW}/corpus.json"
    json.dump([corpus[n] for n in missing], open(sub, "w"))
    for cmd, extra in (("rhythm", ["1", "0.02"]), ("presses", []), ("onset", [])):
        subprocess.run([ORA, cmd, *extra, "--corpus", sub, "--out", f"{NEW}/{cmd}.csv"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    store["replays"].update(_extract(f"{NEW}/rhythm.csv", f"{NEW}/presses.csv", f"{NEW}/onset.csv", None, set(missing)))
    pickle.dump(store, open(CACHE, "wb"))
    print(f"cache: +{len(missing)} replays")
    return store


# ------------------------------------------------------------------------------------------
# Flat table
# ------------------------------------------------------------------------------------------

def _sessions():
    out = []
    if os.path.exists("build/sessions.jsonl"):
        for line in open("build/sessions.jsonl"):
            s = json.loads(line)
            out.append((dt.datetime.fromisoformat(s["start"]), dt.datetime.fromisoformat(s["end"]), s.get("output")))
    return out


def _era(t: dt.datetime) -> int:
    d = t.strftime("%Y-%m-%d")
    return next(i for i, (_, a, b) in enumerate(ERAS) if a <= d < b)


def table(store: dict) -> dict:
    """One row per circle with a hit error in a qualifying run, kept or not (reason says)."""
    sessions = _sessions()
    names = sorted(n for n, v in store["replays"].items() if v["runs"] and play_time(n))
    times = {n: play_time(n) for n in names}
    # Session per replay: a sessions.jsonl window when one contains it, else gaps over 30 min.
    order = sorted(names, key=lambda n: times[n])
    session, gid, prev, prev_key = {}, 0, None, ""
    slack = dt.timedelta(minutes=2)
    for n in order:
        t = times[n]
        win = next((i for i, (a, b, _) in enumerate(sessions) if a - slack <= t <= b + slack), None)
        if win is not None:
            session[n] = f"w{win}"
        else:
            if prev is None or t - prev > dt.timedelta(minutes=30) or prev_key.startswith("w"):
                gid += 1
            session[n] = f"g{gid}"
        prev, prev_key = t, session[n]
    output = {f"w{i}": o for i, (_, _, o) in enumerate(sessions)}

    cols = collections.defaultdict(list)
    md5_ids, run_id = {}, 0
    for ri, n in enumerate(names):
        v = store["replays"][n]
        t = times[n]
        rate = v["rate"] or 1.0
        off, status = offset_at(v["md5"], n) if v["md5"] else (0.0, "ambiguous")
        mi = md5_ids.setdefault(v["md5"], len(md5_ids))
        evclack = EVCLACK[0] <= t < EVCLACK[1]
        corr = (CORES_SHIFT if CORES4[0] <= t < CORES4[1] else 0.0) + \
               (SPEAKER_SHIFT if output.get(session[n]) == "speakers" else 0.0)
        level_ok = not (CORES_MIXED[0] <= t < CORES_MIXED[1])
        era = _era(t)
        for run in v["runs"]:
            n_obj = len(run["times"])
            judge = run["judge"]
            for i, (err, j, kind) in enumerate(zip(run["errors"], judge, run["kinds"])):
                if err is None:
                    continue
                if run["verdict"] != "ok":
                    reason = 1 if run["verdict"] == "unalignable" else 2
                elif j in ("next", "prev", "far"):
                    reason = 3 if "self" in judge[:i] else 4
                elif j != "self":
                    reason = 5
                elif status == "ambiguous":
                    reason = 6
                elif evclack:
                    reason = 7
                else:
                    reason = 0
                cols["replay"].append(ri)
                cols["md5"].append(mi)
                cols["run"].append(run_id)
                cols["pos"].append(i + 1)
                cols["len"].append(n_obj)
                cols["bpm"].append(60000 / run["beat"] * rate)
                cols["err"].append((err - off) / rate)
                cols["corr"].append(corr)
                cols["level_ok"].append(level_ok)
                cols["reason"].append(reason)
                cols["era"].append(era)
                cols["t"].append(t.timestamp())
                a = run["aim"][i]
                cols["aim"].append(np.nan if a is None else a)
            run_id += 1
    tab = {k: np.asarray(v) for k, v in cols.items()}
    tab["names"] = names
    tab["session"] = np.asarray([session[names[r]] for r in tab["replay"]])
    tab["month"] = np.asarray([dt.datetime.fromtimestamp(x, EDT).strftime("%Y-%m") for x in tab["t"]])
    return tab


# ------------------------------------------------------------------------------------------
# Statistics
# ------------------------------------------------------------------------------------------

def _robust_sd(x):
    return 1.4826 * float(np.median(np.abs(x - np.median(x)))) if len(x) else float("nan")


class Fit:
    """Pooled within-run slope and spread growth over a subset, with beatmap-cluster bootstrap."""

    def __init__(self, tab: dict, mask: np.ndarray, boot: int = BOOT, positions: tuple[int, int] = FIT_POS):
        self.entry, self.entry_ci = self._entry(tab, mask, boot)
        mask = mask & (tab["pos"] >= positions[0]) & (tab["pos"] <= positions[1])
        run, x, y, md5 = tab["run"][mask], tab["pos"][mask].astype(float), tab["err"][mask], tab["md5"][mask]
        # Runs with too few kept circles carry no slope.
        u, inv, cnt = np.unique(run, return_inverse=True, return_counts=True)
        keep = cnt[inv] >= MIN_RUN_POINTS
        run, x, y, md5 = run[keep], x[keep], y[keep], md5[keep]
        self.n, self.runs = len(x), len(np.unique(run))
        self.maps = len(np.unique(md5))
        if self.n == 0:
            self.slope = self.growth = self.within = float("nan")
            self.ci = {k: (float("nan"),) * 2 for k in ("slope", "growth", "within")}
            self.draws = {}
            return
        u, r = np.unique(run, return_inverse=True)
        c = np.bincount(r)
        xc = x - (np.bincount(r, x) / c)[r]
        yc = y - (np.bincount(r, y) / c)[r]
        m_u, m = np.unique(md5, return_inverse=True)
        sxy, sxx = np.bincount(m, xc * yc), np.bincount(m, xc * xc)
        self.slope = sxy.sum() / sxx.sum()
        resid = yc - self.slope * xc
        s = math.sqrt(math.pi / 2) * np.minimum(np.abs(resid), CLIP)
        sc = s - (np.bincount(r, s) / c)[r]
        txy = np.bincount(m, xc * sc)
        self.growth = txy.sum() / sxx.sum()
        dev = np.abs(yc) / np.sqrt(1 - 1 / c[r])
        self.within = 1.4826 * float(np.median(dev))

        rng = np.random.default_rng(0)
        M = len(m_u)
        w = rng.multinomial(M, np.full(M, 1 / M), size=boot).astype(float)
        bs = (w @ sxy) / (w @ sxx)
        bg = (w @ txy) / (w @ sxx)
        # Weighted median of dev for the within-run spread.
        order = np.argsort(dev)
        dsorted, msorted = dev[order], m[order]
        bw = np.empty(min(boot, 500))
        for k in range(len(bw)):
            cw = np.cumsum(w[k][msorted])
            bw[k] = 1.4826 * dsorted[np.searchsorted(cw, cw[-1] / 2)]
        self.draws = {"slope": bs, "growth": bg, "within": bw}
        self.ci = {k: tuple(np.nanpercentile(v, [2.5, 97.5])) for k, v in self.draws.items()}

    @staticmethod
    def _entry(tab, mask, boot):
        """Entry tap: position 1 minus the mean of positions 2-5 of the same run, in ms."""
        sel = mask & (tab["pos"] <= 5)
        run, pos, y, md5 = tab["run"][sel], tab["pos"][sel], tab["err"][sel], tab["md5"][sel]
        first = {r: v for r, p_, v in zip(run, pos, y) if p_ == 1}
        rest = collections.defaultdict(list)
        maps = {}
        for r, p_, v, m in zip(run, pos, y, md5):
            if p_ > 1 and r in first:
                rest[r].append(v)
                maps[r] = m
        d = [(maps[r], first[r] - sum(v) / len(v)) for r, v in rest.items() if len(v) >= 2]
        if not d:
            return float("nan"), (float("nan"),) * 2
        mi, val = np.unique([a for a, _ in d], return_inverse=True)[1], np.array([b for _, b in d])
        sums, cnts = np.bincount(mi, val), np.bincount(mi).astype(float)
        M = len(sums)
        w = np.random.default_rng(1).multinomial(M, np.full(M, 1 / M), size=boot).astype(float)
        draws = (w @ sums) / np.maximum(w @ cnts, 1e-9)
        return float(val.mean()), tuple(np.percentile(draws, [2.5, 97.5]))

    def fmt(self, key: str, width: int = 22) -> str:
        v = getattr(self, key)
        lo, hi = self.entry_ci if key == "entry" else self.ci[key]
        if math.isnan(v):
            return "--".rjust(width)
        if key in ("within",):
            return f"{v:.1f} [{lo:.1f},{hi:.1f}]".rjust(width)
        d = 1 if key == "entry" else 3
        return f"{v:+.{d}f} [{lo:+.{d}f},{hi:+.{d}f}]".rjust(width)


def _level(tab, mask):
    m = mask & tab["level_ok"]
    return float(np.mean(tab["err"][m] - tab["corr"][m])) if m.any() else float("nan")


# ------------------------------------------------------------------------------------------
# Report
# ------------------------------------------------------------------------------------------

POS_BUCKETS = [(i, i, str(i)) for i in range(1, 17)] + [(17, 24, "17-24"), (25, 32, "25-32"),
                                                         (33, 48, "33-48"), (49, 10 ** 6, "49+")]
BPM_BANDS = [(0, 160, "<160"), (160, 180, "160-180"), (180, 200, "180-200"), (200, 220, "200-220"),
             (220, 10 ** 6, "220+")]
LEN_BANDS = [(5, 8, "5-8"), (9, 12, "9-12"), (13, 16, "13-16"), (17, 32, "17-32"), (33, 10 ** 6, "33+")]


def report(out_path: str | None) -> None:
    tab = table(update())
    kept = tab["reason"] == 0
    era = tab["era"] == CURRENT_ERA
    lines = []
    p = lines.append

    total = len(kept)
    p(f"stream drift monitor, {PLAYER.split()[0]}: all-circle 1/4 runs (>= {MIN_RUN} taps), "
      f"real ms (rate applied), raw = hitError - offset")
    p(f"{total} circles with a hit error in {len(np.unique(tab['run']))} runs; kept {kept.sum()} "
      f"({100 * kept.mean():.1f}%). Excluded:")
    for k, name in enumerate(REASONS[1:], 1):
        nk = int((tab["reason"] == k).sum())
        p(f"  {name:<26}{nk:8}  {100 * nk / total:5.2f}%")

    def fitline(label, mask, width=12):
        f = Fit(tab, mask)
        return f"  {label:<{width}}{f.n:>8}{f.runs:>7}{f.maps:>6}{f.fmt('slope')}{f.fmt('growth')}{f.fmt('within', 20)}{f.fmt('entry', 20)}" \
               f"{_level(tab, mask):>8.1f}"

    head = lambda w=12: f"  {'':<{w}}{'circles':>8}{'runs':>7}{'maps':>6}{'slope ms/tap':>22}{'growth ms/tap':>22}" \
                        f"{'within SD ms':>20}{'entry ms':>20}{'level':>8}"

    p(f"\nheadline. slope and growth fitted over positions {FIT_POS[0]}-{FIT_POS[1]}: slope + = taps slide later "
      f"through a run, growth = ms of SD gained per tap; entry = first tap minus positions 2-5")
    p(head())
    p(fitline("all time", kept))
    p(fitline("analog era", kept & era))
    yr = np.char.startswith(tab["month"], "2025")
    p(fitline("2025", kept & yr))

    p(f"\nper position in run, analog era (pooled over run lengths; long positions exist only in long runs)")
    p(f"  {'pos':<7}{'n':>8}{'mean raw':>10}{'mean in-run':>12}{'spread raw':>12}{'spread in-run':>14}")
    sel = kept & era
    run, y = tab["run"][sel], tab["err"][sel]
    u, r = np.unique(run, return_inverse=True)
    yc = y - (np.bincount(r, y) / np.bincount(r))[r]
    pos = tab["pos"][sel]
    for lo, hi, label in POS_BUCKETS:
        m = (pos >= lo) & (pos <= hi)
        if m.sum() < 30:
            continue
        p(f"  {label:<7}{m.sum():>8}{y[m].mean():>+10.2f}{yc[m].mean():>+12.2f}{_robust_sd(y[m]):>12.2f}{_robust_sd(yc[m]):>14.2f}")

    for title, key, bands in (("stream BPM (rate applied)", "bpm", BPM_BANDS), ("run length", "len", LEN_BANDS)):
        p(f"\nby {title}, analog era")
        p(head())
        for lo, hi, label in bands:
            m = kept & era & (tab[key] >= lo) & (tab[key] < hi if key == "bpm" else tab[key] <= hi)
            p(fitline(label, m))
        p(f"  all time, by {title}")
        for lo, hi, label in bands:
            m = kept & (tab[key] >= lo) & (tab[key] < hi if key == "bpm" else tab[key] <= hi)
            p(fitline(label, m))

    p("\nby input era (setup-timeline.csv buttons rows)")
    p(head(28))
    for i, (name, _, _) in enumerate(ERAS):
        m = kept & (tab["era"] == i)
        if m.sum():
            p(fitline(name, m, 28))

    p("\nby month (level: setup-corrected mean raw; months under 500 circles are noise)")
    p(head(9))
    for mo in sorted(set(tab["month"])):
        m = kept & (tab["month"] == mo)
        if m.sum():
            p(fitline(mo, m, 9))

    # Step 5 again: hit SD by position, as step5.py printed it, every circle with a hit error vs
    # only the circles that took their own tap. The aim column is the self-judged circles.
    p("\nstep 5 rerun: hit SD (pstdev, ms) by position, every judged circle vs own-tap circles only, all time")
    for b, lo, hi, limit in (("5-8", 5, 8, 8), ("9-12", 9, 12, 12), ("13+", 13, 10 ** 6, 17)):
        p(f"  runs of {b}:  {'pos':<5}{'n all':>8}{'sd all':>8}{'n own':>8}{'sd own':>8}{'aim own (r)':>13}")
        band = (tab["len"] >= lo) & (tab["len"] <= hi) & (tab["reason"] != 7) & (tab["reason"] != 6)
        rows = []
        for i in range(1, limit + 1):
            ma, mo = band & (tab["pos"] == i), band & (tab["pos"] == i) & kept
            if ma.sum() < MIN_N or mo.sum() < MIN_N:
                continue
            aim = tab["aim"][mo]
            rows.append((i, np.std(tab["err"][ma]), np.std(tab["err"][mo])))
            p(f"  {'':<12}{i:<5}{ma.sum():>8}{rows[-1][1]:>8.2f}{mo.sum():>8}{rows[-1][2]:>8.2f}"
              f"{np.nanmean(aim) if np.isfinite(aim).any() else float('nan'):>13.3f}")
        if len(rows) >= 2:
            xs = np.array([q[0] for q in rows], float)
            p(f"  {'':<12}sd slope over position: all {np.polyfit(xs, [q[1] for q in rows], 1)[0]:+.3f}, "
              f"own taps {np.polyfit(xs, [q[2] for q in rows], 1)[0]:+.3f} ms/tap")

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


def _band(v, bands, upper_inclusive):
    for lo, hi, label in bands:
        if lo <= v and (v <= hi if upper_inclusive else v < hi):
            return label
    return "?"


def _side(tab, mask, slope=None):
    """Per-row pieces of the within-run statistics for one side of a comparison, fitted rows only."""
    mask = mask & (tab["pos"] >= FIT_POS[0]) & (tab["pos"] <= FIT_POS[1])
    idx = np.flatnonzero(mask)
    run = tab["run"][idx]
    u, inv, cnt = np.unique(run, return_inverse=True, return_counts=True)
    idx = idx[cnt[inv] >= MIN_RUN_POINTS]
    run, x, y = tab["run"][idx], tab["pos"][idx].astype(float), tab["err"][idx]
    u, r = np.unique(run, return_inverse=True)
    c = np.bincount(r)
    xc = x - (np.bincount(r, x) / c)[r]
    yc = y - (np.bincount(r, y) / c)[r]
    b = (xc * yc).sum() / (xc * xc).sum() if slope is None else slope
    sabs = math.sqrt(math.pi / 2) * np.minimum(np.abs(yc - b * xc), CLIP)
    sc = sabs - (np.bincount(r, sabs) / c)[r]
    dev = math.sqrt(math.pi / 2) * np.minimum(np.abs(yc) / np.sqrt(1 - 1 / c[r]), CLIP)
    return {"idx": idx, "xy": xc * yc, "xx": xc * xc, "txy": xc * sc, "dev": dev, "one": np.ones(len(idx))}


def matched(tab, sess, base, boot=BOOT):
    """Session minus baseline with the baseline matched to the session's composition.

    Slope depends on run length and BPM, so a session's map mix moves it on its own. Each
    session beatmap is compared with the same beatmap in the baseline when the baseline played
    it, and otherwise with the baseline's (run length x BPM band) cell, never with both. Per
    key, the statistic is computed on each side; the difference is averaged with the session's
    own weights (its Sxx for slope and growth, its circle count for within-run spread), over
    keys present on both sides. The CI resamples beatmaps on each side independently."""
    S, B = _side(tab, sess), _side(tab, base)
    shared = set(tab["md5"][S["idx"]]) & set(tab["md5"][B["idx"]])

    def keys(side):
        i = side["idx"]
        return [("m", m) if m in shared else
                ("c", _band(L, LEN_BANDS, True), _band(bpm, BPM_BANDS, False))
                for m, L, bpm in zip(tab["md5"][i], tab["len"][i], tab["bpm"][i])]

    ks, kb = keys(S), keys(B)
    common = sorted(set(ks) & set(kb), key=str)
    kid = {k: j for j, k in enumerate(common)}
    out = {"keys": len(common), "maps shared": len(shared)}

    def mats(side, klist):
        sel = np.array([k in kid for k in klist], bool)
        m_u, m = np.unique(tab["md5"][side["idx"]][sel], return_inverse=True)
        kk = np.array([kid[k] for k, ok in zip(klist, sel) if ok], int)
        cell = m * len(common) + kk
        size = len(m_u) * len(common)
        A = {f: np.bincount(cell, side[f][sel], minlength=size).reshape(len(m_u), len(common))
             for f in ("xy", "xx", "txy", "dev", "one")}
        return A, len(m_u), int(sel.sum())

    As, Ms, ns = mats(S, ks)
    Ab, Mb, nb = mats(B, kb)
    out["coverage"] = ns / max(1, len(ks))
    if not common:
        return out
    rng = np.random.default_rng(2)
    ws = np.vstack([np.ones(Ms), rng.multinomial(Ms, np.full(Ms, 1 / Ms), size=boot)]).astype(float)
    wb = np.vstack([np.ones(Mb), rng.multinomial(Mb, np.full(Mb, 1 / Mb), size=boot)]).astype(float)
    T = lambda w, A, f: w @ A[f]
    with np.errstate(divide="ignore", invalid="ignore"):
        for name, num, den, weight in (("slope", "xy", "xx", "xx"), ("growth", "txy", "xx", "xx"),
                                       ("within", "dev", "one", "one")):
            vs = T(ws, As, num) / T(ws, As, den)
            vb = T(wb, Ab, num) / T(wb, Ab, den)
            w = T(ws, As, weight)
            ok = np.isfinite(vs) & np.isfinite(vb) & (w > 0)
            w = np.where(ok, w, 0)
            diff = (np.where(ok, vs - vb, 0) * w).sum(1) / w.sum(1)
            sess_v = (np.where(ok, vs, 0) * w).sum(1) / w.sum(1)
            out[name] = (sess_v[0], sess_v[0] - diff[0], diff[0], tuple(np.nanpercentile(diff[1:], [2.5, 97.5])))
    return out


def latest(days: int = 30, pool: int = 1) -> None:
    tab = table(update())
    kept = tab["reason"] == 0
    if not kept.any():
        sys.exit("no kept stream circles")
    # The last `pool` sessions that have kept stream circles, newest first.
    ends = {}
    for k, t in zip(tab["session"][kept], tab["t"][kept]):
        ends[k] = max(ends.get(k, 0), t)
    chosen = sorted(ends, key=ends.get, reverse=True)[:pool]
    in_sess = np.isin(tab["session"], chosen)
    last = "+".join(sorted(chosen, key=ends.get))
    sess = kept & in_sess
    t0 = tab["t"][in_sess].min()
    base = kept & ~in_sess & (tab["t"] < t0) & (tab["t"] >= t0 - days * 86400)
    era = kept & (tab["era"] == CURRENT_ERA) & (tab["t"] < t0)

    start = dt.datetime.fromtimestamp(t0, EDT)
    reps = sorted({tab["names"][i] for i in np.unique(tab["replay"][in_sess])})
    excl = in_sess & ~kept
    p = print
    p(f"latest session {last} from {start:%Y-%m-%d %H:%M} EDT: {len(reps)} replays with all-circle 1/4 streams, "
      f"{sess.sum()} kept circles, {excl.sum()} excluded "
      f"({', '.join(f'{REASONS[k]} {int(((tab['reason'] == k) & excl).sum())}' for k in range(1, len(REASONS)) if ((tab['reason'] == k) & excl).any())})")
    newest = [n for n in reps if EVCLACK[0] <= play_time(n) < EVCLACK[1]]
    if newest:
        p(f"  {len(newest)} of them in the evclack bug window, excluded")

    fs, fb, fe = Fit(tab, sess), Fit(tab, base), Fit(tab, era)
    for n in reps:
        p(f"    {n[len(PLAYER) + 1:-4]}")
    p(f"\n  {'':<22}{'session':>24}{f'trailing {days} d':>24}{'analog era':>24}")
    p(f"  {'circles / runs / maps':<22}{f'{fs.n}/{fs.runs}/{fs.maps}':>24}{f'{fb.n}/{fb.runs}/{fb.maps}':>24}{f'{fe.n}/{fe.runs}/{fe.maps}':>24}")
    for key, label in (("slope", "slope ms/tap"), ("growth", "spread growth ms/tap"),
                       ("within", "within-run SD ms"), ("entry", "entry tap ms")):
        p(f"  {label:<22}{fs.fmt(key, 24)}{fb.fmt(key, 24)}{fe.fmt(key, 24)}")
    p(f"  {'level ms (secondary)':<22}{_level(tab, sess):>+24.1f}{_level(tab, base):>+24.1f}{_level(tab, era):>+24.1f}")

    # The verdict: composition-matched, not the raw columns above, which move with the map mix.
    mt = matched(tab, sess, base)
    p(f"\n  matched to the session's mix: {mt['maps shared']} beatmaps paired within-map, the rest by "
      f"run length x BPM cell; {mt['keys']} keys, {100 * mt['coverage']:.0f}% of session circles covered")
    p(f"  {'':<22}{'session':>10}{'matched base':>14}{'difference [95% CI]':>28}")
    flags = []
    for key, label, worse in (("slope", "slope ms/tap", "abs"), ("growth", "spread growth ms/tap", "up"),
                              ("within", "within mean-abs SD ms", "up")):
        if key not in mt:
            continue
        sv, bv, d, (lo, hi) = mt[key]
        p(f"  {label:<22}{sv:>+10.3f}{bv:>+14.3f}{f'{d:+.3f} [{lo:+.3f},{hi:+.3f}]':>28}")
        if fs.maps < MIN_FLAG_MAPS or not (lo > 0 or hi < 0):
            continue
        bad = abs(sv) > abs(bv) if worse == "abs" else d > 0
        flags.append(f"{label}: session {sv:+.3f} vs matched baseline {bv:+.3f}, difference CI [{lo:+.3f},{hi:+.3f}] "
                     f"excludes zero -> {'REGRESSION' if bad else 'improved'}")
    p("")
    if fs.maps < MIN_FLAG_MAPS:
        p(f"not flagged: {fs.maps} beatmaps, and a bootstrap over fewer than {MIN_FLAG_MAPS} cannot carry a CI; "
          f"pool sessions with --sessions {pool + 1}")
    elif not flags:
        p("no change: every matched difference CI contains zero")
    for f in flags:
        p(f)


# ------------------------------------------------------------------------------------------
# Label sensitivity
# ------------------------------------------------------------------------------------------

# Cost overrides for streams.align (None: no alignment, see _relabel). The flat-cost entry
# reproduces the first alignment exactly: every gap 0.6, matches up to one interval.
VARIANTS = [
    ("current", {}),
    ("flat costs (first)", {"EXTRA_FAR": 0.6, "DROP_FAR": 0.6, "MAX_MATCH": 1.0}),
    ("HOLE 1.6", {"HOLE": 1.6}), ("HOLE 2.0", {"HOLE": 2.0}),
    ("BOUNCE 0.2", {"BOUNCE": 0.2}), ("BOUNCE 0.4", {"BOUNCE": 0.4}),
    ("FAR 4", {"EXTRA_FAR": 4.0, "DROP_FAR": 4.0}), ("FAR 16", {"EXTRA_FAR": 16.0, "DROP_FAR": 16.0}),
    ("MAX_MATCH 1.25", {"MAX_MATCH": 1.25}),
    ("no alignment: every circle", None), ("no alignment: drop Mehs", None),
]
ALIGNED = sum(1 for _, c in VARIANTS if c is not None)


def _relabel(store: dict, label: str, costs: dict | None) -> dict:
    replays = {}
    for name, v in store["replays"].items():
        runs = []
        for r in v["runs"]:
            if costs is not None:
                res = classify(r["times"], r["results"], r["errors"], r["presses"], r["snap"], r["beat"], costs)
                verdict, judge = res["verdict"], res["judge"]
            elif label.endswith("every circle"):
                verdict, judge = "ok", ["self"] * len(r["times"])
            else:
                verdict, judge = "ok", ["self" if x != "Meh" else "next" for x in r["results"]]
            runs.append(r | {"verdict": verdict, "judge": judge})
        replays[name] = v | {"runs": runs}
    return {"version": VERSION, "replays": replays}


def _step5_own(tab) -> list[float]:
    kept = tab["reason"] == 0
    out = []
    for lo, hi, limit in ((5, 8, 8), (9, 12, 12), (13, 10 ** 6, 17)):
        band = (tab["len"] >= lo) & (tab["len"] <= hi) & (tab["reason"] != 7) & (tab["reason"] != 6)
        rows = [(i, np.std(tab["err"][band & (tab["pos"] == i) & kept])) for i in range(1, limit + 1)
                if (band & (tab["pos"] == i)).sum() >= MIN_N and (band & (tab["pos"] == i) & kept).sum() >= MIN_N]
        out.append(np.polyfit([q[0] for q in rows], [q[1] for q in rows], 1)[0] if len(rows) >= 2 else float("nan"))
    return out


def _weekly_flags(tab, weeks: int, days: int = 30) -> dict[str, str]:
    """`latest`'s verdict with sessions pooled by ISO week, for the last ``weeks`` weeks."""
    kept = tab["reason"] == 0
    week = np.asarray([dt.datetime.fromtimestamp(x, EDT).strftime("%G-W%V") for x in tab["t"]])
    out = {}
    for w in sorted(set(week[kept]))[-weeks:]:
        inside = week == w
        sess = kept & inside
        t0 = tab["t"][inside].min()
        base = kept & ~inside & (tab["t"] < t0) & (tab["t"] >= t0 - days * 86400)
        maps = len(np.unique(tab["md5"][sess]))
        if maps < MIN_FLAG_MAPS:
            out[w] = "few maps"
            continue
        mt = matched(tab, sess, base, boot=500)
        flags = []
        for key, worse in (("slope", "abs"), ("growth", "up"), ("within", "up")):
            if key in mt:
                sv, bv, d, (lo, hi) = mt[key]
                if lo > 0 or hi < 0:
                    bad = abs(sv) > abs(bv) if worse == "abs" else d > 0
                    flags.append(f"{key}{'+' if bad else '-'}")
        out[w] = " ".join(flags) or "-"
    return out


def sensitivity(out_path: str | None, weeks: int = 30) -> None:
    store = update()
    results = []
    for label, costs in VARIANTS:
        tab = table(_relabel(store, label, costs))
        kept = tab["reason"] == 0
        era = tab["era"] == CURRENT_ERA
        r = {"label": label, "kept": kept.mean(),
             "all": Fit(tab, kept, boot=500), "analog": Fit(tab, kept & era, boot=500),
             "months": {mo: Fit(tab, kept & (tab["month"] == mo), boot=500)
                        for mo in sorted(set(tab["month"])) if (kept & (tab["month"] == mo)).sum() >= 500},
             "step5": _step5_own(tab), "weeks": _weekly_flags(tab, weeks)}
        results.append(r)
        print(f"  {label}: kept {r['kept']:.1%}", file=sys.stderr, flush=True)

    lines = []
    p = lines.append
    p("drift numbers under other stream labellings. The first row is what `report` prints; the next "
      f"{ALIGNED - 1} vary the alignment's costs\n(streams.py), the last two use no alignment at all. "
      "Fits bootstrap 500 draws here, so CIs differ slightly from `report`.\n")
    p(f"  {'labelling':<28}{'kept':>7}{'slope all':>24}{'growth all':>24}{'slope analog':>24}"
      f"{'growth analog':>24}{'within':>8}   step 5 own-tap SD slope, runs 5-8/9-12/13+")
    for r in results:
        a, g = r["all"], r["analog"]
        p(f"  {r['label']:<28}{r['kept']:>7.1%}{a.fmt('slope', 24)}{a.fmt('growth', 24)}{g.fmt('slope', 24)}"
          f"{g.fmt('growth', 24)}{a.within:>8.1f}   " + "/".join(f"{x:+.2f}" for x in r["step5"]))

    sig = lambda f, k: int(f.ci[k][0] > 0) - int(f.ci[k][1] < 0)
    months = sorted(set().union(*(r["months"] for r in results)))
    for key, title in (("slope", "slope ms/tap"), ("growth", "spread growth ms/tap")):
        p(f"\nmonthly {title}; * = CI excludes zero. SIGN / SIG: the aligned variants disagree on the "
          f"sign / on whether it is significant")
        p(f"  {'month':<9}" + "".join(f"{r['label'][:10]:>11}" for r in results))
        for mo in months:
            fits = [r["months"].get(mo) for r in results]
            cells = "".join((f"{getattr(f, key):+.3f}{'*' if sig(f, key) else ' '}".rjust(11) if f else f"{'--':>11}")
                            for f in fits)
            al = [f for f in fits[:ALIGNED] if f]
            tag = ("  SIGN" if len({getattr(f, key) > 0 for f in al}) > 1 else "") + \
                  ("  SIG" if len({sig(f, key) for f in al}) > 1 else "")
            p(f"  {mo:<9}{cells}{tag}")

    p(f"\n`latest`'s verdict pooled by ISO week (30 days before as the baseline); + is a regression, "
      f"- an improvement, - alone no change")
    p(f"  {'week':<10}" + "".join(f"{r['label'][:10]:>11}" for r in results))
    differ = [0, 0]
    for w in sorted(results[0]["weeks"]):
        v = [r["weeks"].get(w, "--") for r in results]
        differ[0] += len(set(v[:ALIGNED])) > 1
        differ[1] += len(set(v)) > 1
        p(f"  {w:<10}" + "".join(f"{x:>11}" for x in v))
    p(f"  weeks whose verdict differs: {differ[0]} among the aligned variants, {differ[1]} with the "
      f"label-free ones, of {len(results[0]['weeks'])}")

    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w").write(text)
    print(text)


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "cache":
        cache()
    elif cmd == "sensitivity":
        sensitivity(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/drift-sensitivity.txt")
    elif cmd == "latest":
        arg = lambda k, d: int(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
        latest(days=arg("--days", 30), pool=arg("--sessions", 1))
    else:
        report(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/drift.txt")
