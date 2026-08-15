#!/usr/bin/env bash
# Синхронизация канонических шаблонов из templates/ в установочные копии скиллов.
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

# --- умолчание бюджета попыток: DEFAULT_BUDGET кода обязан совпадать с шаблоном журнала ------
code_budget=$(sed -n 's/^DEFAULT_BUDGET = "\([0-9]*\)".*/\1/p' \
  implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py)
tpl_budget=$(sed -n 's|.*/ \([0-9][0-9]*\) — умолчание.*|\1|p' templates/chunk-journal.template.md)
if [[ -z "$code_budget" || "$code_budget" != "$tpl_budget" ]]; then
  echo "РАЗОШЛОСЬ умолчание бюджета попыток: flow-verdict.py DEFAULT_BUDGET=«$code_budget» ↔ chunk-journal.template.md «$tpl_budget»"
  fail=1
fi

if [[ "$mode" == "--check" ]]; then
  [[ $fail -eq 0 ]] && echo "Копии шаблонов совпадают с корнем." || exit 1
else
  [[ $fail -eq 0 ]] || exit 1
fi
