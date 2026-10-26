"""Describe the friends list: rank, pp, hours, input devices, countries, and where the player sits.

One full-profile call per friend, because playstyle and the osu!-specific statistics only come
on /users/{id}/osu; the bulk and /friends endpoints carry neither reliably. Profiles are cached
in build/friends-profiles.json and a re-run fetches only ids not already there (--refresh to
fetch all again).

    python3 tools/reference/friends_stats.py [--refresh]
"""
import collections, json, os, statistics, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch
from fetch import call
from authorize import user_token

OUT = "build"
CACHE = f"{OUT}/friends-profiles.json"
fetch.PACE = 2.0  # friends.py may be pulling at the same time


def keep(u):
    s = u.get("statistics") or {}
    return {"id": u["id"], "username": u["username"], "country": (u.get("country") or {}).get("name"),
            "playstyle": u.get("playstyle"), "join_date": u.get("join_date"),
            "last_visit": u.get("last_visit"), "supporter": u.get("is_supporter"),
            "rank": s.get("global_rank"), "country_rank": s.get("country_rank"), "pp": s.get("pp"),
            "acc": s.get("hit_accuracy"), "playcount": s.get("play_count"),
            "hours": (s.get("play_time") or 0) / 3600, "total_hits": s.get("total_hits"),
            "max_combo": s.get("maximum_combo"), "level": (s.get("level") or {}).get("current"),
            "rank_highest": (u.get("rank_highest") or {}).get("rank")}


def pull():
    bearer = user_token()
    cache = {} if "--refresh" in sys.argv else (json.load(open(CACHE)) if os.path.exists(CACHE) else {})
    ids = [str(u["id"]) for u in call("https://osu.ppy.sh/api/v2/friends", bearer) or []]
    todo = [i for i in ids if i not in cache]
    print(f"{len(ids)} friends, {len(todo)} to fetch", flush=True)
    for n, i in enumerate(todo, 1):
        u = call(f"https://osu.ppy.sh/api/v2/users/{i}/osu", bearer)
        if u:
            cache[i] = keep(u)
        if n % 25 == 0:
            print(f"  {n}/{len(todo)}", flush=True)
            json.dump(cache, open(CACHE, "w"), indent=1)
    me = call("https://osu.ppy.sh/api/v2/me/osu", bearer)
    json.dump(cache, open(CACHE, "w"), indent=1)
    return [cache[i] for i in ids if i in cache], keep(me)


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=v.__getitem__)
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    return statistics.correlation(ranks(xs), ranks(ys))


def main():
    friends, me = pull()
    now = datetime.now(timezone.utc)
    for u in friends + [me]:
        u["years"] = (now - datetime.fromisoformat(u["join_date"])).days / 365.25
        u["idle_days"] = (now - datetime.fromisoformat(u["last_visit"])).days if u["last_visit"] else None
        u["pp_per_hour"] = u["pp"] / u["hours"] if u["hours"] else None
        u["min_per_play"] = 60 * u["hours"] / u["playcount"] if u["playcount"] else None

    ranked = [u for u in friends if u["rank"]]
    print(f"\n{len(friends)} friends, {len(ranked)} with an osu! global rank "
          f"(the rest inactive or never ranked in standard)\n")

    cols = [("rank", "global rank", "{:,.0f}"), ("pp", "pp", "{:,.0f}"), ("acc", "accuracy %", "{:.2f}"),
            ("hours", "hours played", "{:,.0f}"), ("playcount", "playcount", "{:,.0f}"),
            ("min_per_play", "min / play", "{:.2f}"), ("pp_per_hour", "pp / hour", "{:.2f}"),
            ("years", "account years", "{:.1f}"), ("rank_highest", "peak rank", "{:,.0f}"),
            ("idle_days", "days since visit", "{:,.0f}")]
    print(f"{'':17}{'min':>10}{'p25':>10}{'median':>10}{'p75':>10}{'max':>10}{'you':>10}{'you pct':>9}")
    for key, label, fmt in cols:
        v = sorted(u[key] for u in ranked if u[key] is not None)
        q = statistics.quantiles(v, n=4)
        mine = me[key]
        # Percentile as "share of friends you beat"; lower is better for ranks and idle days.
        lower_better = key in ("rank", "rank_highest", "idle_days")
        beat = sum((x > mine) if lower_better else (x < mine) for x in v) / len(v) if mine is not None else None
        cells = [v[0], q[0], q[1], q[2], v[-1], mine]
        print(f"{label:17}" + "".join(f"{(fmt.format(c) if c is not None else '-'):>10}" for c in cells)
              + (f"{beat:>8.0%}" if beat is not None else ""))
    print("(you pct = share of ranked friends you are ahead of on that column)")

    print("\nSpearman correlations, ranked friends:")
    for a, b in [("hours", "pp"), ("playcount", "pp"), ("years", "pp"), ("acc", "pp"),
                 ("hours", "acc"), ("min_per_play", "pp")]:
        pairs = [(u[a], u[b]) for u in ranked if u[a] is not None and u[b] is not None]
        print(f"  {a:>13} ~ {b:<4} rho {spearman(*zip(*pairs)):+.2f}  (n={len(pairs)})")

    bands = [(0, 10_000), (10_000, 50_000), (50_000, 120_000), (120_000, 220_000), (220_000, 10**9)]
    print("\nRank bands:")
    for lo, hi in bands:
        n = sum(lo <= u["rank"] < hi for u in ranked)
        print(f"  #{lo:>9,} - #{hi if hi < 10**9 else '':<9,}  {n:4} {'#' * n}" if hi < 10**9 else
              f"  #{lo:>9,} +           {n:4} {'#' * n}")

    print("\nSelf-disclosed input devices (playstyle):")
    combo = collections.Counter("+".join(sorted(u["playstyle"])) if u["playstyle"] else "(not set)" for u in friends)
    for k, n in combo.most_common():
        print(f"  {k:28} {n:4}  {n / len(friends):5.0%}")
    single = collections.Counter(d for u in friends for d in (u["playstyle"] or []))
    disclosed = sum(1 for u in friends if u["playstyle"])
    print("  any mention, of those who set it: " +
          ", ".join(f"{d} {n / disclosed:.0%}" for d, n in single.most_common()))
    for dev in ("tablet", "mouse"):
        group = [u["pp"] for u in ranked if u["playstyle"] and dev in u["playstyle"]
                 and not ({"tablet", "mouse"} <= set(u["playstyle"]))]
        if group:
            print(f"  median pp, {dev} (not both): {statistics.median(group):,.0f}  (n={len(group)})")
    print(f"  you: {'+'.join(me['playstyle'] or ['(not set)'])}")

    print("\nCountries:")
    countries = collections.Counter(u["country"] for u in friends)
    print(f"  {len(countries)} countries")
    for k, n in countries.most_common(15):
        print(f"  {k:24} {n:4}  {n / len(friends):5.0%}")
    print(f"  you: {me['country']}")

    print(f"\nSupporters: {sum(bool(u['supporter']) for u in friends)}  "
          f"Visited in last 30 days: {sum(u['idle_days'] is not None and u['idle_days'] <= 30 for u in friends)}")


if __name__ == "__main__":
    main()
