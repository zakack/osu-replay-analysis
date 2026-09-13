"""Step 6: is the spread growth yours, or is it how streams work?

Compares the player against no-mod lazer replays from the same maps, on all-circle
sixteenth runs. The quantity is the ratio of late-run spread to entry spread, because
a ratio survives everything that makes absolute milliseconds incomparable between
people: recorder frame rate, clock rate, and how hard the map is.

Matching is on conditions, not just on maps. Every reference replay here was fetched
from a no-mod leaderboard, so rate is 1 for both sides and hit errors are in the same
units. The player's own data is restricted to the same maps, so the comparison is not
their whole library against someone's single good run.
"""
import csv, collections, json, statistics, sys

ENTRY = {0, 1}
LATE_FROM = 5
MIN_SAMPLES = 25   # swept from 15 to 100; the result does not move, see the commit
PLAYER = "zaksynack playing"


def runs(path):
    """(replay, time) -> position, for clicks inside all-circle sixteenth runs long
    enough to have both an entry and a late section."""
    out = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["tolerance"] != "0.02" or r["snap"] != "0.25" or r["interiorCircles"] != "1":
                continue
            if r["clickIndex"] == "-1" or int(r["runSize"]) < LATE_FROM + 2:
                continue
            out[(r["replay"], round(float(r["time"]), 3))] = int(r["runIndex"])
    return out


def errors(geometry, positions, identify, maps=None):
    """player -> (entry errors, late errors), and the set of maps each played."""
    cells = collections.defaultdict(lambda: ([], []))
    played = collections.defaultdict(set)

    with open(geometry) as f:
        for r in csv.DictReader(f):
            who = identify(r["replay"])
            if who is None or not r["hitError"]:
                continue
            key = (r["replay"], round(float(r["startTime"]), 3))
            if key not in positions:
                continue

            index = positions[key]
            error = float(r["hitError"])

            if index in ENTRY:
                cells[who][0].append(error)
            elif index >= LATE_FROM:
                cells[who][1].append(error)

            played[who].add(r["replay"])

    return cells, played


def main():
    manifest = json.load(open("build/reference/manifest.json"))
    by_file = {v["file"]: v for v in manifest.values()}

    # Only replays fetched from a no-mod board, and only maps the player actually has.
    mine_md5 = {r["Path"].split("/")[-1]: r["BeatmapMd5"] for r in json.load(open("build/corpus.json"))}
    reference_maps = {v["beatmap_md5"] for v in by_file.values() if v.get("filter") == "NM"}

    def reference_player(name):
        entry = by_file.get(name)
        if entry is None or entry.get("filter") != "NM" or entry["mods"]:
            return None
        return entry["username"]

    def me(name):
        if not name.startswith(PLAYER):
            return None
        return "zaksynack" if mine_md5.get(name) in reference_maps else None

    ref_positions = runs("build/rhythm-reference-nm.csv")
    ref_cells, ref_played = errors("build/geometry-reference-nm.csv", ref_positions, reference_player)

    my_positions = runs("build/rhythm.csv")
    my_cells, my_played = errors("build/geometry.csv", my_positions, me)

    rows = []
    for who, (entry, late) in list(ref_cells.items()) + list(my_cells.items()):
        if len(entry) < MIN_SAMPLES or len(late) < MIN_SAMPLES:
            continue
        e, l = statistics.pstdev(entry), statistics.pstdev(late)
        rows.append((who, len(entry), len(late), e, l, l / e))

    rows.sort(key=lambda r: r[5])
    ratios = [r[5] for r in rows if r[0] != "zaksynack"]

    print(f"  players with at least {MIN_SAMPLES} samples each side: {len(rows)}")
    print(f"  {'player':<18}{'n entry':>9}{'n late':>8}{'entry sd':>10}{'late sd':>9}{'ratio':>8}")
    for who, ne, nl, e, l, ratio in rows:
        mark = "   <- you" if who == "zaksynack" else ""
        print(f"  {who[:17]:<18}{ne:>9}{nl:>8}{e:>10.2f}{l:>9.2f}{ratio:>8.2f}{mark}")

    if ratios:
        print(f"\n  reference ratio: median {statistics.median(ratios):.2f}, "
              f"mean {statistics.mean(ratios):.2f}, n={len(ratios)}")
        mine = next((r for r in rows if r[0] == "zaksynack"), None)
        if mine:
            below = sum(1 for x in ratios if x < mine[5])
            print(f"  yours {mine[5]:.2f} — {below} of {len(ratios)} reference players degrade less than you")


if __name__ == "__main__":
    main()
