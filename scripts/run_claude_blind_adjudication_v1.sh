#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
: "${CERTCF_ADJUDICATION_ROOT:?Set the directory containing blinded packets and batch manifests}"
: "${CERTCF_ADJUDICATION_PROMPT:?Set the path to the adjudication prompt file}"
ISO=$CERTCF_ADJUDICATION_ROOT
OUT="$ROOT/artifacts/validation_llm_adjudication_claude_v1"
MODEL=claude-sonnet-4-6

if [[ $# -ne 1 ]]; then
  echo "usage: $0 BATCH_JSON" >&2
  exit 2
fi

BATCH=$1
BASE=$(basename "$BATCH" .json)
RAW="$OUT/raw/${BASE}.json"
[[ -e "$RAW" ]] && { echo "refusing overwrite: $RAW" >&2; exit 3; }

cd "$ISO"
PROMPT=$(cat "$CERTCF_ADJUDICATION_PROMPT")
PROMPT+=$'\n\nAssigned batch manifest: '
PROMPT+="$BATCH"
PROMPT+=$'\nAdjudicator ID: '
PROMPT+="${MODEL}_${BASE}"

mkdir -p "$OUT/raw"
claude --print \
  --safe-mode \
  --restricted \
  --no-session-persistence \
  --permission-mode dontAsk \
  --permission-prompts none \
  --model "$MODEL" \
  --effort high \
  --max-budget-usd 6 \
  --allowedTools 'Read,Glob,Grep,Bash(sqlite3 *)' \
  --output-format json \
  "$PROMPT" > "$RAW"
