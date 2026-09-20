"""Pull the top-200 lists of friends inside a chosen rank band.

The two reference corpora this project already has answer the wrong question for a player at
#170k. A map's top-50 board is four digits above him, so every difference is confounded with
being vastly better. The daily boards span the whole ladder but ppy assigns the map, so
nothing is shared with his own history by construction.

Friends inside the same rank band are neither. They are matched on skill and they choose the
same maps, so any map both have played is a controlled comparison for free: same geometry,
same mods where they agree, and a peer rather than a god at the other end of it.

Only the friends *list* needs user-scoped auth; everything downstream is public. Rather than
handle a token, this reads user ids out of a saved friends page and uses client credentials
from there. Re-save the page when the list changes.

    python3 tools/reference/friends.py [--page build/zaksynack-friends.html]
                                       [--low 120000] [--high 220000]
                                       [--include Name,Other]

--include pulls named players whatever their rank. The band is the default because matched
skill is what makes a shared map a controlled comparison, but a few rungs above it are worth
carrying deliberately: they share map taste with the player in a way a top-50 board does not,
while being far enough ahead that the difference is worth looking at.
"""
import collections, json, os, re, sys, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import call, token

OUT = "build"


def arg(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


def main():
    page = arg("--page", f"{OUT}/zaksynack-friends.html")
    low, high = arg("--low", 120_000), arg("--high", 220_000)

    html = open(page, encoding="utf-8", errors="replace").read()
    ids = sorted({u for u in re.findall(r'osu\.ppy\.sh/users/(\d+)', html)}, key=int)
    print(f"{len(ids)} user ids in {page}")

    bearer = token()
    profiles = {}

    # Bulk lookup, fifty a request: one call per fifty friends rather than one per friend.
    for i in range(0, len(ids), 50):
        qs = urllib.parse.urlencode([("ids[]", x) for x in ids[i:i + 50]])
        r = call(f"https://osu.ppy.sh/api/v2/users?{qs}", bearer)
        for u in (r or {}).get("users", []):
            st = (u.get("statistics_rulesets") or {}).get("osu") or u.get("statistics") or {}
            profiles[u["id"]] = {"username": u.get("username"), "rank": st.get("global_rank"),
                                 "pp": st.get("pp"), "acc": st.get("hit_accuracy"),
                                 "playcount": st.get("play_count")}

    with open(f"{OUT}/friends.json", "w") as f:
        json.dump(profiles, f, indent=1)
    print(f"resolved {len(profiles)} -> {OUT}/friends.json")

    keep = {k for k, v in profiles.items() if v["rank"] and low <= v["rank"] <= high}

    named = [n.strip() for n in arg("--include", "").split(",") if n.strip()]
    if named:
        wanted = {n.casefold() for n in named}
        extra = {k for k, v in profiles.items() if (v.get("username") or "").casefold() in wanted}
        for k in extra - keep:
            print(f"  including {profiles[k]['username']} at #{profiles[k]['rank']:,} (outside the band)")
        keep |= extra

    peers = sorted(((k, profiles[k]) for k in keep), key=lambda kv: kv[1]["rank"])
    print(f"{len(peers)} players: band #{low:,}-#{high:,} plus {len(named)} named\n")

    rows = []
    for uid, v in peers:
        got = []
        for offset in (0, 100):
            p = call(f"https://osu.ppy.sh/api/v2/users/{uid}/scores/best", bearer,
                     {"mode": "osu", "limit": 100, "offset": offset})
            if not isinstance(p, list):
                break
            got += p
            if len(p) < 100:
                break

        for i, s in enumerate(got, 1):
            bm = s.get("beatmap") or {}
            rows.append({"user_id": uid, "username": v["username"], "rank": v["rank"],
                         "position": i, "beatmap_id": bm.get("id"), "checksum": bm.get("checksum"),
                         "score_id": s.get("id"), "pp": s.get("pp"), "accuracy": s.get("accuracy"),
                         "has_replay": s.get("has_replay"), "statistics": s.get("statistics"),
                         "mods": [m.get("acronym") for m in s.get("mods", []) if isinstance(m, dict)]})
        print(f"  {v['username'][:18]:18} #{v['rank']:,} -> {len(got)}", flush=True)

    with open(f"{OUT}/friends-best.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"\n{len(rows):,} scores -> {OUT}/friends-best.jsonl")

    mine = {v["beatmap_md5"] for v in json.load(open(f"{OUT}/best/manifest.json")).values()}
    shared = collections.Counter(r["checksum"] for r in rows if r["checksum"] in mine)
    print(f"maps shared with your own top-200: {len(shared)}  "
          f"(5+ peers: {sum(1 for n in shared.values() if n >= 5)}, "
          f"10+: {sum(1 for n in shared.values() if n >= 10)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
