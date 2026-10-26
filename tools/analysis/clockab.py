"""Which clock does each hand keep? Reads a clock-ab record (tools/experiments/clock-ab.sh).

Per circle, measured against the object's time on the game clock:

    press   pressTime - startTime, over judged circles
    cursor  closestTime - startTime, over circles the cursor came within a radius of

Both come from `build/clicks.csv`, which the corpus sync extends. The cursor time is the
cursor's closest approach, which `Clicks.cs` places between 60Hz frames; one play's median is
good to a couple of ms, which is why the effect is read off many plays and not one.

**offset mode** compares per-play medians. With S = the A-B separation in ms:

    press shift ~ -S or +S    the button hand presses to the music; the test took
    press shift ~ 0           it presses to the screen too, and the test cannot answer
    cursor/press ratio ~ 0    the cursor keeps the approach circles
    cursor/press ratio ~ 1    the cursor keeps the music, or the button hand -- this cannot
                              tell those two apart; a step in the press *sound* alone can
                              (setup-timeline.csv, 2026-10-09)

**muted mode** compares per-play spreads (IQR / 1.349, a standard deviation that ignores the
tails), as the ratio muted / normal. Read the two hands against each other, never one alone:
the cursor's spread carries a 60Hz measurement floor in both conditions, so its ratio is
pulled toward 1 and only a gap between the hands' ratios means anything.

    press grows, cursor flat   the button hand needed the music and the cursor did not
    both grow alike            they share a clock, or the whole player is just worse muted

Every play is checked against `build/score-index.json` for MU and set aside if it is in the
wrong session; the metronome setting is not in the index and is taken on trust.

Both modes take the condition difference within each half (sessions 1 vs 2 and 4 vs 3), so
a linear drift across the evening cancels in their mean and their disagreement is a rough
error bar. Circles are split three ways: stream entries (the first four of a run of at least
five with gaps within GAP ms), the rest of each stream, and everything else. Entries are where
a player without music should be thrown most, since mid-stream they keep their own rhythm;
and a stream barely constrains when the cursor arrives, while a jump pins it to the press.

    python3 tools/analysis/clockab.py [build/experiments/clock-ab-<mode>-<stamp>.log] [--player zaksynack]
"""
import csv, datetime as dt, glob, json, math, re, statistics as st, sys

GAP = 110.0
RUN, ENTRY = 5, 4
STAMP = re.compile(r'(20\d\d-\d\d-\d\d_\d\d-\d\d)\)(?: \(\d+\))?\.osr$')


def arg(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def read_record(path):
    rec = {'offsets': {}, 'sessions': {}}
    for line in open(path):
        f = line.split()
        if f[0] in ('mode', 'base'):
            rec[f[0]] = f[1]
        elif f[0] == 'order':
            rec['order'] = f[1:]
        elif f[0] in ('A', 'B'):
            rec['offsets'][f[0]] = float(f[1])
        elif f[0] == 'session':
            i, cond, edge, when = int(f[1]), f[2], f[3], dt.datetime.fromisoformat(f[4])
            rec['sessions'].setdefault(i, {'cond': cond})[edge] = when.replace(tzinfo=None)
    return rec


def session_of(replay, sessions):
    """Lazer stamps the export name in local time to the minute; allow that minute of slack."""
    m = STAMP.search(replay)
    if not m:
        return None
    t = dt.datetime.strptime(m.group(1), '%Y-%m-%d_%H-%M')
    for i, s in sessions.items():
        if 'end' in s and s['start'] - dt.timedelta(minutes=1) <= t <= s['end'] + dt.timedelta(minutes=1):
            return i
    return None


def load(sessions, player):
    """Per replay, each circle as (startTime, press offset or None, cursor offset or None)."""
    plays = {}
    for r in csv.DictReader(open('build/clicks.csv')):
        k = r['replay']
        if not k.startswith(player + ' playing') or r['kind'] != 'circle':
            continue
        i = session_of(k, sessions)
        if i is None:
            continue
        t = float(r['startTime'])
        press = float(r['pressTime']) - t if r['result'] != 'Miss' and r['pressTime'] else None
        cursor = float(r['closestTime']) - t if r['closestTime'] and float(r['closestDist']) < 1.0 else None
        plays.setdefault(k, {'session': i, 'objs': []})['objs'].append((t, press, cursor))
    return plays


def muted_mismatches(plays, sessions):
    """Plays whose MU disagrees with their session's condition, by score-index filename."""
    mods = {e['Filename']: e['Mods'] for e in json.load(open('build/score-index.json'))}
    bad = []
    for k, play in plays.items():
        name = re.sub(r'(?: \(\d+\))?\.osr$', '', k)
        if name not in mods:
            bad.append((k, 'not in score-index'))
        elif ('MU' in mods[name]) != (sessions[play['session']]['cond'] == 'M'):
            bad.append((k, f"mods '{mods[name]}' in a {sessions[play['session']]['cond']} session"))
    return bad


def classes(objs):
    """'entry', 'inside' or 'other' per circle, from runs of near-spaced circles."""
    ts = sorted(o[0] for o in objs)
    label, run = {}, [ts[0]] if ts else []
    for a, b in zip(ts, ts[1:] + [math.inf]):
        if b - a <= GAP:
            run.append(b)
            continue
        for j, t in enumerate(run):
            label[t] = ('entry' if j < ENTRY else 'inside') if len(run) >= RUN else 'other'
        run = [b]
    return [label[o[0]] for o in objs]


def spread(xs):
    q = st.quantiles(xs, n=4)
    return (q[2] - q[0]) / 1.349


def per_play(play, want, stat):
    objs = [o for o, c in zip(play['objs'], classes(play['objs'])) if want is None or c == want]
    p = [o[1] for o in objs if o[1] is not None]
    c = [o[2] for o in objs if o[2] is not None]
    return (stat(p), stat(c)) if len(p) >= 20 and len(c) >= 20 else None


def main():
    path = next((a for a in sys.argv[1:] if a.endswith('.log')), None) or sorted(glob.glob('build/experiments/clock-ab-*.log'))[-1]
    rec = read_record(path)
    sessions, muted = rec['sessions'], rec['mode'] == 'muted'
    plays = load(sessions, arg('--player', 'zaksynack'))
    if muted:
        for k, why in muted_mismatches(plays, sessions):
            print(f"set aside: {why}: {k}")
            del plays[k]
        first, second, stat = 'M', 'N', spread
        print(f"{path}: muted, order {' '.join(rec['order'])}, {len(plays)} plays; spreads in ms, muted/normal ratios")
    else:
        first, second, stat = 'A', 'B', st.median
        o = rec['offsets']
        print(f"{path}: offset, order {' '.join(rec['order'])}, A {o['A']:+.1f} B {o['B']:+.1f} (S = {o['A'] - o['B']:.0f} ms), {len(plays)} plays")

    for label, want in (('all circles', None), ('stream entries', 'entry'), ('inside streams', 'inside'), ('other circles', 'other')):
        by = {i: [] for i in sessions}
        for play in plays.values():
            m = per_play(play, want, stat)
            if m:
                by[play['session']].append(m)
        means = {i: (st.mean(x[0] for x in v), st.mean(x[1] for x in v), len(v)) for i, v in by.items() if v}
        print(f"\n{label}")
        print(f"  {'session':8} {'cond':4} {'plays':>5} {'press':>7} {'cursor':>7}")
        sign = '' if muted else '+'
        for i in sorted(means):
            print(f"  {i:<8} {sessions[i]['cond']:4} {means[i][2]:5} {means[i][0]:{sign}7.1f} {means[i][1]:{sign}7.1f}")
        if len(means) < 4:
            print("  fewer than four sessions with enough circles; no estimate")
            continue
        # first condition against second within each half, so a linear drift cancels in the mean
        halves = []
        for a, b in ((1, 2), (4, 3)):
            if sessions[a]['cond'] != first:
                a, b = b, a
            if muted:
                halves.append((means[a][0] / means[b][0], means[a][1] / means[b][1]))
            else:
                halves.append((means[a][0] - means[b][0], means[a][1] - means[b][1]))
        dp, dc = st.mean(h[0] for h in halves), st.mean(h[1] for h in halves)
        err = max(abs(halves[0][0] - halves[1][0]), abs(halves[0][1] - halves[1][1])) / 2
        if muted:
            print(f"  muted/normal spread: press x{dp:.2f}, cursor x{dc:.2f} (halves disagree by +/-{err:.2f})")
        else:
            ratio = f"{dc / dp:.2f}" if abs(dp) > 3 else "n/a (press barely moved)"
            print(f"  A-B: press {dp:+.1f} ms, cursor {dc:+.1f} ms (halves disagree by +/-{err:.1f}); cursor/press {ratio}")


if __name__ == '__main__':
    main()
