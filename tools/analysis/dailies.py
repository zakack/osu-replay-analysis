"""The player's daily challenges: which days they played, their first attempt, and whether the
map was new to them -- the table a daily evaluation of the risk map reads.

The daily challenge is the one map stream the player does not choose, which is what makes it
an evaluation set: the risk model's training plays are full of retries of maps the player
grinds because they miss on them, and every training window over-predicts by 5-10%
(riskmap.py). A map the player did not pick removes that selection.

Joins `build/daily-maps.json` (tools/reference/dailymaps.py: each room's map MD5, window and
declared mods) to the player's local replays (build/corpus.json, MD5 per replay, and the
local wall-clock stamp lazer puts in every exported file name). A play belongs to a day
when it is that day's map and it finished inside the room's window. File names are local
time and rooms are UTC, so stamps are converted with this machine's timezone; a minute's
slack either side covers the stamp's resolution.

Per day it records the plays in the window, the first of them, and whether any replay of
that map predates the room opening. Two limits, both from what lazer saves:

- **First attempt means first completed attempt.** Lazer keeps a replay for every completed
  run and none for a failed one (unless saved by hand), so a fail before the first pass is
  invisible. NF makes every attempt complete, which is why the evaluation plan plays the
  daily on NF.
- **"Seen before" only knows plays.** A map can sit in the library unplayed; the player's
  own check -- did song select have to download it -- is the stronger test for that.

    python3 tools/analysis/dailies.py                  # summary, writes build/dailies.json
    python3 tools/analysis/dailies.py --weeks          # the maps by week
    python3 tools/analysis/dailies.py --groups > g.tsv # md5 TAB collection name, for
                                                       # `ora collection --groups`
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import where  # noqa: E402

MAPS = "build/daily-maps.json"
OUT = "build/dailies.json"
PLAYER = "zaksynack"
STAMP = re.compile(r"\((\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\)\.osr$")
SLACK = dt.timedelta(minutes=1)
# What the risk model was fitted on: the map's own rate and difficulty.
KEPT = frozenset({"HD", "NF", "MR", "SD", "PF"})


def utc(stamp: re.Match) -> dt.datetime:
    local = dt.datetime.fromisoformat(f"{stamp.group(1)}T{stamp.group(2)}:{stamp.group(3)}")
    return local.astimezone().astimezone(dt.timezone.utc)


def week_of(start: dt.datetime) -> tuple[str, str]:
    """ISO week key, and a human label for it: 'Oct 5-11, 2026'."""
    monday = (start - dt.timedelta(days=start.weekday())).date()
    sunday = monday + dt.timedelta(days=6)
    year, week, _ = monday.isocalendar()
    label = (f"{monday:%b} {monday.day}-{sunday.day}, {sunday.year}" if monday.month == sunday.month
             else f"{monday:%b} {monday.day} - {sunday:%b} {sunday.day}, {sunday.year}")
    return f"{year}-W{week:02d}", label


def build() -> list[dict]:
    rooms = json.load(open(MAPS))
    mods = where.mods_by_replay()
    plays = collections.defaultdict(list)
    for e in json.load(open("build/corpus.json")):
        name = os.path.basename(e["Path"])
        if not name.startswith(PLAYER + " playing") or not e.get("BeatmapMd5"):
            continue
        stamp = STAMP.search(name)
        if stamp:
            plays[e["BeatmapMd5"]].append((utc(stamp), name))
    for v in plays.values():
        v.sort()

    days = []
    for r in rooms:
        if not r.get("md5") or not r.get("starts_at"):
            continue
        start = dt.datetime.fromisoformat(r["starts_at"])
        end = dt.datetime.fromisoformat(r["ends_at"]) if r.get("ends_at") else start + dt.timedelta(days=1)
        mine = plays.get(r["md5"], [])
        inside = [(t, n) for t, n in mine if start - SLACK <= t <= end + SLACK]
        before = [n for t, n in mine if t < start - SLACK]
        week, label = week_of(start)
        day = {
            "date": start.date().isoformat(), "week": week, "week_label": label,
            "room_id": r["room_id"], "md5": r["md5"], "beatmap_id": r["beatmap_id"],
            "map": f"{r['artist']} - {r['title']} [{r['version']}]", "creator": r.get("creator"),
            "stars": r.get("stars"), "length": r.get("length"), "bpm": r.get("bpm"),
            "required_mods": r.get("required_mods") or [],
            "plays": [{"file": n, "utc": t.isoformat(),
                       "mods": sorted(mods.get(n, [])) if n in mods else None} for t, n in inside],
            "seen_before": len(before),
        }
        if inside:
            first = day["plays"][0]
            day["first"] = first["file"]
            # Usable as an evaluation day: the first completed attempt was at the map's own
            # rate and difficulty, with known mods, on a map not played before the day.
            day["usable"] = (first["mods"] is not None and set(first["mods"]) <= KEPT
                             and not day["seen_before"])
        days.append(day)
    days.sort(key=lambda d: d["date"])
    return days


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weeks", action="store_true", help="print the maps by week")
    parser.add_argument("--groups", action="store_true",
                        help="print md5 TAB collection name: every daily, and one per week")
    args = parser.parse_args()

    days = build()
    json.dump(days, open(OUT, "w"), indent=1)

    if args.groups:
        for d in days:
            print(f"{d['md5']}\tDaily challenges")
        for d in days:
            print(f"{d['md5']}\tDaily {d['week_label']}")
        return 0

    if args.weeks:
        current = None
        for d in days:
            if d["week"] != current:
                current = d["week"]
                print(f"\n{d['week']}  {d['week_label']}")
            mark = ("played" if d["plays"] else "      ") + (" (seen before)" if d["seen_before"] else "")
            req = f" +{''.join(d['required_mods'])}" if d["required_mods"] else ""
            print(f"  {d['date']}  {d['stars']:4.2f}*  {d['map'][:70]}{req}  [{d['creator']}]  {mark}")
        return 0

    played = [d for d in days if d["plays"]]
    usable = [d for d in played if d.get("usable")]
    print(f"{len(days)} daily rooms, {days[0]['date']} to {days[-1]['date']}")
    print(f"  played {len(played)} of them; {sum(len(d['plays']) for d in played)} completed plays in all")
    print(f"  seen before the day: {sum(1 for d in played if d['seen_before'])} of the played days")
    def label(m):
        return "unknown" if m is None else " ".join(m) or "nomod"
    firsts = collections.Counter(label(d["plays"][0]["mods"]) for d in played)
    print("  first-attempt mods: " + ", ".join(f"{k} {v}" for k, v in firsts.most_common()))
    print(f"  usable for evaluation (new map, first attempt at its own rate and difficulty): "
          f"{len(usable)}")
    required = collections.Counter(" ".join(d["required_mods"]) or "none" for d in days)
    print("  declared required mods: " + ", ".join(f"{k} {v}" for k, v in required.most_common()))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
