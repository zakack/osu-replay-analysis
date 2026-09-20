"""Collect the daily challenge leaderboard, which is a natural experiment.

Every other source in this project inherits the players' own map choice. Leaderboards are
maps someone farmed; a best list is maps someone liked; the firehose is whatever the
population happened to queue, which skews hard to short maps — measured, repeat pairs there
have a median length of 39s against a ~98s baseline. Map choice is therefore confounded with
everything, and map structure is exactly what the variance question is about.

The daily challenge removes that in one step: **ppy assigns the map**. Nobody picked it for
pp, nobody picked it because they are good at it, and on the day it opens nearly everyone
playing it is seeing it for the first time. Over a run of days the assignment varies the
thing under study — 205s on 2026-09-19, 422s on 2026-09-20 — without the players having any
say. That is randomised treatment with a large sample, which is not otherwise available here.

The field is large and fully retrievable: 1934 scores on 2026-09-19 against 7179 listed
participants, complete in 39 requests. The gap is people who joined without landing a score
on the board.

What it is *not* is an attempt log. The board holds one score per user, their best, and the
per-user endpoint is not readable with a client-credentials token. So a single pull measures
**spread across players on identical geometry**, not within-player variance.

Snapshots recover some of the missing half. Polling the board through the day and diffing it
gives each user's best-so-far trajectory, and a record sequence is exactly the max-of-N
structure the variance model runs on: a best that keeps climbing is a player still sampling a
wide distribution, a best that sticks is one who has converged. That is a weaker signal than
a true attempt log and it is the strongest available.

Two confounds to carry into any conclusion. The population is not constant across days —
a harder assignment draws fewer and better players, so spread differences between maps are
partly selection and not only structure. And the board holds passes, so every distribution is
truncated at the fail threshold, hardest on the hard maps.

    python3 tools/reference/daily.py --snapshot [--every 1800] [--seconds 21600]
    python3 tools/reference/daily.py --archive <room_id>
    python3 tools/reference/daily.py --backfill [50]
    python3 tools/reference/daily.py --list [10]
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import call, token

OUT = "build/daily"
KEEP = ("id", "user_id", "accuracy", "pp", "max_combo", "passed", "rank",
        "ended_at", "started_at", "has_replay", "total_score", "build_id")


def arg(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


def rooms(bearer, mode, limit=1):
    r = call("https://osu.ppy.sh/api/v2/rooms", bearer,
             {"category": "daily_challenge", "mode": mode, "limit": limit})
    return r if isinstance(r, list) else []


def detail(bearer, room_id):
    """The room carries the playlist item and the beatmap fields the analysis needs; the
    listing does not, so it has to be fetched per room."""
    room = call(f"https://osu.ppy.sh/api/v2/rooms/{room_id}", bearer)
    item = ((room or {}).get("playlist") or [{}])[0]
    bm = item.get("beatmap") or {}
    st = bm.get("beatmapset") or {}
    return room, item, {
        "room_id": room_id,
        "playlist_item_id": item.get("id"),
        "beatmap_id": bm.get("id"),
        "artist": st.get("artist"), "title": st.get("title"), "version": bm.get("version"),
        "total_length": bm.get("total_length"), "difficulty_rating": bm.get("difficulty_rating"),
        "starts_at": (room or {}).get("starts_at"), "ends_at": (room or {}).get("ends_at"),
        "participant_count": (room or {}).get("participant_count"),
    }


def board(bearer, room_id, item_id):
    """The whole leaderboard, paged to exhaustion."""
    url = f"https://osu.ppy.sh/api/v2/rooms/{room_id}/playlist/{item_id}/scores"
    out, cursor = [], None

    while True:
        params = {"limit": 50}
        if cursor:
            params["cursor_string"] = cursor

        r = call(url, bearer, params)
        xs = (r or {}).get("scores", [])
        if not xs:
            break

        out.extend(xs)
        cursor = (r or {}).get("cursor_string")
        if not cursor:
            break

    return out


def row(s):
    d = {k: s.get(k) for k in KEEP}
    d["mods"] = [m.get("acronym") for m in s.get("mods", []) if isinstance(m, dict)]
    d["statistics"] = s.get("statistics")
    return d


def write(meta, scores, stamp):
    os.makedirs(OUT, exist_ok=True)
    path = f"{OUT}/room-{meta['room_id']}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps({"snapshot": stamp, "meta": meta, "n": len(scores)},
                           separators=(",", ":")) + "\n")
        for s in scores:
            f.write(json.dumps({"snapshot": stamp, **row(s)}, separators=(",", ":")) + "\n")
    return path


def main():
    bearer = token()

    if "--list" in sys.argv:
        for mode in ("active", "ended"):
            for r in rooms(bearer, mode, arg("--list", 10) if mode == "ended" else 1):
                print(f"  {mode:7} room {r.get('id')}  {r.get('name')}  "
                      f"participants {r.get('participant_count')}  ends {r.get('ends_at')}")
        return 0

    if "--backfill" in sys.argv:
        # Fifty assignments is what makes the design work. At three, length and star rating
        # were perfectly collinear and nothing could be attributed to either; across weeks
        # ppy varies them separately, which is the only way they come apart.
        want = arg("--backfill", 50)
        done = skipped = 0
        for r in rooms(bearer, "ended", want):
            rid = r["id"]
            if os.path.exists(f"{OUT}/room-{rid}.jsonl"):
                skipped += 1
                continue
            bearer = token()
            _, item, meta = detail(bearer, rid)
            if not item.get("id"):
                continue
            scores = board(bearer, rid, item["id"])
            write(meta, scores, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            done += 1
            print(f"  {r.get('name')}: {len(scores):5d} scores  "
                  f"{meta['total_length']}s sr {meta['difficulty_rating']:.2f}  "
                  f"{meta['artist']} - {meta['title']}", flush=True)
        print(f"backfill done: {done} archived, {skipped} already had")
        return 0

    if "--archive" in sys.argv:
        rid = arg("--archive", 0)
        _, item, meta = detail(bearer, rid)
        scores = board(bearer, rid, item["id"])
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"{meta['artist']} - {meta['title']} [{meta['version']}] "
              f"{meta['total_length']}s sr {meta['difficulty_rating']}")
        print(f"archived {len(scores)} scores -> {write(meta, scores, stamp)}")
        return 0

    every, seconds = arg("--every", 1800), arg("--seconds", 21600)
    active = rooms(bearer, "active", 1)
    if not active:
        print("no active daily challenge")
        return 1

    rid = active[0]["id"]
    _, item, meta = detail(bearer, rid)
    print(f"{meta['artist']} - {meta['title']} [{meta['version']}] "
          f"{meta['total_length']}s sr {meta['difficulty_rating']}")
    print(f"room {rid} item {item['id']}, snapshotting every {every}s for {seconds/3600:.1f}h", flush=True)

    started = time.time()
    while time.time() - started < seconds:
        began = time.time()
        bearer = token()                      # a long run outlives one token
        _, item, meta = detail(bearer, rid)
        scores = board(bearer, rid, item["id"])
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write(meta, scores, stamp)
        print(f"  {stamp}  {len(scores):5d} scores  {meta['participant_count']} participants", flush=True)
        time.sleep(max(0.0, every - (time.time() - began)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
