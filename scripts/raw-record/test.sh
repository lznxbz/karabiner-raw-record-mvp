#!/bin/bash
set -euo pipefail
repo_dir=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo_dir"
if [[ ${1:-} != --prepared ]]; then
    exec bash scripts/raw-record/build.sh test
fi
for suite in raw_record hid_report_only_events manipulator_basic manipulator post_event_to_virtual_devices types event_queue core_configuration; do
    echo "Testing $suite"
    cmake -S "tests/src/$suite" -B "tests/src/$suite/build"
    cmake --build "tests/src/$suite/build" -j "${RAW_RECORD_BUILD_JOBS:-2}"
    (cd "tests/src/$suite" && MallocNanoZone=0 ./build/karabiner_test)
done
python3 -m unittest discover -s scripts/raw-record -p 'test_*.py'
echo "All selected software tests passed. Physical hardware validation is separate."
