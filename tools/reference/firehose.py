"""Sample the global score stream, for a replay corpus nobody selected.

Every other replay source is conditioned on something. A leaderboard is the top fifty.
A player's best list is their best. `scores/users/{u}/all` is whatever osu-web's cleanup
job happened to preserve. All of them answer "how do good runs look", and none of them
answer "how does a run look", which is the question a variance study actually asks.

`GET /scores` is the exception: recent passes across the whole site, unconditioned. Its
accuracy distribution has a median of 89% and a fifth percentile of 68%, which is what an
unselected sample is supposed to look like and what none of the other endpoints produce.

Three properties of the stream shape this script.

It is a *tail*, not an archive. There is no paging backward into history, so a corpus here
is built by returning over days rather than by one big pull. But the cursor must NOT be
carried between runs: the stream ran at 15.4 osu!standard scores a second when measured on
2026-09-19, so one thousand-score page covers about 1.1 minutes of live play and a fifteen
minute gap is some fourteen thousand new scores against the one or two thousand a run reads.
Resuming from a stored cursor falls further behind on every run, forever, and samples an
ever-staler window. Each run therefore starts fresh at the live edge, and the cursor exists
only to page within a run. What does persist is the highest score id seen, which deduplicates
across runs without a cursor and without a set that grows without bound.

Metadata is cheap and replays are not. A page is one request and carries a thousand scores;
a replay is one request each, and the download endpoint throttles hard in bursts — measured
at roughly four a minute sustained. So this records metadata for *everything* it sees and
downloads replays for a small random sample. The asymmetry is the point: the metadata log
is an unbiased sample of real play and is enough on its own for anything that only needs an
accuracy number, while the replay sample is small, slow, and still conditioned on the server
having preserved the replay at all.

That last bias does not go away. Roughly a quarter of passes have no stored replay, and they
will not be the good ones. Every replay corpus in this project inherits that, including this
one; what makes this one better is that the *selection among preserved scores* is random
rather than rank-ordered.

These are other people's plays, pulled through an API anyone with an account can use. They
land in build/, which is gitignored, and they are not ours to redistribute. CLAUDE.md asks
for an email to ppy before pulling at volume, and a standing cron job is volume.

There is also a capture mode, which is a different thing and exists for one job: deciding
the sampling parameters above. It holds the cursor and polls fast enough to keep up, so it
records the stream *complete* rather than sampled. A complete log is a superset of every
possible (interval, depth) configuration, so one capture run evaluates all of them offline
instead of needing a grid of live experiments — and at one page a minute it costs about the
same as any partial config would.

    python3 tools/reference/firehose.py [--pages 2] [--replays 10] [--no-replays]
    python3 tools/reference/firehose.py --complete [--seconds 21600] [--every 60]
"""
import json, os, random, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import call, token

OUT = "build/firehose"
REPLAYS = f"{OUT}/replays"
STATE = f"{OUT}/state.json"
SCORES = f"{OUT}/scores.jsonl"
TAKEN = f"{OUT}/replays.jsonl"

# Only the fields a later question could need. The full score object is ~7x larger and the
# rest of it is display chrome; at a thousand scores a run that difference is the whole disk
# budget.
KEEP = ("id", "user_id", "beatmap_id", "ruleset_id", "accuracy", "pp", "max_combo",
        "passed", "rank", "ended_at", "build_id", "has_replay", "preserve", "total_score")


def arg(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


# The firehose carries no beatmap object at all — only beatmap_id — and `include=beatmap`
# does not change that. Length, star rating and BPM therefore have to be resolved afterwards
# through the bulk /beatmaps endpoint, which takes fifty ids a request. That is the better
# order anyway: only the maps that survive into the analysis need resolving, which is far
# fewer than the maps that pass through the stream.
def detailed(s, fetched_at):
    row = compact(s)
    row["fetched_at"] = fetched_at
    return row


def capture(bearer, seconds, every):
    """Hold the cursor and keep pace with the stream, so the log is gapless.

    Each cycle reads until a page comes back short, which is the signal that the live edge
    has been reached. `fetched_at` is stamped per batch rather than per score, because
    reconstructing a polling schedule offline needs to know when a reader would have seen a
    score, not when it was set."""
    out = f"{OUT}/complete.jsonl"
    started, cursor, total, cycles = time.time(), None, 0, 0

    while time.time() - started < seconds:
        began = time.time()
        batch, stamp = [], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        while True:
            params = {"ruleset": "osu"}
            if cursor:
                params["cursor_string"] = cursor

            page = call("https://osu.ppy.sh/api/v2/scores", bearer, params)

            if not isinstance(page, dict) or not page.get("scores"):
                break

            batch.extend(page["scores"])
            cursor = page.get("cursor_string") or cursor

            # A short page means the live edge. Anything else means we are behind and
            # should keep reading rather than wait out the interval and fall further back.
            if len(page["scores"]) < 900 or not page.get("cursor_string"):
                break

        if batch:
            with open(out, "a") as f:
                for sc in batch:
                    f.write(json.dumps(detailed(sc, stamp), separators=(",", ":")) + "\n")
            total += len(batch)

        cycles += 1
        if cycles % 10 == 0:
            elapsed = time.time() - started
            print(f"  {elapsed/60:5.1f} min  {total:7d} scores  {total/max(elapsed,1):5.1f}/s", flush=True)

        time.sleep(max(0.0, every - (time.time() - began)))

    print(f"capture done: {total} scores over {(time.time()-started)/60:.1f} min -> {out}")
    return 0


def compact(s):
    row = {k: s.get(k) for k in KEEP}
    row["mods"] = [m.get("acronym") for m in s.get("mods", []) if isinstance(m, dict)]
    row["statistics"] = s.get("statistics")
    row["beatmap_md5"] = (s.get("beatmap") or {}).get("checksum")
    return row


def main():
    if "--complete" in sys.argv:
        os.makedirs(OUT, exist_ok=True)
        return capture(token(), arg("--seconds", 21600), arg("--every", 60))

    pages = arg("--pages", 2)
    wanted = 0 if "--no-replays" in sys.argv else arg("--replays", 10)
    os.makedirs(REPLAYS, exist_ok=True)

    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    bearer = token()
    fresh, cursor = [], None

    for _ in range(pages):
        params = {"ruleset": "osu"}
        if cursor:
            params["cursor_string"] = cursor

        page = call("https://osu.ppy.sh/api/v2/scores", bearer, params)

        if not isinstance(page, dict) or not page.get("scores"):
            break

        fresh.extend(page["scores"])
        cursor = page.get("cursor_string")

        if not cursor:
            break

    # Ids increase with time, so the high-water mark is all the dedup a run needs. Blocks
    # only overlap when the interval is short enough that the stream has not moved a full
    # page, which is a configuration mistake rather than a case to handle.
    mark = state.get("last_id", 0)
    fresh = [s for s in fresh if s["id"] > mark]

    # Metadata first and unconditionally: it is the half that is cheap, unbiased and useful
    # on its own, and it should survive a replay download failing or being switched off.
    with open(SCORES, "a") as f:
        for s in fresh:
            f.write(json.dumps(compact(s), separators=(",", ":")) + "\n")

    if fresh:
        state["last_id"] = max(s["id"] for s in fresh)
    state["scores_seen"] = state.get("scores_seen", 0) + len(fresh)
    state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)

    got = 0
    if wanted and fresh:
        # Uniform among the ones that have a replay at all. Sampling by rank or by pp would
        # rebuild exactly the selection this endpoint exists to escape.
        pool = [s for s in fresh if s.get("has_replay")]
        random.shuffle(pool)

        for s in pool:
            if got >= wanted:
                break

            name = f"fh-{s['id']}.osr"
            if os.path.exists(f"{REPLAYS}/{name}"):
                continue

            data = call(f"https://osu.ppy.sh/api/v2/scores/{s['id']}/download", bearer, raw=True)
            if not data:
                continue

            with open(f"{REPLAYS}/{name}", "wb") as f:
                f.write(data)
            with open(TAKEN, "a") as f:
                f.write(json.dumps({"file": name, **compact(s)}, separators=(",", ":")) + "\n")
            got += 1

    state["replays_taken"] = state.get("replays_taken", 0) + got
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)

    print(f"scores seen {len(fresh)} (total {state['scores_seen']}), "
          f"replays taken {got} (total {state['replays_taken']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
