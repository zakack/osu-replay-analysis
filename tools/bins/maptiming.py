"""The map's own timing error, so a finding sitting on it can be discounted.

Every finding this package produces says "your timing is worse in this class of pattern",
and every one of them rests on the assumption that the object's nominal time is the time
the music is at. Maps are hand-snapped and that assumption fails constantly: a section
snapped a frame off its own audio makes a whole leaderboard hit early there, and a cell
that happens to draw its observations from that section inherits the offset as if it were
the player's.

So the reference corpus is asked what *it* did at each object. Where a whole
leaderboard agrees on a signed offset, the offset is the map. What is left after
removing it is the part that can be about the person.

Two levels of median, never one: each contributing player's own median at the object,
then the median across players. A player with three replays of a map would otherwise get
three votes on whether that map is mistimed, and the maps with the most replays are
exactly the popular ones a single grinder farms.
"""
from __future__ import annotations

import csv
import statistics
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace

from .schema import MAP_BIAS_COLUMNS, SCHEMA_VERSION, Observation

# A window narrower than this is one player's bad night wearing a section's clothes. The
# claim being made is "the mapper snapped a run wrong", and a run is not two objects.
MIN_SECTION_OBJECTS = 3


def _key(beatmap_md5: str, start_time: float) -> tuple[str, float]:
    """The join key across players, rounded in exactly one place.

    ``start_time`` is a whole millisecond that survived a float round trip through the
    CSV, so it is the same value for every player only after rounding. Every function
    here goes through this helper; a second rounding rule elsewhere would not raise, it
    would just silently find no reference players for any object.
    """
    return (beatmap_md5, round(start_time, 3))


def _iqr(values: list[float]) -> float:
    """Plain quartile difference — the median of each half, middle element in neither.

    Not a percentile interpolation and not scipy: this number is read as "do the players
    agree", and at the sample sizes involved the choice of quartile convention moves it
    far less than one more player would.
    """
    xs = sorted(values)
    half = len(xs) // 2
    if half == 0:
        return 0.0
    return statistics.median(xs[len(xs) - half:]) - statistics.median(xs[:half])


@dataclass(frozen=True)
class ObjectBias:
    beatmap_md5: str
    start_time: float
    n: int              # distinct reference players contributing, not observations
    median: float       # ms, signed; negative is the leaderboard hitting early
    iqr: float          # ms; the spread of the leaderboard's own agreement


def object_bias(observations: Iterable[Observation], *, min_players: int = 5
                ) -> dict[tuple[str, float], ObjectBias]:
    """The reference corpus's own hit error at each object of each beatmap.

    Counted per player, not per observation: one player with three replays of a map must
    not get three votes on whether that map is mistimed.

    Misses arrive with ``hit_error`` of ``None`` and drop out on their own; Meh and Ok
    judgements are kept, because a click near the window edge is still evidence about
    where the music is, and the two-level median is what absorbs its tail.

    One pass over ``observations`` — it may be a generator, and on the reference side it
    is roughly a million rows. The per-player lists are dropped as each object's medians
    are taken rather than at the end, so peak memory is the grouping, not the grouping
    plus the result.
    """
    groups: dict[tuple[str, float], dict[str, list[float]]] = {}
    for o in observations:
        if o.hit_error is None:
            continue
        groups.setdefault(_key(o.beatmap_md5, o.start_time), {}) \
              .setdefault(o.player, []).append(o.hit_error)

    bias: dict[tuple[str, float], ObjectBias] = {}
    while groups:
        (md5, start), players = groups.popitem()
        if len(players) < min_players:
            continue
        per_player = [statistics.median(errors) for errors in players.values()]
        players.clear()
        bias[(md5, start)] = ObjectBias(md5, start, len(per_player),
                                        statistics.median(per_player), _iqr(per_player))
    return bias


def detrend(observations: Iterable[Observation],
            bias: dict[tuple[str, float], ObjectBias]) -> Iterator[Observation]:
    """Yield the same observations with the map's own bias removed from hit_error.

    An observation on an object with no bias estimate passes through unchanged. With no
    evidence of map bias the honest estimate of map bias is zero, and that is what
    subtracting nothing means. Dropping instead would make every downstream cell count
    depend on which objects the leaderboard happened to cover — popular maps, early
    sections, the parts people can pass — which is a selection effect dressed as a
    measurement, and a worse error than the one being corrected.

    Within one object only the location moves, exactly: a single constant off every
    observation leaves the spread bit-for-bit unchanged. Across a cell it is a different
    constant per object, so a cell's spread does move — by however much the estimates
    vary between objects, plus the estimates' own noise, which on this corpus is a
    standard error of about 3ms per object. That noise is added to the player side and
    subtracted from the reference side, which detrends against estimates it is itself
    inside of, so a ratio-of-spreads metric picks up a small systematic lean towards
    "player worse". Whether to detrend spread findings at all is ``compare``'s call.
    """
    for o in observations:
        if o.hit_error is None:
            yield o
            continue
        b = bias.get(_key(o.beatmap_md5, o.start_time))
        yield o if b is None else replace(o, hit_error=o.hit_error - b.median)


@dataclass(frozen=True)
class MapVerdict:
    beatmap_md5: str
    objects: int          # objects with a bias estimate
    biased: int           # of those, how many exceed the threshold
    fraction: float
    median_abs_bias: float
    worst_section: tuple[float, float, float] | None   # (start ms, end ms, median bias)


def _worst_section(objects: list[ObjectBias], threshold_ms: float,
                   section_ms: float) -> tuple[float, float, float] | None:
    """The window with the largest absolute median bias, over objects sorted by time.

    Every object anchors one window, so on a real mistimed run a dozen windows tie within
    noise. The ordering is therefore made total: largest absolute median, then most
    objects over the threshold, then earliest — otherwise the same corpus would name a
    different section on a rerun and the regression suite could not assert anything.
    """
    best: tuple[tuple[float, int, float], tuple[float, float, float]] | None = None
    hi = 0
    for lo in range(len(objects)):
        start = objects[lo].start_time
        while hi < len(objects) and objects[hi].start_time < start + section_ms:
            hi += 1
        window = objects[lo:hi]
        if len(window) < MIN_SECTION_OBJECTS:
            continue
        med = statistics.median([b.median for b in window])
        rank = (abs(med), sum(1 for b in window if abs(b.median) > threshold_ms), -start)
        if best is None or rank > best[0]:
            best = (rank, (start, start + section_ms, med))
    return None if best is None else best[1]


def map_verdicts(bias: dict[tuple[str, float], ObjectBias], *, threshold_ms: float = 5.0,
                 section_ms: float = 4000.0) -> list[MapVerdict]:
    """Which beatmaps are mistimed, and where. The worst section is the contiguous
    window of ``section_ms`` whose objects share the largest absolute median bias — a
    single bad object is noise, a whole run of them is a mapping error.

    Every beatmap with at least one estimate gets a verdict, worst first. Where the line
    between "mistimed" and "hand-snapped like everything else" falls is a presentation
    decision and belongs to the caller, so nothing is filtered out here; ``biased``
    counts objects strictly above ``threshold_ms`` in absolute value.

    The window is nominal — ``[anchor, anchor + section_ms)`` from the object that starts
    it, not the span of the objects inside it — so that two sections reported from
    different maps are the same width and can be compared.
    """
    by_map: dict[str, list[ObjectBias]] = {}
    for b in bias.values():
        by_map.setdefault(b.beatmap_md5, []).append(b)

    verdicts = []
    for md5, objects in by_map.items():
        objects.sort(key=lambda b: b.start_time)
        biased = sum(1 for b in objects if abs(b.median) > threshold_ms)
        verdicts.append(MapVerdict(
            md5, len(objects), biased, biased / len(objects),
            statistics.median([abs(b.median) for b in objects]),
            _worst_section(objects, threshold_ms, section_ms)))

    verdicts.sort(key=lambda v: (-v.fraction, -v.median_abs_bias, v.beatmap_md5))
    return verdicts


def write(bias: dict[tuple[str, float], ObjectBias], destination: str) -> None:
    """Write the map-bias CSV, columns exactly ``schema.MAP_BIAS_COLUMNS``, header
    included. Rows are sorted by the join key so two runs over the same corpus produce
    byte-identical files and a diff means the corpus changed.
    """
    with open(destination, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(MAP_BIAS_COLUMNS)
        for key in sorted(bias):
            b = bias[key]
            out.writerow([SCHEMA_VERSION, b.beatmap_md5, f"{b.start_time:.3f}", b.n,
                          f"{b.median:.4f}", f"{b.iqr:.4f}"])
