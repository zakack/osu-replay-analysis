#!/usr/bin/env python3
"""Facts about one play session, as plain text for a person or a model to narrate from.

Everything here is computed; nothing is interpreted. The session-recap skill
(.claude/skills/session-recap/SKILL.md) turns this output into a short summary and must
not add numbers that are not in it.

    python3 tools/analysis/recap.py              # the latest session in build/sessions.jsonl
    python3 tools/analysis/recap.py --session 2  # the one before it
    python3 tools/analysis/recap.py --no-drift   # skip the tapping-drift check (~20 s)

Sections: setup, plays, map comparison against the previous 90 days, attempts (including
quick retries, from lazer's own log), offset changes, recent setup-timeline rows, the
tapping-drift check, and trace alignment. A section with nothing to say says so.
"""
import argparse, bisect, csv, glob, json, os, re, statistics as st, subprocess, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

PLAYER = "zaksynack"
ap = argparse.ArgumentParser()
ap.add_argument("--session", type=int, default=1, help="1 = latest, 2 = the one before, ...")
ap.add_argument("--no-drift", action="store_true")
args = ap.parse_args()
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def utc(s):
    s = s.replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


class Api:
    """The osu! API, for the pp the server awarded: realm stores none (Pp is null on every
    local score). Credentials as for tools/reference/fetch.py, from the environment or
    ~/.config/osu-api.env. Each score's pp is cached in build/pp-cache.json."""

    def __init__(self):
        import urllib.parse, urllib.request
        self.u, self.bearer, self.error = urllib, None, None
        env = dict(os.environ)
        try:
            for line in open(os.path.expanduser("~/.config/osu-api.env")):
                k, _, v = line.strip().partition("=")
                env.setdefault(k, v)
        except OSError:
            pass
        try:
            body = urllib.parse.urlencode({"client_id": env["OSU_API_CLIENT"], "client_secret": env["OSU_API_KEY"],
                                           "grant_type": "client_credentials", "scope": "public"}).encode()
            with urllib.request.urlopen(urllib.request.Request("https://osu.ppy.sh/oauth/token", data=body,
                                        headers={"Accept": "application/json"}), timeout=15) as r:
                self.bearer = json.load(r)["access_token"]
        except Exception as e:  # no credentials, no network: the recap goes on without pp
            self.error = f"{type(e).__name__}: {e}"

    def get(self, path, **params):
        url = "https://osu.ppy.sh/api/v2/" + path + ("?" + self.u.parse.urlencode(params) if params else "")
        req = self.u.request.Request(url, headers={"Authorization": f"Bearer {self.bearer}", "Accept": "application/json"})
        with self.u.request.urlopen(req, timeout=15) as r:
            return json.load(r)


sessions = [json.loads(l) for l in open("build/sessions.jsonl") if l.strip()]
s = sessions[-args.session]
start, end = utc(s["start"]), utc(s["end"])
local = datetime.fromisoformat(s["start"]).tzinfo
fmt = lambda d: d.astimezone(local).strftime("%Y-%m-%d %H:%M")

print(f"# Session {fmt(start)} to {fmt(end)[11:]} local ({(end - start).seconds // 60} min)")
print(f"output {s.get('output', '?')}, monitor profile {s.get('monitor', '?')}, pipewire {s.get('pipewire', '?')}")

# ---- plays ----
idx = json.load(open("build/score-index.json"))
idx = idx if isinstance(idx, list) else idx.get("entries", list(idx.values()))
mine = [x for x in idx if x["User"] == PLAYER and x["Ruleset"] == "osu"]
for x in mine:
    x["_t"] = utc(x["Date"])
plays = sorted((x for x in mine if start <= x["_t"] <= end + timedelta(minutes=2)), key=lambda x: x["_t"])
md5s = {x["BeatmapMd5"] for x in plays}
prior = [x for x in mine if x["BeatmapMd5"] in md5s and start - timedelta(days=90) <= x["_t"] < start]

want = {x["Filename"] + ".osr" for x in plays + prior}
geo = defaultdict(list)          # replay -> [(hitError or None, result, aimErrorRadii)]
if want and os.path.exists("build/geometry.csv"):
    # grep first: geometry.csv is ~2 GB and C is ~50x faster than csv.reader at skipping it
    pat = "\n".join(sorted(want))
    g = subprocess.run(["grep", "-F", "-f", "-", "build/geometry.csv"], input=pat, capture_output=True, text=True).stdout
    head = open("build/geometry.csv").readline().strip().split(",")
    I = {k: i for i, k in enumerate(head)}
    for row in csv.reader(g.splitlines()):
        if row[0] in want and row[I["kind"]] in ("circle", "slider"):
            he = row[I["hitError"]]
            ae = row[I["aimErrorRadii"]]
            geo[row[0]].append((float(he) if he else None, row[I["result"]], float(ae) if ae else None))


def stats(rep):
    rows = geo.get(rep, [])
    he = [r[0] for r in rows if r[0] is not None]
    ae = [r[2] for r in rows if r[2] is not None]
    return dict(n=len(he), med=st.median(he) if he else None,
                ur=10 * st.pstdev(he) if len(he) > 1 else None,
                aim=st.median(ae) if ae else None,
                miss=sum(r[1] == "Miss" for r in rows))


api = Api()
cache_path = "build/pp-cache.json"
cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
pp, user_id = {}, None
for p in plays:
    sid = str(p.get("OnlineId") or -1)
    if sid == "-1":
        continue                          # never submitted: a fail, or played offline
    if sid not in cache and api.bearer:
        try:
            sc = api.get(f"scores/{sid}")
            cache[sid] = {"pp": sc.get("pp"), "user_id": sc.get("user_id")}
        except Exception as e:
            api.error = f"{type(e).__name__}: {e}"
    if sid in cache:
        pp[sid] = cache[sid]["pp"]
        user_id = user_id or cache[sid].get("user_id")
if cache:
    json.dump(cache, open(cache_path, "w"))

print(f"\n## Plays ({len(plays)} saved, {sum(p['Rank'] == 'F' for p in plays)} failed)")
if plays:
    print("time   rank  acc     combo  pp     hit-err  UR    aim(r)  miss  map")
for p in plays:
    k = stats(p["Filename"] + ".osr")
    f = lambda v, w, d: f"{v:{w}.{d}f}" if v is not None else " " * (w - 1) + "-"
    v = pp.get(str(p.get("OnlineId") or -1))
    print(f"{fmt(p['_t'])[11:]}  {p['Rank']:4}  {p['Accuracy'] * 100:5.2f}%  {p['MaxCombo']:5}  {f(v, 5, 0)}  "
          f"{f(k['med'], 6, 1)}  {f(k['ur'], 5, 0)}  {f(k['aim'], 5, 2)}  {k['miss']:4}  "
          f"{p['Beatmap'][:70]}{' +' + p['Mods'] if p['Mods'] else ''}")
print("pp = what the server awarded (- = none: failed, unranked map, or not submitted); hit-err = median ms (+ late); UR = 10 x SD of hit error; aim(r) = median distance from circle centre at the hit, in circle radii")

# ---- pp ----
print("\n## pp")
if not api.bearer and not pp:
    print(f"(unavailable: {api.error})")
else:
    got = [(v, p) for p in plays if (v := pp.get(str(p.get("OnlineId") or -1)))]
    if got:
        v, p = max(got, key=lambda g: g[0])
        print(f"highest this session: {v:.0f}pp on {p['Beatmap'][:60]} ({p['Accuracy'] * 100:.2f}%)")
    else:
        print("no play this session was awarded pp")
    if api.bearer and user_id:
        try:
            best = api.get(f"users/{user_id}/scores/best", mode="osu", limit=100) + \
                   api.get(f"users/{user_id}/scores/best", mode="osu", limit=100, offset=100)
            pos = {str(b["id"]): i + 1 for i, b in enumerate(best)}
            ids = {str(p.get("OnlineId")): p for p in plays}
            entered = sorted((pos[i], ids[i]) for i in ids if i in pos)
            for n, p in entered:
                print(f"top-200 play #{n}: {pp[str(p['OnlineId'])]:.0f}pp, {p['Beatmap'][:60]}")
            if not entered:
                print(f"no play entered the top 200 (#200 is {best[-1]['pp']:.0f}pp)" if best else "no top plays listed")
            u = api.get(f"users/{user_id}/osu")
            total, rank = u["statistics"]["pp"], u["statistics"].get("global_rank")
            hist = "build/pp-history.jsonl"
            prev = None
            if os.path.exists(hist):
                for line in open(hist):
                    h = json.loads(line)
                    if utc(h["at"]) < start:
                        prev = h
            print(f"total now {total:.2f}pp, global rank #{rank}"
                  + (f"; {total - prev['pp']:+.2f}pp and {(prev['rank'] or 0) - (rank or 0):+d} places since {fmt(utc(prev['at']))}"
                     if prev and prev.get("pp") is not None else "; no earlier total recorded to compare with"))
            with open(hist, "a") as fh:
                fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "pp": total, "rank": rank}) + "\n")
        except Exception as e:
            print(f"(top plays and total unavailable: {type(e).__name__}: {e})")

# ---- map comparison ----
print("\n## Same maps, previous 90 days")
any_cmp = False
for md5 in sorted(md5s, key=lambda m: min(p["_t"] for p in plays if p["BeatmapMd5"] == m)):
    now = [p for p in plays if p["BeatmapMd5"] == md5]
    before = [p for p in prior if p["BeatmapMd5"] == md5 and p["Rank"] != "F"]
    name = now[0]["Beatmap"][:60]
    best_now = max((p["Accuracy"] for p in now if p["Rank"] != "F"), default=None)
    if not before:
        today = f"best today {best_now * 100:.2f}%" if best_now is not None else "no completed play today"
        print(f"- {name}: {today}; no completed play in the previous 90 days")
        continue
    any_cmp = True
    best_before = max(p["Accuracy"] for p in before)
    med_before = st.median(p["Accuracy"] for p in before)
    he_before = [stats(p["Filename"] + ".osr")["med"] for p in before]
    he_before = [v for v in he_before if v is not None]
    he_now = [v for v in (stats(p["Filename"] + ".osr")["med"] for p in now) if v is not None]
    bn = f"{best_now * 100:.2f}%" if best_now is not None else "no completed play"
    line = (f"- {name}: best today {bn}; before: best {best_before * 100:.2f}%, "
            f"median {med_before * 100:.2f}% over {len(before)} completed plays")
    if he_now and he_before:
        line += f"; median hit error {st.median(he_now):+.1f} ms today vs {st.median(he_before):+.1f} ms before"
    if best_now is not None and best_now > best_before:
        line += "  [NEW 90-DAY BEST]"
    print(line)
if not md5s:
    print("(no plays)")

# ---- attempts, from lazer's runtime log (times there are UTC) ----
print("\n## Attempts (lazer runtime log, includes quick retries and quits)")
att = []
for f in sorted(set(glob.glob("build/lazer-logs/*runtime*.log") + glob.glob(os.path.expanduser("~/.config/osu-lazer/logs/*runtime*.log")))):
    beat, t_in = None, None
    for l in open(f, errors="replace"):
        m = re.search(r"working beatmap updated to (.*)$", l)
        if m:
            beat = m.group(1).strip()
        if "entered SoloPlayer#" in l:
            t_in, b = l[:19], beat
        elif t_in and "exit from SoloPlayer#" in l:
            a, z = (datetime.fromisoformat(x).replace(tzinfo=timezone.utc) for x in (t_in, l[:19]))
            if start - timedelta(minutes=1) <= a <= end:
                att.append((a, (z - a).total_seconds(), b))
            t_in = None
att = sorted(set(att))
if att:
    per = defaultdict(list)
    for a, secs, b in att:
        per[b].append(secs)
    print(f"{len(att)} attempts, {len(plays)} saved; under 20 s (quick retry or quit early): {sum(v < 20 for _, v, _ in att)}")
    for b, v in sorted(per.items(), key=lambda kv: -len(kv[1])):
        print(f"- {len(v):3} attempts, {sum(x < 20 for x in v):3} under 20 s: {b[:70]}")
else:
    print("(no attempts found; the log archive may not cover this session)")

# ---- offsets ----
print("\n## Per-map offset changes")
rows = []
if os.path.exists("build/offset-history.csv"):
    for r in csv.DictReader(open("build/offset-history.csv")):
        a = datetime.strptime(r["after"], "%Y-%m-%dT%H:%M:%S%z")
        bb = datetime.strptime(r["before"], "%Y-%m-%dT%H:%M:%S%z")
        if bb >= start and a <= end + timedelta(minutes=10):
            rows.append(f"- {r['title']} [{r['difficulty']}]: {r['old']} -> {r['new']} ms")
print("\n".join(rows) if rows else "(none)")

# ---- setup timeline ----
print("\n## Setup changes dated within 7 days before the session")
recent = []
for r in csv.DictReader(open("setup-timeline.csv")):
    try:
        d = datetime.fromisoformat(r["start"][:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        continue
    if start - timedelta(days=7) <= d <= end:
        recent.append((r["start"], f"- {r['start']} {r['component']}: {r['change'][:200]}"))
recent = [line for _, line in sorted(recent)]
print("\n".join(recent) if recent else "(none)")

# ---- drift ----
print("\n## Tapping drift check (tools/analysis/drift.py latest)")
if args.no_drift or args.session != 1:
    print("(skipped)" if args.no_drift else "(only computed for the latest session)")
else:
    out = ""
    for n in (1, 2, 3, 5, 8, 12, 20):
        out = subprocess.run([sys.executable, "tools/analysis/drift.py", "latest", "--sessions", str(n)],
                             capture_output=True, text=True).stdout
        if "cannot carry a CI" not in out:
            break
    print("\n".join(out.strip().splitlines()[-5:]) or "(no output)")

# ---- traces ----
print("\n## Input traces")
best = None
for j in glob.glob("build/traces/*.align.json"):
    stamp = os.path.basename(j)[:19]
    try:
        t = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=local)
    except ValueError:
        continue
    if abs((t - start).total_seconds()) < 120:
        best = j
if best:
    al = json.load(open(best))
    dr = [a["drift"] * 1e6 for a in al if "drift" in a and len(a) > 3]
    print(f"{os.path.basename(best)}: {len(al)} replays aligned"
          + (f", game-clock drift median {st.median(dr):+.0f} ppm" if dr else ""))
else:
    print("(no aligned trace for this session)")
