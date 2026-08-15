#!/usr/bin/env bash
# E2E-прогон Agent-SDLC: полный цикл /sdlc-* на fixture-проекте с записанными ответами человека.
#
# Роль человека играет test/answers.md: каждая пауза (AskUserQuestion/ExitPlanMode) закрывается
# ответом из него от имени «Тест-Оператор», и по норме «Прокси человека» (SDLC.md) каждый такой
# ответ помечается в артефакте «источник: файл ответов». Скиллы и агенты — ГЛОБАЛЬНЫЕ
# (~/.claude/skills, ~/.claude/agents): перед прогоном сверяются с редакцией репозитория и при
# расхождении устанавливаются; расходящиеся глобальные версии бэкапятся в <run-dir>/logs/.
#
# Запуск:  test/run-e2e.sh [run-dir]
#          SLUG=… TASK_FILE=… ANSWERS_FILE=… — другой виток
#          RESUME=1 test/run-e2e.sh <run-dir> — продолжить оборванный прогон с места по артефактам
# Retry-петля крутится до бюджета попыток из журнала chunk'а (число должно совпадать с нормой
# SDLC.md → этап 6). Все значения из артефактов читает единственный парсер —
# flow-verdict.py --print — bash формат артефактов не разбирает.
# При обрыве любого этапа handoff и вердикт всё равно выполняются (норма «оформить любой обрыв»).
# Итог: артефакты в <run-dir>/.sdlc/<SLUG>/, логи в <run-dir>/logs/, вердикт в verdict.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="$REPO/implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py"
SLUG="${SLUG:-SCHED-101}"
TASK_FILE="${TASK_FILE:-$REPO/test/task.md}"
ANSWERS_FILE="${ANSWERS_FILE:-$REPO/test/answers.md}"
RESUME="${RESUME:-0}"
RUN="${1:-$(mktemp -d "${TMPDIR:-/tmp}/sdlc-e2e.XXXXXX")}"
mkdir -p "$RUN/logs"
echo "run dir: $RUN · slug: $SLUG · resume: $RESUME"

# --- шаблоны в скиллах обязаны совпадать с корнем ДО установки скиллов глобально ----------------
"$REPO/sync-templates.sh" --check

# --- скиллы ГЛОБАЛЬНЫЕ (~/.claude): сверить с репозиторием, при расхождении установить ----------
IMPL="$REPO/implementations/claude-code"
stale=0
for src in "$IMPL/skills/"sdlc-* "$IMPL/agents/"sdlc-*.md; do
  dst="$HOME/.claude/skills/$(basename "$src")"
  [ -f "$src" ] && dst="$HOME/.claude/agents/$(basename "$src")"
  diff -rq "$src" "$dst" >/dev/null 2>&1 || stale=1
done
if [ "$stale" -eq 0 ]; then
  echo "глобальные скиллы sdlc-* совпадают с репозиторием"
else
  echo "глобальные скиллы sdlc-* разошлись с репозиторием — бэкап и установка"
  mkdir -p "$HOME/.claude/skills" "$HOME/.claude/agents" "$RUN/logs/skills-backup"
  # бэкап текущих глобальных версий: пользовательская правка не должна молча исчезнуть
  for src in "$IMPL/skills/"sdlc-*; do
    name=$(basename "$src")
    if [ -d "$HOME/.claude/skills/$name" ] && ! diff -rq "$src" "$HOME/.claude/skills/$name" >/dev/null 2>&1; then
      cp -R "$HOME/.claude/skills/$name" "$RUN/logs/skills-backup/"
      rm -rf "$HOME/.claude/skills/$name"   # честная синхронизация: удалённое в репо не выживает
    fi
  done
  cp -r "$IMPL/skills/"* "$HOME/.claude/skills/"
  cp    "$IMPL/agents/"* "$HOME/.claude/agents/"
  echo "бэкап расходившихся версий: $RUN/logs/skills-backup/"
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

ABORTED=""
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
    ABORTED="$name"
    return 1
  fi
  tail -3 "logs/$name.log"
}

D=".sdlc/$SLUG"
pv() { python3 "$TOOL" --print "$1" "$RUN" "$SLUG"; }  # единственный парсер артефактов

main_flow() {
  if ! { [ "$RESUME" = "1" ] && [ -s "$D/readiness.md" ]; }; then
    stage 1-intent "/sdlc-intent $SLUG" || return 1
  fi
  if ! { [ "$RESUME" = "1" ] && [ -s "$D/exploration-report.md" ]; }; then
    stage 2-explore "/sdlc-explore $SLUG" || return 1
  fi
  if ! { [ "$RESUME" = "1" ] && grep -q "Вердикт прогона 2" "$D/readiness.md" 2>/dev/null; }; then
    stage 3-ask "/sdlc-ask $SLUG" || return 1
  fi
  if ! { [ "$RESUME" = "1" ] && [ "$(pv approved)" = "yes" ]; }; then
    stage 4-plan "/sdlc-plan $SLUG" || return 1
  fi

  # цикл chunk → verify до continue/escalate, исчерпания бюджета или отсутствия следов
  local none_streak=0
  while :; do
    local act n
    act=$(pv action); n=$(pv attempts)
    case "$act" in
      continue) break ;;
      escalate) echo "ЭСКАЛАЦИЯ после попытки $n — handoff оформит обрыв"; break ;;
      retry|none)
        local b; b=$(pv budget)
        if [ "$act" = "retry" ] && [ "$n" -ge "$b" ]; then
          echo "бюджет попыток ($b) исчерпан при action=retry — handoff оформит обрыв"
          break
        fi
        if [ "$act" = "none" ]; then
          none_streak=$((none_streak + 1))
          if [ "$none_streak" -gt 2 ]; then
            echo "verify дважды не оставил action — останов, handoff оформит обрыв"
            ABORTED="verify-без-отчёта"
            return 1
          fi
        else
          none_streak=0
        fi
        local k=$((n + 1))
        stage "5-chunk-attempt-$k"  "/sdlc-chunk $SLUG" || return 1
        stage "6-verify-attempt-$k" "/sdlc-verify $SLUG 1" || return 1
        ;;
    esac
  done
}

if ! main_flow; then
  echo "ОБРЫВ основного потока (этап: ${ABORTED:-неизвестен}) — оформляю handoff и вердикт"
fi

stage 7-handoff "/sdlc-handoff $SLUG" || echo "handoff тоже упал — вердикт всё равно считается"

echo
echo "=== ВЕРДИКТ ==="
rc=0
python3 "$TOOL" "$RUN" "$SLUG" | tee "$RUN/verdict.md" || rc=$?
[ -z "$ABORTED" ] || rc=1
exit "$rc"
