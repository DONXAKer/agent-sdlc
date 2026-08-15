#!/usr/bin/env bash
# E2E-прогон Agent-SDLC: полный цикл /sdlc-* на fixture-проекте с записанными ответами человека.
#
# Роль человека играет test/answers.md: каждая пауза (AskUserQuestion/ExitPlanMode) закрывается
# ответом из него от имени «Тест-Оператор». Скиллы и агенты — ГЛОБАЛЬНЫЕ (~/.claude/skills,
# ~/.claude/agents): перед прогоном они сверяются с редакцией репозитория и при расхождении
# устанавливаются штатной командой из README реализации.
#
# Запуск: test/run-e2e.sh [run-dir]
# Итог: артефакты в <run-dir>/.sdlc/SCHED-101/, лог по этапам в <run-dir>/logs/,
#       вердикт — test/verdict.py, печатается в конце и в verdict.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SLUG="SCHED-101"
RUN="${1:-$(mktemp -d "${TMPDIR:-/tmp}/sdlc-e2e.XXXXXX")}"
mkdir -p "$RUN/logs"
echo "run dir: $RUN"

# --- рабочая копия fixture + git ---------------------------------------------
cp -R "$REPO/test/fixture/." "$RUN/"
cp "$REPO/test/task.md" "$RUN/TASK.md"
cp "$REPO/test/answers.md" "$RUN/ANSWERS.md"
mkdir -p "$RUN/.claude"
cat > "$RUN/.claude/settings.json" <<'JSON'
{ "permissions": { "defaultMode": "bypassPermissions" } }
JSON

# --- скиллы ГЛОБАЛЬНЫЕ (~/.claude): сверить с редакцией репозитория, при расхождении
# --- установить штатной командой из README реализации — иначе прогон молча уйдёт на старую версию
IMPL="$REPO/implementations/claude-code"
stale=0
for src in "$IMPL/skills/"sdlc-*; do
  name=$(basename "$src")
  diff -rq "$src" "$HOME/.claude/skills/$name" >/dev/null 2>&1 || stale=1
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
cd "$RUN"
git init -q -b main
git add -A
git -c user.email=e2e@test -c user.name=e2e commit -qm "fixture: scheduler baseline"

PROXY_PROMPT="Неинтерактивный e2e-прогон Agent-SDLC. Роль человека играет файл ANSWERS.md в корне \
проекта: перед каждой паузой на человека (AskUserQuestion, ExitPlanMode, любое «спроси/подтверди») \
НЕ зови интерактивный инструмент — прочитай ANSWERS.md, возьми оттуда ответ для этого этапа и \
запиши его в артефакт как ответ человека «Тест-Оператор» с сегодняшней датой. Ответа нет — правило \
по умолчанию в конце ANSWERS.md. Исходная задача человека — TASK.md. Работай строго по вызванному \
скиллу, ничего сверх его выхода."

stage() {
  local name="$1"; shift
  local prompt="$1"; shift
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

stage 1-intent  "/sdlc-intent $SLUG"
stage 2-explore "/sdlc-explore $SLUG"
stage 3-ask     "/sdlc-ask $SLUG"
stage 4-plan    "/sdlc-plan $SLUG"
stage 5-chunk   "/sdlc-chunk $SLUG"
stage 6-verify  "/sdlc-verify $SLUG 1"
# retry-петля: если вердикт retry — ещё одна попытка chunk+verify (не больше одной в e2e)
if grep -qi "action.*retry" .sdlc/$SLUG/verification-report-1-attempt-1.md 2>/dev/null; then
  stage 5b-chunk-retry "/sdlc-chunk $SLUG"
  stage 6b-verify-retry "/sdlc-verify $SLUG 1"
fi
stage 7-handoff "/sdlc-handoff $SLUG"

echo
echo "=== ВЕРДИКТ ==="
python3 "$REPO/test/verdict.py" "$RUN" "$SLUG" | tee "$RUN/verdict.md"
