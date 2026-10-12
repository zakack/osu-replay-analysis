"""Build a self-contained replay viewer from a play and a reference field.

Takes one of your replays plus other people's replays of the same beatmap, runs each
through `ora scene`, and folds the results into `template.html` as a single inlined
payload. The output is one file with no fetches in it, because an artifact's content
policy blocks them and a viewer that cannot load its own data is not a viewer.

    python3 tools/viewer/build.py --replay <path.osr> --out build/viewer/lap.html \
        --reference build/reference-nm/ref-1.osr build/reference-nm/ref-2.osr

With `--auto N` the reference field is chosen for you: the N highest-pp no-mod lazer
scores on the same beatmap that the corpus already has.

With `--mine N` the field is *you*, N of your own other completed runs on the same beatmap,
sampled evenly across the span you have been playing it. The comparison stops being "a
better player's line" and becomes your own spread: the same objects, the same hand, on
different days. Each ghost is labelled by date and carries its own timing spread, so the
roster reads as a history rather than a leaderboard.

Reference players contribute frames and per-object errors only, never geometry. They are
on the same beatmap, so the objects are already in the document once, and object index is
the join key — the scene pass emits objects in start-time order for every replay of a
beatmap, so index *i* is the same object for everybody.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
ORA = os.path.join(ROOT, "src", "Cli", "bin", "Release", "net10.0", "ora.dll")


def scene(replay: str, destination: str) -> dict:
    """Run the extraction shim over one replay and read back its scene document.

    The command exits non-zero when its own path-walk cross-check diverges, and that is
    worth propagating rather than swallowing: a viewer drawing a polyline the shim could
    not reproduce positions on is drawing something nobody has checked.
    """
    result = subprocess.run(
        ["dotnet", ORA, "scene", replay, destination],
        capture_output=True, text=True, cwd=ROOT)

    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"scene failed on {os.path.basename(replay)}")

    with open(destination) as handle:
        return json.load(handle)


def spread(document: dict) -> float:
    """Timing spread over the clicks that have one.

    Spinners and misses carry no hit error — a spinner never had a click, a miss never
    received one — and counting either as a zero would report a steadier player than the
    replay shows.
    """
    errors = [o["error"] for o in document["objects"] if o.get("error") is not None]
    return round(statistics.pstdev(errors), 2) if len(errors) > 1 else 0.0


def corpus_lookup(md5: str, limit: int) -> list[str]:
    """The best no-mod lazer scores the reference corpus holds for one beatmap."""
    reference = json.load(open(os.path.join(ROOT, "build", "corpus-nm.json")))
    manifest = json.load(open(os.path.join(ROOT, "build", "reference", "manifest.json")))
    by_file = {v["file"]: v for v in manifest.values()}

    rows = []
    for record in reference:
        if record["BeatmapMd5"] != md5:
            continue
        entry = by_file.get(os.path.basename(record["Path"]))
        if entry and not entry["mods"]:
            rows.append((entry.get("pp", 0), record["Path"], entry["username"]))

    rows.sort(reverse=True)
    return [path for _, path, _ in rows[:limit]]


def own_runs(md5: str, limit: int, exclude: str) -> list[str]:
    """N of my own other completed runs on one beatmap, spread across their whole span.

    Evenly sampled rather than best-first or most-recent, because the question an all-me
    field answers is how much the same hand varies, and taking the top N by anything
    answers a narrower one. The corpus is in no particular order, so sort before sampling.
    """
    corpus = json.load(open(os.path.join(ROOT, "build", "corpus.json")))
    paths = sorted(record["Path"] for record in corpus
                   if record["BeatmapMd5"] == md5 and record.get("Paired")
                   and os.path.abspath(record["Path"]) != os.path.abspath(exclude))

    if len(paths) <= limit:
        return paths

    # Endpoints included, so the span is actually shown rather than its middle.
    step = (len(paths) - 1) / (limit - 1) if limit > 1 else 0
    return [paths[round(i * step)] for i in range(limit)]


def when(path: str) -> str:
    """The run's date, from the name lazer exported it under: `... (2026-01-09_11-55).osr`.

    This is the ghost label for an all-me field, where every name would otherwise be the
    same word. Falls back to the bare file name, which is wrong-looking rather than silently
    identical -- two ghosts sharing a label would make the roster unreadable.
    """
    stamp = re.search(r"\((\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\)", os.path.basename(path))
    return f"{stamp.group(1)} {stamp.group(2)}:{stamp.group(3)}" if stamp \
        else os.path.splitext(os.path.basename(path))[0][-24:]


def username(path: str) -> str:
    """Whoever set the score, from the fetch manifest, falling back to the file name."""
    try:
        manifest = json.load(open(os.path.join(ROOT, "build", "reference", "manifest.json")))
        by_file = {v["file"]: v for v in manifest.values()}
        entry = by_file.get(os.path.basename(path))
        if entry:
            return entry["username"]
    except FileNotFoundError:
        pass

    name = os.path.basename(path)
    return name.split(" playing ")[0] if " playing " in name else os.path.splitext(name)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", required=True, help="the play the viewer is about")
    parser.add_argument("--out", required=True, help="destination HTML file")
    parser.add_argument("--reference", nargs="*", default=[], help="other players' replays")
    parser.add_argument("--auto", type=int, default=0,
                        help="instead, take the N best no-mod lazer scores from the corpus")
    parser.add_argument("--mine", type=int, default=0,
                        help="instead, take N of your own other runs on the same beatmap")
    args = parser.parse_args()

    if args.auto and args.mine:
        raise SystemExit("--auto and --mine are different fields; pick one")

    if not os.path.exists(ORA):
        raise SystemExit(f"no release build at {ORA}; run: dotnet build -c Release")

    with tempfile.TemporaryDirectory() as work:
        subject = scene(args.replay, os.path.join(work, "subject.json"))
        print(f"{len(subject['objects'])} objects, {subject['frameCount']} frames, "
              f"spread {spread(subject)}ms")

        paths, field = args.reference, "reference"
        if args.auto:
            paths = corpus_lookup(subject["beatmapMd5"], args.auto)
            print(f"reference field: {len(paths)} no-mod lazer scores from the corpus")
        elif args.mine:
            paths = own_runs(subject["beatmapMd5"], args.mine, args.replay)
            field = "your runs"
            print(f"own field: {len(paths)} of your other runs on this beatmap")

        ghosts = []
        for n, path in enumerate(paths):
            other = scene(path, os.path.join(work, f"ghost{n}.json"))

            # Same beatmap or the join key means nothing. Checked rather than assumed,
            # because a mismatch would silently attribute one map's errors to another's.
            if other["beatmapMd5"] != subject["beatmapMd5"]:
                print(f"  skipped {os.path.basename(path)}: different beatmap")
                continue

            # Same speed, or the overlay compares two different demands. The reference path
            # filters to no-mod before it gets here; an all-me field cannot, because the
            # corpus holds every completed run and the player does use rate mods.
            if other["rate"] != subject["rate"] or other["mods"] != subject["mods"]:
                print(f"  skipped {when(path)}: {other['mods'] or 'no mod'} at "
                      f"{other['rate']}x, not {subject['mods'] or 'no mod'} at "
                      f"{subject['rate']}x")
                continue

            # Where each of their presses landed, not just how they moved. The viewer
            # leaves these on the field the same way it leaves the player's, which is what
            # makes a stream's evenness checkable: a row of marks either steps along the
            # path at one pitch or it does not.
            marks = [[o["i"], o["hitTime"], o["hx"], o["hy"]]
                     for o in other["objects"]
                     if o.get("hx") is not None and o.get("hitTime") is not None]

            ghosts.append({
                "name": when(path) if args.mine else username(path),
                "sd": spread(other),
                "frames": other["frames"],
                "marks": marks,
                "errors": {str(o["i"]): o["error"]
                           for o in other["objects"] if o.get("error") is not None},
            })
            print(f"  {ghosts[-1]['name']:<18} spread {ghosts[-1]['sd']}ms")

    payload = json.dumps(
        {"scene": subject, "ghosts": ghosts, "field": field,
         "you": {"name": "you", "sd": spread(subject)}},
        separators=(",", ":"))

    with open(TEMPLATE) as handle:
        html = handle.read()

    if "/*__PAYLOAD__*/" not in html:
        raise SystemExit("template has no payload slot")

    # A closing tag inside the JSON would end the script element early and truncate the
    # document at whatever object happened to contain it.
    html = html.replace("/*__PAYLOAD__*/", payload.replace("</", r"<\/"), 1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        handle.write(html)

    print(f"\nwritten: {args.out}  ({len(html) / 1024 / 1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
