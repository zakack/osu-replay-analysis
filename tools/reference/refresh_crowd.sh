#!/bin/bash
# After a reference pull: extend the no-mod corpus, rebuild the per-object crowd timing,
# and rebuild where.py's cache so hitVsCrowd picks up the new coverage. Waits for any
# fetch.py still running, so it can be started alongside one and left alone. Long: the
# geometry and rhythm extractors run on one thread, and a column change in either forces
# a full re-extraction of the reference corpus.
#
#     nohup nice -n 10 tools/reference/refresh_crowd.sh > build/refresh-crowd.log 2>&1 &
set -euo pipefail
cd "$(dirname "$0")/../.."

while pgrep -f 'tools/reference/fetch.py' >/dev/null; do sleep 60; done
echo "fetch finished $(date -Iseconds)"

tools/reference/extend_nm.sh
echo "reference-nm extended $(date -Iseconds)"

PYTHONPATH=tools python3 -m bins map-bias
python3 tools/analysis/where.py reduce
echo "done $(date -Iseconds)"
