#!/usr/bin/env bash
# Pre-demo regression gate (Phase 6): everything in one command, with a summary at the end.
#   scripts/check_all.sh              # full gate (~12 min: the conversation evals use the real LLM + DB)
#   SKIP_EVALS=1 scripts/check_all.sh # quick gate (~2 min)
set -uo pipefail
cd "$(dirname "$0")/.."

results=()
run() {
  local name="$1"; shift
  echo; echo "=== $name"
  if "$@"; then results+=("PASS  $name"); else results+=("FAIL  $name"); fi
}

run "Python lint + format"        bash -c "uv run ruff check . && uv run ruff format --check ."
run "Python unit tests"           uv run pytest -q
run "Database tests (Supabase)"   uv run pytest -q -m integration
run "Frontend tests + types"      bash -c "cd frontend && corepack pnpm@9.15.9 test && npx tsc --noEmit -p ."
if [ "${SKIP_EVALS:-0}" != "1" ]; then
  run "Conversation evals (45, >= 95% pass)" uv run python -m evals.run_text_evals --min-pass 0.95
fi

echo; echo "=== Summary"
printf '%s\n' "${results[@]}"
for r in "${results[@]}"; do [[ $r == FAIL* ]] && exit 1; done
exit 0
