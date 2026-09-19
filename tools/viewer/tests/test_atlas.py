"""Does the atlas draw the cell it says it is drawing?

Two of these run on real corpus data rather than on a fixture, which is unusual for a test
and is the point: the claim the page makes is that the picture and the findings table
cannot disagree, and that claim is about the corpus, not about a constructed example.

The differential test is the one CLAUDE.md asks for whenever reference geometry is
recomputed anywhere. It also asserts that the *wrong* frame fails, because the bug it
exists to catch — taking the apex to be object n rather than n-1 — produces a picture that
looks entirely plausible, and a test that only accepts the right answer would pass on it.
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

import atlas  # noqa: E402
from bins import cells, load  # noqa: E402
from bins.schema import DEG, SCHEMES  # noqa: E402

ROOT = atlas.ROOT

# Scene stores positions rounded to two decimal places, so even a perfect reconstruction
# disagrees with it a little. For a distance that is a flat tolerance; for an angle it is
# not, because the same positional error subtends a larger angle on a shorter leg. Scale
# the angular tolerance by the leg it was measured on rather than picking one epsilon
# loose enough for the worst case, which would stop testing the long legs at all.
POSITION_EPS = 5e-3          # radii
POSITION_ROUNDING = 0.02     # osu!pixels; 2dp per coordinate, with headroom
ANGLE_FLOOR = 1e-6           # radians, for float noise on a long leg

_CACHE: dict = {}


def corpus() -> tuple[dict, collections.Counter]:
    """The deduped corpus table, built once and shared by the tests that need it."""
    if "table" not in _CACHE:
        drops: collections.Counter = collections.Counter()
        links = atlas.links(load.PLAYER, drops, players={"zaksynack"})
        _CACHE["table"] = (atlas.corpus_table(links, drops), drops)
    return _CACHE["table"]


def _chain_csv(rows: list[dict]) -> str:
    """A geometry CSV holding exactly the given rows.

    bins/tests/synthetic.py cannot stand in here: it writes object times as
    1000 + position*250 while writing deltaTime as snap*300, so its times and its gaps
    describe different maps and every adjacency check in the chain layer rejects it.
    """
    header = ("replay,startTime,kind,signedAngle,lazerAngle,observedAngle,spacingRadii,"
              "minJumpRadii,deltaTime,requiredVelocity,aimErrorRadii,hitError,result,"
              "travelRadii,travelTime,exitSlackRadii")
    lines = [header]
    for r in rows:
        lines.append(",".join(str(r.get(k, "")) for k in (
            "replay", "startTime", "kind", "signedAngle", "lazerAngle", "observedAngle",
            "spacingRadii", "minJumpRadii", "deltaTime", "requiredVelocity",
            "aimErrorRadii", "hitError", "result", "travelRadii", "travelTime",
            "exitSlackRadii")))
    return "\n".join(lines) + "\n"


def _obs(start, angle_deg, spacing, delta, **kw):
    """An Observation carrying only what the chain layer and the cell key read."""
    from bins.schema import Observation
    fields = dict(replay="r.osr", player="p", beatmap_md5="m", era=None,
                  start_time=start, target="circle", from_slider="0",
                  angle=angle_deg, spacing=spacing,
                  velocity=(spacing / delta if delta else None), delta_time=delta,
                  snap="0.25", run_index=1, run_size=8, hit_error=0.0,
                  aim_error=0.0, result="Great")
    fields.update(kw)
    return Observation(**fields)


# ---- the invariant the whole artifact rests on -----------------------------------

def test_wedge_contains_every_instance():
    """Every instance on a slide lies inside the boundary that slide draws.

    If the wedge is ever put on the wrong leg, or the angle's sign convention is flipped
    somewhere between the schema and the canvas, this fails on the first slide. It is the
    single assertion that ties the drawing back to the binning.
    """
    table, _ = corpus()
    md5 = next(k[0] for k in table if k[0].startswith("a5492de7a342"))
    focus = {k: v for k, v in table.items() if k[0] == md5}
    slot = {k: i for i, k in enumerate(sorted(focus))}
    assert focus, "the focus map produced no turns"

    checked = 0
    for scheme in SCHEMES:
        built = atlas.build_scheme(scheme, focus, table, slot, 3, {})
        geom = built["geometry"]
        if geom["angle"] is None and geom["radius"] is None:
            continue
        for slide in built["slides"]:
            cell = tuple(slide["labels"])
            members = [k for k in focus
                       if cells.key(focus[k].obs, scheme) == cell
                       and focus[k].obs.target == slide["target"]
                       and focus[k].obs.from_slider == slide["fromSlider"]]
            assert len(members) == len(slide["idx"]), (
                f"{scheme} {slide['cell']}: {len(slide['idx'])} drawn, "
                f"{len(members)} in the cell")
            for key in members:
                link = focus[key]
                if geom["angle"] is not None:
                    lo, hi = slide["bounds"][geom["angle"]]
                    hi = 180.0 if hi is None else hi
                    # schema.Axis makes only the top edge inclusive, and 180 exactly is a
                    # straight turn, which is common rather than exotic.
                    assert lo <= link.obs.angle <= hi, (
                        f"{scheme} {slide['cell']}: angle {link.obs.angle} outside "
                        f"[{lo}, {hi}]")
                if geom["radius"] is not None:
                    lo, hi = slide["bounds"][geom["radius"]]
                    hi = math.inf if hi is None else hi
                    assert lo <= link.obs.spacing < hi, (
                        f"{scheme} {slide['cell']}: exit {link.obs.spacing} outside "
                        f"[{lo}, {hi})")
                checked += 1
    assert checked > 1000, f"only {checked} instances checked; the corpus looks empty"


def test_membership_matches_the_binning_package():
    """No slide shows an object the binning package would not put in that cell.

    test_wedge_contains_every_instance compares the deck against the chained table it was
    built from, which is self-consistent by construction. This compares it against the raw
    observation stream instead, which is what the findings table counts, so a chain rule
    that quietly changed which objects are in a bin would show up here and nowhere else.

    The reverse direction is allowed to differ: a turn needs the row before it to be its
    real neighbour, and the findings table asks no such question. What is not allowed is
    an object appearing on a slide whose cell it is not in.
    """
    table, _ = corpus()
    md5 = next(k[0] for k in table if k[0].startswith("a5492de7a342"))
    focus = {k: v for k, v in table.items() if k[0] == md5}
    slot = {k: i for i, k in enumerate(sorted(focus))}
    by_slot = {i: k for k, i in slot.items()}

    scheme = "angle-spacing-snap"
    theirs: dict = collections.defaultdict(set)
    for o in load.observations(load.PLAYER, maps={md5}, players={"zaksynack"}):
        cell = cells.key(o, scheme)
        if cell is not None:
            theirs[(o.target, o.from_slider, cell)].add(round(o.start_time, 3))

    built = atlas.build_scheme(scheme, focus, table, slot, 3, {})
    invented, dropped, drawn = 0, 0, 0
    for slide in built["slides"]:
        key = (slide["target"], slide["fromSlider"], tuple(slide["labels"]))
        mine = {by_slot[i][1] for i in slide["idx"]}
        invented += len(mine - theirs.get(key, set()))
        dropped += len(theirs.get(key, set()) - mine)
        drawn += len(mine)

    assert drawn > 500, f"only {drawn} instances drawn; the corpus looks empty"
    assert invented == 0, (
        f"{invented} objects are drawn on a slide whose cell they are not in")
    assert dropped <= drawn * 0.02, (
        f"{dropped} of {drawn + dropped} objects the binning names are missing from the "
        f"deck, which is more than chain rejection explains")


# ---- the differential harness ----------------------------------------------------

def test_canonical_frame_matches_scene_positions():
    """The frame built from the CSV is the frame lazer's own positions describe.

    And, just as importantly, the off-by-one frame is not. tools/viewer/pattern.py takes
    the apex to be object n when the row's apex is object n-1; the resulting picture is
    wrong in a way that looks fine, so this asserts the wrong reading is rejected.
    """
    docs = {os.path.basename(p)[:-5]: p for p in glob.glob(os.path.join(ROOT, "build",
                                                                       "scene", "*.json"))}
    if not docs:
        print("      SKIPPED: build/scene is empty; run 'ora scene' on a replay first")
        return

    drops: collections.Counter = collections.Counter()
    links = atlas.links(load.PLAYER, drops, players={"zaksynack"})
    by_replay: dict = collections.defaultdict(list)
    for link in links:
        by_replay[link.obs.replay].append(link)

    right, wrong, checked = [], [], 0
    for replay, group in by_replay.items():
        doc = docs.get(replay[:-4])
        if doc is None:
            continue
        scene = json.load(open(doc))
        objects, radius = scene["objects"], scene["beatmap"]["radius"]
        at = {round(o["t"], 0): i for i, o in enumerate(objects)}

        for link in group:
            n = at.get(round(link.obs.start_time, 0))
            if n is None or n < 2 or n + 1 >= len(objects):
                continue
            a, b, c = objects[n - 2], objects[n - 1], objects[n]
            if "slider" in (a["type"], b["type"]) or b["type"] == "spinner":
                continue

            entry, angle, exit_ = link.frame
            v1 = (a["x"] - b["x"], a["y"] - b["y"])
            v2 = (c["x"] - b["x"], c["y"] - b["y"])
            legs = (math.hypot(*v1), math.hypot(*v2))
            assert abs(legs[0] / radius - entry) < POSITION_EPS, (
                f"entry leg at {link.obs.start_time}")
            assert abs(legs[1] / radius - exit_) < POSITION_EPS, (
                f"exit leg at {link.obs.start_time}")
            tolerance = POSITION_ROUNDING / min(legs) + ANGLE_FLOOR
            right.append(_delta(math.atan2(v1[0] * v2[1] - v1[1] * v2[0],
                                           v1[0] * v2[0] + v1[1] * v2[1]),
                                angle) / tolerance)

            p, q, r = objects[n - 1], objects[n], objects[n + 1]
            w1 = (p["x"] - q["x"], p["y"] - q["y"])
            w2 = (r["x"] - q["x"], r["y"] - q["y"])
            wrong.append(_delta(math.atan2(w1[0] * w2[1] - w1[1] * w2[0],
                                           w1[0] * w2[0] + w1[1] * w2[1]), angle))
            checked += 1

    assert checked > 500, f"only {checked} chains had a scene document to check against"
    assert max(right) < 1.0, (
        f"angle diverges by {max(right):.2f}x what scene's own position rounding explains")

    agree = sum(1 for d in wrong if d < 1e-3) / len(wrong)
    assert agree < 0.10, (
        f"the off-by-one frame agrees on {agree:.0%} of chains, so this test cannot tell "
        f"the two apart and would not catch the bug it exists for")


def _delta(a: float, b: float) -> float:
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


# ---- the chain layer -------------------------------------------------------------

def test_chain_rejects_spinner_gaps():
    """A hole in the row stream must break the chain, not splice across it."""
    rows = [_obs(1000 + i * 100, 90.0, 2.0, 100.0) for i in range(5)]
    drops: collections.Counter = collections.Counter()
    assert len(atlas.chain(rows, drops)) == 4, "an unbroken run should chain end to end"

    holed = rows[:2] + rows[3:]            # object 2 removed, as a spinner would be
    drops = collections.Counter()
    linked = atlas.chain(holed, drops)
    assert drops["notAdjacent"] == 1, f"the hole was not detected: {dict(drops)}"
    assert all(l.obs.start_time != 1300 for l in linked), "chained across the hole"


def test_chain_rejects_zero_length_legs():
    """A zero leg is a missing measurement, not a very tight jump.

    It reaches the CSV either because a spinner came before, so lazer never set the
    distance, or because two objects sit at exactly the same point. Both carry a bearing
    derived from nothing into whichever angle cell that bearing happens to name.
    """
    rows = [_obs(1000, 90.0, 2.0, 100.0), _obs(1100, 90.0, 0.0, 100.0),
            _obs(1200, 90.0, 2.0, 100.0)]
    drops: collections.Counter = collections.Counter()
    linked = atlas.chain(rows, drops)
    # the zero is both row 2's exit leg and row 3's entry leg, so it kills both turns
    assert drops["zeroExitLeg"] == 1, dict(drops)
    assert drops["zeroEntryLeg"] == 1, dict(drops)
    assert linked == []


def test_rate_is_inferred_under_dt():
    """Object times are map times; deltaTime is the gap over the clock rate."""
    plain = [_obs(1000 + i * 150, 90.0, 2.0, 150.0) for i in range(6)]
    assert abs(atlas.rate(plain) - 1.0) < 1e-9

    doubled = [_obs(1000 + i * 150, 90.0, 2.0, 100.0) for i in range(6)]
    assert abs(atlas.rate(doubled) - 1.5) < 1e-9, atlas.rate(doubled)

    drops: collections.Counter = collections.Counter()
    assert len(atlas.chain(doubled, drops)) == 5, (
        f"rate-modded rows were rejected as non-adjacent: {dict(drops)}")


def test_delta_floor_is_not_used_as_evidence():
    """At the 25ms clamp deltaTime no longer records the gap, so it cannot prove a pair."""
    rows = [_obs(1000, 90.0, 2.0, 150.0), _obs(1010, 90.0, 2.0, atlas.DELTA_FLOOR),
            _obs(1020, 90.0, 2.0, atlas.DELTA_FLOOR)]
    drops: collections.Counter = collections.Counter()
    assert len(atlas.chain(rows, drops)) == 2, dict(drops)


# ---- the page --------------------------------------------------------------------

def test_payload_inlines_and_parses():
    payload = {"schemaVersion": 3, "note": "</script> must not close the tag"}
    with tempfile.TemporaryDirectory() as work:
        out = os.path.join(work, "out.html")
        saved, atlas.TEMPLATE = atlas.TEMPLATE, os.path.join(work, "t.html")
        try:
            with open(atlas.TEMPLATE, "w") as handle:
                handle.write('<script id="payload">/*__PAYLOAD__*/</script>')
            atlas.write(payload, out)
            html = open(out).read()
            assert "/*__PAYLOAD__*/" not in html, "the sentinel survived"
            assert "</script> must not" not in html, "an unescaped </ would close the tag"
            body = html[html.index(">") + 1:html.rindex("</script>")]
            assert json.loads(body.replace(r"<\/", "</")) == payload

            with open(atlas.TEMPLATE, "w") as handle:
                handle.write("<p>no slot here</p>")
            try:
                atlas.write(payload, out)
            except SystemExit:
                pass
            else:
                assert False, "a template with no slot produced a page anyway"
        finally:
            atlas.TEMPLATE = saved


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0

    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")
        else:
            print(f"ok    {test.__name__}")

    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
