"""Every daily challenge room's map, as metadata only, so the player's own plays can be found.

`daily.py` archives leaderboards and replays, and only from 2026-07 onward. This is lighter
and longer: one room-listing call and one call per room, as far back as the listing reaches,
keeping what a per-player evaluation needs -- the day, the map's MD5 (the key every local
index uses), its shape, and the mods the room *declared* required. No scores, no replays.

Resumable: rooms already in the output are not fetched again, so a nightly rerun costs one
call per new day. Paced by fetch.call at one request a second with backoff on 429.

    python3 tools/reference/dailymaps.py            # writes build/daily-maps.json
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import call, token

OUT = "build/daily-maps.json"


def listing(bearer):
    """The ended daily rooms, newest first, as far back as the endpoint reaches.

    Measured 2026-10-13: the listing is a bare list with no cursor and caps at 250 rooms
    whatever limit is asked, which reached back to 2026-02-06. Older rooms are not listable
    this way. That covers the use -- an evaluation fitted on the 180 days before each day --
    and a nightly rerun keeps the file growing from here, since known rooms are kept.
    """
    page = call("https://osu.ppy.sh/api/v2/rooms", bearer,
                {"category": "daily_challenge", "mode": "ended", "limit": 250})
    rooms = page if isinstance(page, list) else (page or {}).get("rooms") or []
    print(f"  listed {len(rooms)} rooms", flush=True)
    return rooms


def main():
    bearer = token()
    known = {}
    if os.path.exists(OUT):
        known = {r["room_id"]: r for r in json.load(open(OUT))}
    rooms = listing(bearer)
    # The listing without a cursor may stop at a page; if it returned fewer than the archive
    # already knows, say so rather than silently writing a shorter history.
    todo = [r for r in rooms if r["id"] not in known]
    print(f"{len(rooms)} rooms listed, {len(known)} known, {len(todo)} to fetch", flush=True)
    for n, r in enumerate(todo, 1):
        room = call(f"https://osu.ppy.sh/api/v2/rooms/{r['id']}", bearer)
        item = ((room or {}).get("playlist") or [{}])[0]
        bm = item.get("beatmap") or {}
        st = bm.get("beatmapset") or {}
        known[r["id"]] = {
            "room_id": r["id"],
            "name": r.get("name"),
            "starts_at": (room or r).get("starts_at"),
            "ends_at": (room or r).get("ends_at"),
            "beatmap_id": bm.get("id"),
            "md5": bm.get("checksum"),
            "artist": st.get("artist"), "title": st.get("title"), "version": bm.get("version"),
            "creator": st.get("creator"),
            "stars": bm.get("difficulty_rating"), "length": bm.get("total_length"),
            "bpm": bm.get("bpm"),
            "required_mods": [m.get("acronym") for m in item.get("required_mods") or []],
            "allowed_mods": [m.get("acronym") for m in item.get("allowed_mods") or []],
            "participants": (room or {}).get("participant_count"),
        }
        if n % 25 == 0 or n == len(todo):
            json.dump(sorted(known.values(), key=lambda x: x["starts_at"] or ""),
                      open(OUT, "w"), indent=1)
            print(f"  fetched {n}/{len(todo)}", flush=True)
    json.dump(sorted(known.values(), key=lambda x: x["starts_at"] or ""), open(OUT, "w"), indent=1)
    missing = sum(1 for r in known.values() if not r["md5"])
    print(f"wrote {OUT}: {len(known)} rooms, {missing} without a map checksum")


if __name__ == "__main__":
    main()
