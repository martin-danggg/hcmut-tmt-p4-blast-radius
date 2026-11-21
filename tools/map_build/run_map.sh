#!/bin/bash
# Build the coverage map from the pinned sqlglot commit (run offline, result committed as map/map.pkl.xz).
#   SG=/path/to/sqlglot@eb11aec  (with `pip install -r requirements.lock`, Python 3.13)
#   bash tools/map_build/run_map.sh /tmp/map_traces
# Each test file runs in its own pytest process; each test runs in its own forked child so
# lazily-initialised state is attributed to every test that needs it. Spawned pool workers
# are traced through sitecustomize.py.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$1; mkdir -p "$OUT"
cd "$SG"
find tests -name 'test_*.py' | sort > "$OUT/files.txt"
run() { f=$1; d=$OUT/$(echo $f | tr '/' '_'); BR_ROOT=$PWD BR_OUT=$d SKIP_INTEGRATION=0 \
  PYTHONPATH=$HERE:. python -m pytest -q -p no:cacheprovider -p br_trace "$f" > $d.log 2>&1 || true; }
export -f run; export OUT HERE
xargs -P 2 -I{} bash -c 'run {}' < "$OUT/files.txt"
python "$HERE/aggregate2.py" "$OUT" "$OUT/agg.pkl"
python "$HERE/build_map.py" "$OUT/agg.pkl" "$HERE/../../map/map.pkl.xz"
