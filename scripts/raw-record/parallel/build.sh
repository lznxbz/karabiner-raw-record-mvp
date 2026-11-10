#!/bin/bash
set -euo pipefail
tools_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$tools_dir/../../.." && pwd)
: "${PQRS_ORG_CODE_SIGN_IDENTITY:?Set the valid application signing identity hash.}"
[[ $(uname -s) == Darwin ]] || { echo "macOS is required." >&2; exit 1; }
security find-identity -v -p codesigning | grep -F "$PQRS_ORG_CODE_SIGN_IDENTITY" >/dev/null || {
    echo "The application signing identity is not valid." >&2; exit 1;
}
cd "$repo_dir"
git submodule update --init --recursive
cmake -S vendor -B vendor/build
cmake --build vendor/build
mkdir -p .raw-record-local
build_dir=$(mktemp -d "$repo_dir/.raw-record-local/parallel.XXXXXX")
python3 "$tools_dir/prepare.py" "$build_dir/source" --repo "$repo_dir"
export PQRS_ORG_INSTALLER_CODE_SIGN_IDENTITY=LOCAL_DEVELOPMENT_APP_ONLY
(cd "$build_dir/source" && ruby scripts/reduce-logs.rb 'make build')
python3 -m unittest discover -s "$tools_dir" -p 'test_*.py'
probe="$build_dir/source/tests/src/parallel_namespace"
mkdir -p "$probe/src"
cp "$tools_dir/namespace_probe.cpp" "$probe/src/test.cpp"
cat > "$probe/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.24 FATAL_ERROR)
include(../../tests.cmake)
project(parallel_namespace_probe)
add_executable(parallel_namespace_probe src/test.cpp)
target_link_libraries(parallel_namespace_probe libduktape "-framework CoreFoundation" "-framework CoreGraphics")
CMAKE
cmake -S "$probe" -B "$probe/build"
cmake --build "$probe/build" -j "${RAW_RECORD_BUILD_JOBS:-2}"
MallocNanoZone=0 "$probe/build/parallel_namespace_probe"
raw="$build_dir/source/tests/src/raw_record"
cmake -S "$raw" -B "$raw/build"
cmake --build "$raw/build" -j "${RAW_RECORD_BUILD_JOBS:-2}"
(cd "$raw" && MallocNanoZone=0 ./build/karabiner_test)
python3 "$tools_dir/package.py" "$build_dir/source" "$build_dir/package" --identity "$PQRS_ORG_CODE_SIGN_IDENTITY"
echo "Isolated local-development package and manifest: $build_dir/package"
echo "No application was installed and no running service was changed."
