#!/usr/bin/env bash
set -uo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUT="$ROOT/artifacts/validation_llm_adjudication_claude_v1"
RAW="$OUT/raw"
FAILED="$OUT/failed_raw"
LOG="$OUT/RESUME_EXECUTION_LOG.txt"
mkdir -p "$FAILED"
printf '%s resume_started\n' "$(date --iso-8601=seconds)" >> "$LOG"

for number in 02 03 04 05; do
  current="$RAW/batch_${number}.json"
  archived="$FAILED/batch_${number}_attempt1_session_limit.json"
  if [[ ! -s "$current" ]]; then
    printf '%s batch_%s missing_session_limit_wrapper\n' "$(date --iso-8601=seconds)" "$number" >> "$LOG"
    continue
  fi
  if ! python3 - "$current" <<'PY'
import json, sys
x=json.load(open(sys.argv[1]))
ok=x.get('is_error') is True and "session limit" in x.get('result','').lower()
raise SystemExit(0 if ok else 1)
PY
  then
    printf '%s batch_%s existing_output_not_session_limit; refusing_retry\n' "$(date --iso-8601=seconds)" "$number" >> "$LOG"
    continue
  fi
  if [[ -e "$archived" ]]; then
    printf '%s batch_%s archive_exists; refusing_overwrite\n' "$(date --iso-8601=seconds)" "$number" >> "$LOG"
    continue
  fi
  mv "$current" "$archived"
  printf '%s batch_%s retry_started\n' "$(date --iso-8601=seconds)" "$number" >> "$LOG"
  if "$ROOT/scripts/run_claude_blind_adjudication_v1.sh" "batches/batch_${number}.json"; then
    printf '%s batch_%s retry_exit_0\n' "$(date --iso-8601=seconds)" "$number" >> "$LOG"
  else
    code=$?
    printf '%s batch_%s retry_exit_%s\n' "$(date --iso-8601=seconds)" "$number" "$code" >> "$LOG"
  fi
done

printf '%s resume_finished\n' "$(date --iso-8601=seconds)" >> "$LOG"
