"""Is the click synchronised with the hand that aimed it?

Aim error says where the cursor was when the button went down. It cannot say whether the
button went down when the cursor got there — and those come apart. A player can arrive on
the circle on time and fire late, which reads as a timing fault in the hit error and as a
clean one in the aim error, and neither column names it.

The quantity here is the gap between the cursor's closest approach to the object and the
press that judged it:

    lag = hitTime - argmin(distance from the object centre)

Positive means the hand was there and the finger had not fired yet. Negative means the
press led the hand in, which on a fast jump is normal — you commit before you arrive.

Two measurement notes that decide whether any of this is real:

* **The press time is exact and the arrival time is not.** A button change forces a replay
  frame, so hitTime is recorded rather than interpolated; cursor position is sampled at
  60Hz, so the closest frame is quantised to about 17ms. Distance near an approach is
  locally quadratic, so the three frames around the minimum are fitted and the turning
  point read off the parabola. Without that the whole measurement lives inside one frame
  of noise and any difference in medians is an artefact.
* **Use the median and the tail, not the mean.** The distribution is heavily skewed —
  every pattern where the cursor parks on the object contributes a large positive lag that
  says nothing about synchronisation.

Run against one beatmap so the patterns are identical for everyone compared:

    PYTHONPATH=tools python3 tools/analysis/clicksync.py --beatmap e322eb229d52
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bins import cells, load  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORA = os.path.join(ROOT, "src", "Cli", "bin", "Release", "net10.0", "ora.dll")

# Spacing and snap bands that count as a jump and as a stream. Taken as cell labels rather
# than as thresholds so that the split here and the split in the findings table cannot
# drift apart.
JUMP = {("4.5:6", "0.5"), ("6:8", "0.5"), ("8:10", "0.5"), ("10:inf", "0.5")}
STREAM = {("0:1", "0.25"), ("1:1.5", "0.25")}

LOOKBACK_MS = 320


def arrival(frames: list, obj: dict) -> tuple[float, float] | None:
    """When the cursor was closest to the object, and how close, with sub-frame timing."""
    points = []
    for k in range(0, len(frames), 4):
        t = frames[k]
        if t < obj["t"] - LOOKBACK_MS:
            continue
        if t > obj["hitTime"] + 60:
            break
        points.append((t, math.hypot(frames[k + 1] - obj["x"], frames[k + 2] - obj["y"])))

    if len(points) < 3:
        return None

    i = min(range(len(points)), key=lambda j: points[j][1])
    when = points[i][0]

    if 0 < i < len(points) - 1:
        (ta, da), (tb, db), (tc, dc) = points[i - 1], points[i], points[i + 1]
        denominator = da - 2 * db + dc
        if abs(denominator) > 1e-9:
            shift = 0.5 * (da - dc) / denominator
            if -1.5 < shift < 1.5:
                when = tb + shift * ((tc - ta) / 2)

    return when, points[i][1]


def scene(replay: str, destination: str) -> dict | None:
    result = subprocess.run(["dotnet", ORA, "scene", replay, destination],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        return None
    with open(destination) as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beatmap", required=True)
    parser.add_argument("--reference", type=int, default=10)
    parser.add_argument("--player", default="zaksynack")
    args = parser.parse_args()

    corpus = json.load(open(os.path.join(ROOT, "build", "corpus.json")))
    reference = json.load(open(os.path.join(ROOT, "build", "corpus-nm.json")))
    manifest = json.load(open(os.path.join(ROOT, "build", "reference", "manifest.json")))
    by_file = {v["file"]: v for v in manifest.values()}

    md5 = next(r["BeatmapMd5"] for r in corpus if r["BeatmapMd5"].startswith(args.beatmap))
    mine = [r["Path"] for r in corpus if r["BeatmapMd5"] == md5 and r.get("Paired")]
    others = [p for _, p in sorted(
        ((by_file[os.path.basename(r["Path"])].get("pp", 0), r["Path"]) for r in reference
         if r["BeatmapMd5"] == md5 and not by_file[os.path.basename(r["Path"])]["mods"]),
        reverse=True)[:args.reference]]

    # Which band each object is in, from the binning package, so this analysis and the
    # findings table agree about what a jump is.
    band = {}
    for o in load.observations(load.PLAYER, maps={md5}, players={args.player}):
        key = cells.key(o, "angle-spacing-snap")
        if key and o.target == "circle":
            pair = (key[1], key[2])
            band[round(o.start_time, 3)] = ("jump" if pair in JUMP
                                            else "stream" if pair in STREAM else None)

    collected = collections.defaultdict(list)

    with tempfile.TemporaryDirectory() as work:
        for n, (path, is_mine) in enumerate([(p, True) for p in mine] +
                                            [(p, False) for p in others]):
            document = scene(path, os.path.join(work, f"s{n}.json"))
            if document is None:
                continue

            who = "you" if is_mine else "reference"
            for obj in document["objects"]:
                if obj["type"] != "circle" or obj.get("hitTime") is None:
                    continue
                kind = band.get(round(obj["t"], 3))
                if kind is None:
                    continue
                found = arrival(document["frames"], obj)
                if found is None:
                    continue
                collected[(who, kind)].append((obj["hitTime"] - found[0], found[1],
                                               obj.get("error")))

    print(f"beatmap {md5[:12]}  ·  {len(mine)} of yours, {len(others)} reference\n")
    print(f"{'group':<11}{'pattern':<9}{'n':>6}{'lag p50':>9}{'lag p95':>9}{'lag sd':>8}"
          f"{'>50ms':>8}{'>100ms':>8}{'closest':>9}{'hit err':>9}")

    for who in ("you", "reference"):
        for kind in ("stream", "jump"):
            rows = collected[(who, kind)]
            if len(rows) < 30:
                continue
            lags = sorted(r[0] for r in rows)
            near = [r[1] for r in rows]
            errors = [r[2] for r in rows if r[2] is not None]
            quantiles = statistics.quantiles(lags, n=20)
            print(f"{who:<11}{kind:<9}{len(lags):>6}{statistics.median(lags):>+9.1f}"
                  f"{quantiles[18]:>+9.1f}{statistics.pstdev(lags):>8.1f}"
                  f"{sum(1 for x in lags if x > 50) / len(lags) * 100:>7.1f}%"
                  f"{sum(1 for x in lags if x > 100) / len(lags) * 100:>7.1f}%"
                  f"{statistics.fmean(near):>9.1f}"
                  f"{statistics.fmean(errors):>+9.1f}")

    print("\nlag = press time minus closest approach, in ms. Positive is the hand arriving "
          "first.\nclosest = distance in osu!pixels at that moment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
