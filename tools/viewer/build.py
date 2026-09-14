"""Build a self-contained replay viewer from a play and a reference field.

Takes one of your replays plus other people's replays of the same beatmap, runs each
through `ora scene`, and folds the results into `template.html` as a single inlined
payload. The output is one file with no fetches in it, because an artifact's content
policy blocks them and a viewer that cannot load its own data is not a viewer.

    python3 tools/viewer/build.py --replay <path.osr> --out build/viewer/lap.html \
        --reference build/reference-nm/ref-1.osr build/reference-nm/ref-2.osr

With `--auto N` the reference field is chosen for you: the N highest-pp no-mod lazer
scores on the same beatmap that the corpus already has.

Reference players contribute frames and per-object errors only, never geometry. They are
on the same beatmap, so the objects are already in the document once, and object index is
the join key — the scene pass emits objects in start-time order for every replay of a
beatmap, so index *i* is the same object for everybody.
"""
from __future__ import annotations

import argparse
import json
import os
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
    args = parser.parse_args()

    if not os.path.exists(ORA):
        raise SystemExit(f"no release build at {ORA}; run: dotnet build -c Release")

    with tempfile.TemporaryDirectory() as work:
        subject = scene(args.replay, os.path.join(work, "subject.json"))
        print(f"{len(subject['objects'])} objects, {subject['frameCount']} frames, "
              f"spread {spread(subject)}ms")

        paths = args.reference
        if args.auto:
            paths = corpus_lookup(subject["beatmapMd5"], args.auto)
            print(f"reference field: {len(paths)} no-mod lazer scores from the corpus")

        ghosts = []
        for n, path in enumerate(paths):
            other = scene(path, os.path.join(work, f"ghost{n}.json"))

            # Same beatmap or the join key means nothing. Checked rather than assumed,
            # because a mismatch would silently attribute one map's errors to another's.
            if other["beatmapMd5"] != subject["beatmapMd5"]:
                print(f"  skipped {os.path.basename(path)}: different beatmap")
                continue

            ghosts.append({
                "name": username(path),
                "sd": spread(other),
                "frames": other["frames"],
                "errors": {str(o["i"]): o["error"]
                           for o in other["objects"] if o.get("error") is not None},
            })
            print(f"  {ghosts[-1]['name']:<18} spread {ghosts[-1]['sd']}ms")

    payload = json.dumps(
        {"scene": subject, "ghosts": ghosts, "you": {"name": "you", "sd": spread(subject)}},
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
