#!/usr/bin/env bash
# E2E-прогон Agent-SDLC: полный цикл /sdlc-* на fixture-проекте с записанными ответами человека.
#
# Роль человека играет test/answers.md: каждая пауза (AskUserQuestion/ExitPlanMode) закрывается
# ответом из него от имени «Тест-Оператор», и по норме «Прокси человека» (SDLC.md) каждый такой
# ответ помечается в артефакте «источник: файл ответов». Скиллы и агенты — ГЛОБАЛЬНЫЕ
# (~/.claude/skills, ~/.claude/agents): перед прогоном сверяются с редакцией репозитория и при
# расхождении устанавливаются штатной командой из README реализации.
#
# Запуск:  test/run-e2e.sh [run-dir]
#          SLUG=… TASK_FILE=… ANSWERS_FILE=… — другой виток
#          RESUME=1 test/run-e2e.sh <run-dir> — продолжить оборванный прогон с места по артефактам
# Retry-петля крутится до бюджета попыток из журнала chunk'а (умолчание 3), не «одна и хватит».
# Итог: артефакты в <run-dir>/.sdlc/<SLUG>/, логи в <run-dir>/logs/, вердикт в verdict.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SLUG="${SLUG:-SCHED-101}"
TASK_FILE="${TASK_FILE:-$REPO/test/task.md}"
ANSWERS_FILE="${ANSWERS_FILE:-$REPO/test/answers.md}"
RESUME="${RESUME:-0}"
RUN="${1:-$(mktemp -d "${TMPDIR:-/tmp}/sdlc-e2e.XXXXXX")}"
mkdir -p "$RUN/logs"
echo "run dir: $RUN · slug: $SLUG · resume: $RESUME"

# --- скиллы ГЛОБАЛЬНЫЕ (~/.claude): сверить с репозиторием, при расхождении установить ----------
IMPL="$REPO/implementations/claude-code"
stale=0
for src in "$IMPL/skills/"sdlc-*; do
  diff -rq "$src" "$HOME/.claude/skills/$(basename "$src")" >/dev/null 2>&1 || stale=1
done
for src in "$IMPL/agents/"sdlc-*.md; do
  diff -q "$src" "$HOME/.claude/agents/$(basename "$src")" >/dev/null 2>&1 || stale=1
done
if [ "$stale" -eq 0 ]; then
  echo "глобальные скиллы sdlc-* совпадают с репозиторием"
else
  echo "глобальные скиллы sdlc-* разошлись с репозиторием — устанавливаю (шаг установки из README)"
  mkdir -p "$HOME/.claude/skills" "$HOME/.claude/agents"
  cp -r "$IMPL/skills/"* "$HOME/.claude/skills/"
  cp    "$IMPL/agents/"* "$HOME/.claude/agents/"
fi

# --- рабочая копия fixture + git (пропускается при RESUME) --------------------------------------
if [ "$RESUME" != "1" ]; then
  cp -R "$REPO/test/fixture/." "$RUN/"
  cp "$TASK_FILE" "$RUN/TASK.md"
  cp "$ANSWERS_FILE" "$RUN/ANSWERS.md"
  mkdir -p "$RUN/.claude"
  printf '{ "permissions": { "defaultMode": "bypassPermissions" } }\n' > "$RUN/.claude/settings.json"
  cd "$RUN"
  git init -q -b main
  git add -A
  git -c user.email=e2e@test -c user.name=e2e commit -qm "fixture: scheduler baseline"
else
  [ -d "$RUN/.sdlc" ] || { echo "RESUME=1, но в $RUN нет .sdlc — нечего продолжать"; exit 2; }
  cd "$RUN"
fi

PROXY_PROMPT="Неинтерактивный e2e-прогон Agent-SDLC. Роль человека играет файл ANSWERS.md в корне \
проекта: перед каждой паузой на человека (AskUserQuestion, ExitPlanMode, любое «спроси/подтверди») \
НЕ зови интерактивный инструмент — прочитай ANSWERS.md, возьми оттуда ответ для этого этапа и \
запиши его в артефакт как ответ человека «Тест-Оператор» с сегодняшней датой и пометкой \
«источник: файл ответов» (норма SDLC.md → «Прокси человека»). Ответа нет — правило по умолчанию \
в конце ANSWERS.md. Исходная задача человека — TASK.md. Работай строго по вызванному скиллу."

stage() {
  local name="$1" prompt="$2"
  echo "=== $name ==="
  if ! claude -p "$prompt" \
      --append-system-prompt "$PROXY_PROMPT" \
      --dangerously-skip-permissions \
      --max-turns 80 \
      > "logs/$name.log" 2>&1; then
    echo "ЭТАП $name УПАЛ — см. $RUN/logs/$name.log (последние строки:)"
    tail -5 "logs/$name.log" || true
    return 1
  fi
  tail -3 "logs/$name.log"
}

D=".sdlc/$SLUG"

# бюджет попыток: шапка журнала chunk'а, иначе умолчание 3
budget() {
  local j="$D/chunk-1-journal.md"
  [ -f "$j" ] && grep -oE 'Бюджет попыток:\*?\*?[^0-9]*([0-9]+)' "$j" | grep -oE '[0-9]+' | head -1 || echo 3
}
# число попыток = строки таблицы журнала
attempts() {
  [ -f "$D/chunk-1-journal.md" ] && grep -cE '^\|\s*[0-9]+\s*\|' "$D/chunk-1-journal.md" || echo 0
}
# action последнего отчёта приёмки
last_action() {
  local r
  r=$(ls "$D"/verification-report-1-attempt-*.md 2>/dev/null | sort -V | tail -1)
  [ -n "$r" ] && grep -m1 -oE 'action:\*?\*? *(continue|retry|escalate)' "$r" | grep -oE '(continue|retry|escalate)' || echo none
}

# --- точка входа: при RESUME пропустить готовые ранние этапы -------------------------------------
approved() { [ -f "$D/plan.md" ] && grep -qE '\*\*Одобрение:\*\*.*(20[0-9][0-9]|источник)' "$D/plan.md" && ! grep -q 'не одобрен' "$D/plan.md"; }

if ! { [ "$RESUME" = "1" ] && [ -f "$D/readiness.md" ]; }; then stage 1-intent  "/sdlc-intent $SLUG";  fi
if ! { [ "$RESUME" = "1" ] && [ -f "$D/exploration-report.md" ]; }; then stage 2-explore "/sdlc-explore $SLUG"; fi
if ! { [ "$RESUME" = "1" ] && grep -q "Вердикт прогона 2" "$D/readiness.md" 2>/dev/null; }; then
  stage 3-ask "/sdlc-ask $SLUG"
fi
if ! { [ "$RESUME" = "1" ] && approved; }; then stage 4-plan "/sdlc-plan $SLUG"; fi

# --- цикл chunk → verify до continue/escalate или исчерпания бюджета ----------------------------
while :; do
  act=$(last_action)
  n=$(attempts)
  case "$act" in
    continue) break ;;
    escalate) echo "ЭСКАЛАЦИЯ после попытки $n — цикл остановлен, handoff оформит обрыв"; break ;;
    retry|none)
      if [ "$act" = "retry" ] && [ "$n" -ge "$(budget)" ]; then
        echo "бюджет попыток ($(budget)) исчерпан при action=retry — handoff оформит обрыв"; break
      fi
      k=$((n + 1))
      stage "5-chunk-attempt-$k"  "/sdlc-chunk $SLUG"
      stage "6-verify-attempt-$k" "/sdlc-verify $SLUG 1"
      ;;
  esac
done

stage 7-handoff "/sdlc-handoff $SLUG"

echo
echo "=== ВЕРДИКТ ==="
python3 "$REPO/test/verdict.py" "$RUN" "$SLUG" | tee "$RUN/verdict.md"
