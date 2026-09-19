"""Pull the player's own top plays as a separate corpus.

The local corpus is everything lazer ever exported: every failed run, every warm-up, every
map played once. That makes it the right place to find where error concentrates and the
wrong place to ask what the player does when it goes well, because the good plays are a few
per cent of it and are not labelled.

This is that label, and it comes from outside the replay: pp rank within the player's own
top list. Same player, same hardware, same tablet, same era — everything the top-50
reference corpus cannot hold constant. Where that corpus answers "how do people who do not
fail this pattern move through it", this one answers "how did *you* move through it the day
it worked", which is the comparison with no between-subject confound in it at all.

Kept separate from build/corpus.json for the same reason the reference set is: these are a
selected subset, and folding them in would silently bias every corpus-wide number toward
good days.

Classic scores are kept, unlike the reference fetch, where they are dropped because a
stranger's replay that cannot be validated against its own header is worth nothing. Here the
list is the player's own history and dropping any of it would cut the corpus at an arbitrary
date. Recorded and flagged rather than skipped; filter downstream on `classic`.

Note that the flag does not mean "imported from stable". Every stable import carries CL, but
CL is a legitimate ranked mod in its own right and a lazer player can simply select it, so
the implication runs one way only. Read the flag as "scored under Classic rules", which is
the thing that actually matters for the tail judgement, and take the client from
`build_id` and the replay's own `client_version`.

Usage: OSU_API_CLIENT/OSU_API_KEY in the environment, then

    python3 tools/reference/best.py <user-id> [count]
"""
import hashlib, json, os, sys

from fetch import call, token

OUT = "build/best"
MANIFEST = f"{OUT}/manifest.json"
PAGE = 50


def listing(bearer, user, count):
    """The top list, paged. A short page means the list ran out, which is the only clean
    signal the API gives that the player has fewer scores than were asked for."""
    scores = []

    while len(scores) < count:
        want = min(PAGE, count - len(scores))
        page = call(f"https://osu.ppy.sh/api/v2/users/{user}/scores/best", bearer,
                    {"mode": "osu", "limit": want, "offset": len(scores)})

        if not isinstance(page, list):
            print(f"  listing failed at offset {len(scores)}", flush=True)
            break

        scores.extend(page)

        if len(page) < want:
            break

    return scores[:count]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    user = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    os.makedirs(OUT, exist_ok=True)

    manifest = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
    bearer = token()
    scores = listing(bearer, user, count)
    print(f"top {len(scores)} listed for user {user}", flush=True)

    fetched = skipped = missing = 0

    for position, score in enumerate(scores, 1):
        key = str(score["id"])
        beatmap = score.get("beatmap") or {}
        mods = [m.get("acronym") for m in score.get("mods", []) if isinstance(m, dict)]

        record = {
            "file": f"best-{key}.osr",
            "position": position,
            "beatmap_id": beatmap.get("id"),
            "beatmap_md5": beatmap.get("checksum"),
            "version": beatmap.get("version"),
            "title": (score.get("beatmapset") or {}).get("title"),
            "mods": mods,
            "classic": "CL" in mods,
            "rank": score.get("rank"),
            "pp": score.get("pp"),
            "weighted_pp": (score.get("weight") or {}).get("pp"),
            "accuracy": score.get("accuracy"),
            "max_combo": score.get("max_combo"),
            "statistics": score.get("statistics"),
            "build_id": score.get("build_id"),
            "legacy_score_id": score.get("legacy_score_id"),
            "ended_at": score.get("ended_at"),
        }

        # Position and pp move as the player improves, so refresh them on a re-run even
        # when the replay itself is already on disk.
        if key in manifest and os.path.exists(f"{OUT}/{record['file']}"):
            record["md5"] = manifest[key].get("md5")
            manifest[key] = record
            skipped += 1
            continue

        if not (score.get("has_replay") or score.get("replay")):
            print(f"  {position:3d}. no replay stored: {beatmap.get('id')} [{beatmap.get('version')}]", flush=True)
            missing += 1
            continue

        data = call(f"https://osu.ppy.sh/api/v2/scores/{key}/download", bearer, raw=True)

        if not data:
            print(f"  {position:3d}. download failed: score {key}", flush=True)
            missing += 1
            continue

        with open(f"{OUT}/{record['file']}", "wb") as f:
            f.write(data)

        # The same play is probably already in the export corpus. Lazer's exporter and the
        # API serve the same stored replay, so a content hash says which, and whether the
        # two paths agree byte for byte.
        record["md5"] = hashlib.md5(data).hexdigest()
        manifest[key] = record
        fetched += 1

        with open(MANIFEST, "w") as f:
            json.dump(manifest, f, indent=1)

    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=1)

    print(f"\nfetched {fetched}, already had {skipped}, no replay available {missing}")
    print(f"manifest: {MANIFEST} ({len(manifest)} replays)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
