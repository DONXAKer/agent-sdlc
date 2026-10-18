#!/usr/bin/env bash
# Синхронизация канонических шаблонов templates/ и sdlc-constants.json в установочные копии скиллов.
# Направление одно: корень → скиллы. Правка копии в скилле затирается — правь корень.
# Запуск без аргументов копирует; `--check` только сверяет и падает при расхождении (для CI/гейта).
set -euo pipefail
cd "$(dirname "$0")"

SKILLS=implementations/claude-code/skills

# шаблон → скиллы, которым он нужен (скилл копирует его в .sdlc/ при работе)
declare -a MAP=(
  "intent.template.md            $SKILLS/sdlc-intent"
  "gates.template.md             $SKILLS/sdlc-intent"
  "readiness.template.md         $SKILLS/sdlc-intent $SKILLS/sdlc-plan"
  "exploration-report.template.md $SKILLS/sdlc-explore"
  "clarification-report.template.md $SKILLS/sdlc-ask"
  "plan.template.md              $SKILLS/sdlc-plan"
  "acceptance-checks.template.md $SKILLS/sdlc-plan $SKILLS/sdlc-verify"
  "chunk-journal.template.md     $SKILLS/sdlc-chunk"
  "verification-report.template.md $SKILLS/sdlc-verify"
  "handoff.template.md           $SKILLS/sdlc-handoff"
)

mode="${1:-copy}"
fail=0
for row in "${MAP[@]}"; do
  # shellcheck disable=SC2086
  set -- $row
  tpl="templates/$1"; shift
  for dir in "$@"; do
    dst="$dir/templates/$(basename "$tpl")"
    if [[ "$mode" == "--check" ]]; then
      if ! diff -q "$tpl" "$dst" >/dev/null 2>&1; then
        echo "РАЗОШЛОСЬ: $tpl ↔ $dst"
        fail=1
      fi
    else
      mkdir -p "$dir/templates"
      cp "$tpl" "$dst"
      echo "→ $dst"
    fi
  done
done

# state-first контракт нужен и установленному скиллу вне checkout репозитория.
# Первоисточник — implementations/runner-contract; копии разливаются только отсюда.
CONTRACT=implementations/runner-contract
CONTRACT_DST="$SKILLS/sdlc-verify/tools"
for relative in tools/state_contract.py schemas/agent-sdlc-state-v1.schema.json schemas/verify-review-v1.schema.json; do
  src="$CONTRACT/$relative"
  case "$relative" in
    tools/*) dst="$CONTRACT_DST/$(basename "$relative")" ;;
    schemas/*) dst="$CONTRACT_DST/state_contract/$relative" ;;
  esac
  if [[ "$mode" == "--check" ]]; then
    if ! diff -q "$src" "$dst" >/dev/null 2>&1; then
      echo "РАЗОШЛОСЬ: $src ↔ $dst"
      fail=1
    fi
  else
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    echo "→ $dst"
  fi
done

# --- сироты: копия в скилле, которой нет в корне (шаблон удалён/переименован) ---------------
# без этого --check зелёный при осиротевшей копии, а install.sh разносит её пользователям
for dst in "$SKILLS"/*/templates/*.md; do
  [ -e "$dst" ] || continue
  if [ ! -f "templates/$(basename "$dst")" ]; then
    if [[ "$mode" == "--check" ]]; then
      echo "СИРОТА (в корне templates/ такого шаблона нет): $dst"
      fail=1
    else
      rm -f "$dst"
      echo "удалена сирота: $dst"
    fi
  fi
done

# --- константы методологии: установочная копия для flow-verdict.py --------------------------
# sdlc-constants.json — единственный источник имён и чисел; flow-verdict.py читает копию
# рядом с собой (после install.sh корня репозитория у него нет). Умолчание бюджета попыток
# и остальные числа с прозой сверяет tools/check-consistency.py, не этот скрипт.
const_dst=implementations/claude-code/skills/sdlc-verify/tools/sdlc-constants.json
if [[ "$mode" == "--check" ]]; then
  if ! diff -q sdlc-constants.json "$const_dst" >/dev/null 2>&1; then
    echo "РАЗОШЛОСЬ: sdlc-constants.json ↔ $const_dst"
    fail=1
  fi
else
  cp sdlc-constants.json "$const_dst"
  echo "→ $const_dst"
fi

if [[ "$mode" == "--check" ]]; then
  [[ $fail -eq 0 ]] && echo "Копии шаблонов совпадают с корнем." || exit 1
else
  [[ $fail -eq 0 ]] || exit 1
fi
