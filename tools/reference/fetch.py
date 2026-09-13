"""Pull top-50 replays for the maps the local corpus has most data on.

The reference lap. For any pattern the player struggles with, this is how people who
do not struggle with it move through the same notes — on the same map, same objects,
same geometry, so the comparison needs no normalisation at all.

Courtesies, in order of how much they matter:

  * One request a second, and back off on 429. ppy asks only that nobody goes crazy.
  * Resume from the manifest. A re-run costs nothing and re-fetches nothing.
  * Files land under build/, which is gitignored. These are other people's replays;
    they are public through an API anyone can use, and that is not the same as ours
    to redistribute.

Lazer only, by default. Every stable-format score on a ranked leaderboard carries the
Classic mod, and the two are the same fact: an imported stable play. Those replays
cannot be validated against their own headers without porting stable's counting model,
because stable counts a completed slider as one 300 while lazer counts the head
separately and has no legacy field for large ticks at all. Rather than reconcile a
client this project does not target, skip them — and because the filter runs on the
listing, the skipped ones cost nothing to download.

The lazer share of a leaderboard rises sharply with map age, from around 2% on old maps
to 20% on recent ones, so breadth across maps is cheaper than depth on any one of them.

Usage: OSU_API_CLIENT/OSU_API_KEY in the environment, then

    python3 tools/reference/fetch.py [map-count] [--include-stable]
"""
import collections, csv, json, os, sys, time, urllib.error, urllib.parse, urllib.request

OUT = "build/reference"
MANIFEST = f"{OUT}/manifest.json"
PLAYER = "zaksynack playing"
PACE = 1.0


def token():
    body = urllib.parse.urlencode({
        "client_id": os.environ["OSU_API_CLIENT"],
        "client_secret": os.environ["OSU_API_KEY"],
        "grant_type": "client_credentials",
        "scope": "public"}).encode()
    request = urllib.request.Request("https://osu.ppy.sh/oauth/token", data=body,
                                     headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["access_token"]


def call(url, bearer, params=None, raw=False):
    """One request, paced, retrying on rate limit and transient server errors."""
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {"Authorization": f"Bearer {bearer}", "x-api-version": "20220705",
               "Accept": "application/x-osu-replay" if raw else "application/json"}

    for attempt in range(5):
        time.sleep(PACE)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                return response.read() if raw else json.load(response)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except OSError:
            time.sleep(5 * (attempt + 1))
    return None


def targets(limit):
    """Maps ranked by how many of the player's sixteenth-run clicks they hold, which is
    where a comparison has the most of their data to stand against."""
    md5 = {r["Path"].split("/")[-1]: r["BeatmapMd5"] for r in json.load(open("build/corpus.json"))}
    clicks = collections.Counter()

    with open("build/rhythm.csv") as f:
        for r in csv.DictReader(f):
            if r["tolerance"] != "0.02" or r["snap"] != "0.25" or r["interiorCircles"] != "1":
                continue
            if r["clickIndex"] == "-1" or not r["replay"].startswith(PLAYER):
                continue
            if (checksum := md5.get(r["replay"])):
                clicks[checksum] += 1

    return clicks.most_common(limit)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    lazer_only = "--include-stable" not in sys.argv
    count = int(args[0]) if args else 30
    os.makedirs(OUT, exist_ok=True)

    manifest = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
    bearer = token()
    fetched = skipped = missing = stable = 0

    for checksum, player_clicks in targets(count):
        beatmap = call("https://osu.ppy.sh/api/v2/beatmaps/lookup", bearer, {"checksum": checksum})

        if not beatmap:
            print(f"  lookup failed for {checksum[:8]}", flush=True)
            continue

        listing = call(f"https://osu.ppy.sh/api/v2/beatmaps/{beatmap['id']}/scores", bearer)
        scores = (listing or {}).get("scores", [])
        lazer = sum(1 for s in scores
                    if "CL" not in [m.get("acronym") for m in s.get("mods", []) if isinstance(m, dict)])
        print(f"  {beatmap['id']} [{beatmap['version']}] {lazer}/{len(scores)} lazer "
              f"(player has {player_clicks} clicks here)", flush=True)

        for score in scores:
            key = str(score["id"])
            mods = [m.get("acronym") for m in score.get("mods", []) if isinstance(m, dict)]

            if key in manifest:
                skipped += 1
                continue
            if lazer_only and "CL" in mods:
                stable += 1
                continue
            if not score.get("has_replay"):
                missing += 1
                continue

            data = call(f"https://osu.ppy.sh/api/v2/scores/{key}/download", bearer, raw=True)

            if not data:
                missing += 1
                continue

            name = f"ref-{key}.osr"
            with open(f"{OUT}/{name}", "wb") as f:
                f.write(data)

            manifest[key] = {
                "file": name,
                "beatmap_id": beatmap["id"],
                "beatmap_md5": checksum,
                "version": beatmap["version"],
                "user_id": score.get("user_id"),
                "username": score.get("user", {}).get("username"),
                "mods": mods,
                "rank": score.get("rank"),
                "pp": score.get("pp"),
                "accuracy": score.get("accuracy"),
                "max_combo": score.get("max_combo"),
            }
            fetched += 1

            with open(MANIFEST, "w") as f:
                json.dump(manifest, f, indent=1)

    print(f"\nfetched {fetched}, already had {skipped}, unavailable {missing}, skipped as stable {stable}")
    print(f"manifest: {MANIFEST} ({len(manifest)} replays)")


if __name__ == "__main__":
    main()
