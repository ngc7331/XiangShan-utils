#!/usr/bin/env bash

set -euo pipefail

if [ ! -f Makefile ] || [ ! -f build.mill ]; then
  echo "Please run this script from the root of XiangShan repository."
  exit 1
fi

source ../env.sh >/dev/null

RUN_NUM=${RUN_NUM:-3}
THREADS=${THREADS:-16}
CLEANUP=${CLEANUP:-false}

# prepare
if [ "$CLEANUP" = true ]; then
  echo "Cleanup..."
  make clean 2>/dev/null >/dev/null
fi

echo "Prepare verilog files..."
make sim-verilog 2>/dev/null >/dev/null

export CCACHE_DISABLE=1

echo "Running tests with $RUN_NUM runs..."
VERILATOR_USER_TIME=()
VERILATOR_WALL_TIME=()
CXX_USER_TIME=()
CXX_WALL_TIME=()
RUN_TIME=()

function get_user_time() {
  local step=$1
  grep "\[$step\]" build/time.log -A 3 | tail -n 1 | awk '{print $4}'
}

function get_wall_time() {
  local step=$1
  local str=$(grep "\[$step\]" build/time.log -A 6 | tail -n 1 | awk '{print $8}')
  if [[ $str == *:* ]]; then
    local minutes=$(echo $str | cut -d: -f1)
    local seconds=$(echo $str | cut -d: -f2)
    local total_seconds=$(echo "scale=2; ($minutes * 60) + $seconds" | bc)
  else
    local total_seconds=$str
  fi
  echo $total_seconds
}

function get_run_time() {
  local run_time=$(grep "Host time spent:" build/run.log | awk '{print $4}' | tr -d 'ms' | tr -d ',')
  # convert to seconds
  run_time=$(echo "scale=2; $run_time / 1000" | bc)
  echo $run_time
}

for i in $(seq 1 $RUN_NUM); do
  rm -rf build/verilator-compile
  rm -f build/emu
  rm -f build/time.log

  echo "Test compile $i..."
  make emu -j$THREADS EMU_THREADS=8 2>/dev/null >/dev/null

  __VERILATOR_USER_TIME=$(get_user_time "verilator")
  __VERILATOR_WALL_TIME=$(get_wall_time "verilator")
  __CXX_USER_TIME=$(get_user_time "c++")
  __CXX_WALL_TIME=$(get_wall_time "c++")
  VERILATOR_USER_TIME+=($__VERILATOR_USER_TIME)
  VERILATOR_WALL_TIME+=($__VERILATOR_WALL_TIME)
  CXX_USER_TIME+=($__CXX_USER_TIME)
  CXX_WALL_TIME+=($__CXX_WALL_TIME)
  echo "  -> Verilator user time: $__VERILATOR_USER_TIME seconds"
  echo "  -> Verilator wall time: $__VERILATOR_WALL_TIME seconds"
  echo "  -> C++ compilation user time: $__CXX_USER_TIME seconds"
  echo "  -> C++ compilation wall time: $__CXX_WALL_TIME seconds"

  echo "Test run $i..."
  python3 scripts/xiangshan.py --numa --threads 8 ready-to-run/microbench.bin 2>/dev/null >build/run.log

  __RUN_TIME=$(get_run_time)
  RUN_TIME+=($__RUN_TIME)
  echo "  -> Test run time: $__RUN_TIME seconds"
done

echo "=== Summary ==="
echo "Verilator version: $(verilator --version | head -n 1)"

# get clang version that verilator uses
function get_clang_version() {
  local verilator_mkfile=$(verilator --getenv VERILATOR_ROOT)/include/verilated.mk
  local clang=$(grep "CXX = " $verilator_mkfile | awk '{print $3}')
  $clang --version | head -n 1
}
echo "Clang version: $(get_clang_version)"

# calculate average & standard deviation
function calculate_stats() {
  local times=("$@")
  local sum=0
  local sum_of_squares=0
  local count=${#times[@]}

  for time in "${times[@]}"; do
    sum=$(echo "$sum + $time" | bc)
    sum_of_squares=$(echo "$sum_of_squares + ($time * $time)" | bc)
  done

  local mean=$(echo "scale=2; $sum / $count" | bc)
  local variance=$(echo "scale=2; ($sum_of_squares / $count) - ($mean * $mean)" | bc)
  local stddev=$(echo "scale=2; sqrt($variance)" | bc)

  echo "  Times: ${times[*]}"
  echo "  Average: $mean seconds"
  echo "  Standard Deviation: $stddev seconds"
}

echo "Verilator compilation user time:"
calculate_stats "${VERILATOR_USER_TIME[@]}"
echo "Verilator compilation wall time::"
calculate_stats "${VERILATOR_WALL_TIME[@]}"
echo "C++ compilation time user time:"
calculate_stats "${CXX_USER_TIME[@]}"
echo "C++ compilation time wall time:"
calculate_stats "${CXX_WALL_TIME[@]}"
echo "EMU run time:"
calculate_stats "${RUN_TIME[@]}"
