
#!/usr/bin/env bash

set -e

REMOTE="open103"
REMOTE_DIR="~/workspace/xs-env/XiangShan"
LOCAL_DIR="./bosc"

function show_help() {
  echo "Usage: $0 [--sync all|wave|db|scripts] [--gc]"
  exit 1
}

SYNC_MODE="all"
RUN_GC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sync)
      if [[ -n "$2" ]]; then
        SYNC_MODE="$2"
        shift
      else
        show_help
      fi
      ;;
    --gc)
      RUN_GC=1
      ;;
    --help|-h)
      show_help
      ;;
    *)
      show_help
      ;;
  esac
  shift
done

function garbage_collect() {
  local wave_dir="$LOCAL_DIR/wave"
  local referenced_fst=$(grep -h '^\[dumpfile\]' "$wave_dir"/*.gtkw 2>/dev/null | sed -n 's/.*"\([^"]*\.fst\)".*/\1/p' | xargs -n1 basename)

  for fst in "$wave_dir"/*.fst; do
    [[ -f "$fst" ]] || continue
    local fst_name=$(basename "$fst")
    if ! echo "$referenced_fst" | grep -qx "$fst_name"; then
      rm -i "$fst"
    fi
  done
}

if [[ $RUN_GC -eq 1 ]]; then
  garbage_collect
  exit 0
fi

function sync_wave() {
  mkdir -p "$LOCAL_DIR/wave"
  rsync -avz -e "ssh -o RemoteCommand=none" \
    --progress \
    --include '*/' --include '*.fst' --exclude '*' \
    "$REMOTE:$REMOTE_DIR/build/" \
    "$LOCAL_DIR/wave/"
}

function sync_db() {
  mkdir -p "$LOCAL_DIR/db"
  rsync -avz -e "ssh -o RemoteCommand=none" \
    --progress \
    --include '*/' --include '*.db' --exclude '*' \
    "$REMOTE:$REMOTE_DIR/build/" \
    "$LOCAL_DIR/db/"
}

function sync_scripts() {
  mkdir -p "$LOCAL_DIR/scripts"
  rsync -avz -e "ssh -o RemoteCommand=none" \
    --progress \
    "$REMOTE:$REMOTE_DIR/tmp/" \
    "$LOCAL_DIR/scripts/"
}

if [[ "$SYNC_MODE" == "all" ]]; then
  sync_wave
  sync_db
  sync_scripts
elif [[ "$SYNC_MODE" == "wave" ]]; then
  sync_wave
elif [[ "$SYNC_MODE" == "db" ]]; then
  sync_db
elif [[ "$SYNC_MODE" == "scripts" ]]; then
  sync_scripts
else
  show_help
fi
