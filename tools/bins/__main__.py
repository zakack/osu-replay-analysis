"""The binning layer's command line.

Three commands, and the ordering between them matters:

    map-bias   what the reference corpus itself does at each object of each beatmap
    findings   the table: every cell, every metric, player against reference
    scheme     the bin edges in force, for reading alongside a findings file

`findings --detrend` consumes what `map-bias` produces. Run it that way by default,
because an undetrended finding cannot tell a player who is late through a section from
a section that everybody is late through.
"""
from __future__ import annotations

import argparse
import sys
import time
from . import cells, compare, load, maptiming, stats
from .schema import (CONTROLS, FINDINGS_COLUMNS, MIN_N_PLAYER, MIN_N_REFERENCE,
                     SCHEMA_VERSION, SCHEMES)

# Metrics that survive having a per-object constant subtracted from them. A mean does; a
# standard deviation does not, because the constant is an estimate and its noise is not
# shared evenly between the two sides. See compare.compare.
LOCATION_METRICS = ("hitMean",)


def _sources(args):
    """Both sides, restricted to what they have in common.

    Two restrictions, and leaving either one out quietly changes what is being measured.
    Only beatmaps both sides played, or one library is being compared against another
    person's single good run. And only the subject on the player side: the local corpus
    holds imported replays from thirty-four other players, which would otherwise be
    averaged into "you".
    """
    shared = load.shared_maps(load.PLAYER, load.REFERENCE_NM)
    print(f"shared beatmaps {len(shared)}, subject {args.player!r}", file=sys.stderr)
    return shared


def map_bias(args) -> int:
    shared = _sources(args)

    started = time.monotonic()
    bias = maptiming.object_bias(load.observations(load.REFERENCE_NM, maps=shared),
                                 min_players=args.min_players)
    maptiming.write(bias, args.out)

    verdicts = maptiming.map_verdicts(bias, threshold_ms=args.threshold)
    biased = [v for v in verdicts if v.fraction > 0.1]

    print(f"objects with a bias estimate {len(bias)}  "
          f"({time.monotonic() - started:.0f}s)")
    print(f"beatmaps: {len(verdicts)}, of which {len(biased)} have more than a tenth of "
          f"their objects biased past {args.threshold:g}ms")
    print(f"\n  {'beatmap':<14}{'objects':>9}{'biased':>8}{'frac':>7}"
          f"{'|bias|':>8}  worst section")

    for v in sorted(verdicts, key=lambda v: -v.fraction)[:15]:
        section = (f"{v.worst_section[0] / 1000:.0f}-{v.worst_section[1] / 1000:.0f}s "
                   f"{v.worst_section[2]:+.1f}ms" if v.worst_section else "--")
        print(f"  {v.beatmap_md5[:12]:<14}{v.objects:>9}{v.biased:>8}{v.fraction:>7.2f}"
              f"{v.median_abs_bias:>8.2f}  {section}")

    print(f"\nwritten: {args.out}")
    return 0


def findings(args) -> int:
    shared = _sources(args)
    schemes = list(SCHEMES) if args.scheme == "all" else [args.scheme]

    # Every control a requested scheme needs is aggregated too, even when it was not
    # asked for. A row without its control is a row that cannot be read.
    needed = set(schemes) | {CONTROLS[s][0] for s in schemes if s in CONTROLS}
    keyers = {name: (lambda o, s=name: cells.key(o, s)) for name in sorted(needed)}

    bias = {}
    if args.detrend:
        started = time.monotonic()
        estimated = maptiming.object_bias(load.observations(load.REFERENCE_NM, maps=shared))

        # Only well-determined estimates are worth subtracting. At the median depth of
        # fourteen players the per-object standard error is about three milliseconds, so
        # a small estimate is mostly its own noise and removing it adds more error than
        # it takes away. Keep the ones that are deep and large enough to be real.
        bias = {k: b for k, b in estimated.items()
                if b.n >= args.bias_players and abs(b.median) >= args.bias_ms}

        print(f"map bias: {len(estimated)} objects estimated, {len(bias)} confident "
              f"enough to subtract ({time.monotonic() - started:.0f}s)", file=sys.stderr)

    def aggregate(source, detrended, **restrict):
        started = time.monotonic()
        observations = load.observations(source, maps=shared, **restrict)
        if detrended:
            observations = maptiming.detrend(observations, bias)
        tables = stats.aggregate_many(observations, keyers)
        print(f"  {source.name} pass{' (detrended)' if detrended else ''} "
              f"{time.monotonic() - started:.0f}s", file=sys.stderr)
        return tables

    # One pass per side per detrending, every scheme at once. Four hundred megabytes of
    # reference CSV is worth reading as few times as possible.
    passes = [(False, None)] + ([(True, LOCATION_METRICS)] if bias else [])
    rows, skipped = [], {}

    for detrended, metrics in passes:
        reference_tables = aggregate(load.REFERENCE_NM, detrended)
        player_tables = aggregate(load.PLAYER, detrended, players={args.player})

        for scheme in schemes:
            control = CONTROLS.get(scheme)
            tables = ((player_tables[control[0]], reference_tables[control[0]])
                      if control else None)

            found = compare.compare(player_tables[scheme], reference_tables[scheme],
                                    scheme=scheme, detrended=detrended,
                                    min_n_player=args.min_n_player,
                                    min_n_reference=args.min_n_reference,
                                    metrics=metrics, control=tables, skipped=skipped)
            rows.extend(found)
            print(f"  {scheme:<20}{'detrended' if detrended else 'raw':<10}"
                  f"{len(player_tables[scheme]):>7} player cells "
                  f"{len(reference_tables[scheme]):>7} reference  -> {len(found):>6} rows")

    compare.write(rows, args.out)

    # A short table is not the same thing as a clean one, so say what did not make it.
    if skipped:
        print("\nnot compared:")
        for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>7}  {reason}")

    print(f"\nschema {SCHEMA_VERSION}, {len(rows)} rows, detrended={bool(bias)}")
    print(f"written: {args.out}")
    return 0


def scheme(args) -> int:
    print(f"schema version {SCHEMA_VERSION}")
    print(f"min n: player {MIN_N_PLAYER}, reference {MIN_N_REFERENCE}")

    for name, axes in SCHEMES.items():
        control = CONTROLS.get(name)
        print(f"\n{name}" + (f"   controlled by {control[0]}" if control else ""))
        for axis in axes:
            print(f"  {axis.name:<10}{axis.unit:<16}{' '.join(axis.labels)}")
        print(f"  cells: {len(list(cells.enumerate_cells(name)))}")

    print(f"\nfindings columns: {', '.join(FINDINGS_COLUMNS)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bins", description=__doc__)
    parser.add_argument("--player", default="zaksynack",
                        help="the subject; the local corpus also holds other people's "
                             "imported replays and they are not you")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("map-bias", help="per-object reference hit error")
    b.add_argument("--out", default="build/map-bias.csv")
    b.add_argument("--min-players", type=int, default=5)
    b.add_argument("--threshold", type=float, default=5.0)
    b.set_defaults(run=map_bias)

    f = sub.add_parser("findings", help="the findings table")
    f.add_argument("--out", default="build/findings.csv")
    f.add_argument("--scheme", default="all", choices=["all", *SCHEMES])
    f.add_argument("--detrend", action="store_true",
                   help="also emit rows with each map's own reference bias removed")
    f.add_argument("--bias-players", type=int, default=20,
                   help="reference players an object needs before its bias is subtracted")
    f.add_argument("--bias-ms", type=float, default=5.0,
                   help="smallest bias worth subtracting; below this it is mostly noise")
    f.add_argument("--min-n-player", type=int, default=MIN_N_PLAYER)
    f.add_argument("--min-n-reference", type=int, default=MIN_N_REFERENCE)
    f.set_defaults(run=findings)

    s = sub.add_parser("scheme", help="the bin edges in force")
    s.set_defaults(run=scheme)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
