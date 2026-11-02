#!/bin/bash
# Run the headless tests in a fresh Blender each.
#   tests/run.sh <site-packages with pyluxcore> [test_name ...]
# BLENDER selects the Blender executable (default: blender in PATH).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
site="${1:?usage: tests/run.sh <site-packages with pyluxcore> [test_name ...]}"
shift
blender="${BLENDER:-blender}"

if [ $# -gt 0 ]; then
    tests=("$@")
else
    tests=()
    for f in "$here"/test_*.py; do tests+=("$(basename "$f" .py)"); done
fi

failed=()
for t in "${tests[@]}"; do
    log="$(mktemp --suffix=".$t.log")"
    if "$blender" -b --factory-startup --python-exit-code 1 --python "$here/$t.py" -- "$site" > "$log" 2>&1; then
        grep -E "^(OK|FAIL) " "$log"
        echo "$t: PASS"
        rm -f "$log"
    else
        grep -E "^(OK|FAIL) |Traceback|Error" "$log"
        echo "$t: FAIL (log: $log)"
        failed+=("$t")
    fi
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "FAILED: ${failed[*]}"
    exit 1
fi
echo "All ${#tests[@]} tests passed"
