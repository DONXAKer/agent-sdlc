#!/usr/bin/env bash
# E2E-прогон Agent-SDLC: полный цикл /sdlc-* на fixture-проекте с записанными ответами человека.
#
# Роль человека играет test/answers.md: каждая пауза (AskUserQuestion/ExitPlanMode) закрывается
# ответом из него от имени «Тест-Оператор», и по норме «Прокси человека» (SDLC.md) каждый такой
# ответ помечается в артефакте «источник: файл ответов». Скиллы и агенты — ГЛОБАЛЬНЫЕ:
# перед прогоном ./install.sh сверяет их с репозиторием и при расхождении устанавливает,
# забэкапив расходящиеся версии в ~/.claude/sdlc-backup-<время>/.
#
# Запуск:  test/run-e2e.sh [run-dir]
#          SLUG=… TASK_FILE=… ANSWERS_FILE=… — другой виток
#          RESUME=1 test/run-e2e.sh <run-dir> — продолжить оборванный прогон с места по артефактам
# Retry-петля крутится до бюджета попыток из журнала chunk'а. Все значения из артефактов читает
# единственный парсер — flow-verdict.py --print (bash формат артефактов не разбирает). Verify,
# отработавший без нового отчёта, — немедленный обрыв. Обрыв любого этапа всё равно завершается
# handoff'ом и вердиктом. Итог: артефакты в <run-dir>/.sdlc/<SLUG>/, логи в <run-dir>/logs/,
# вердикт в verdict.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="$REPO/implementations/claude-code/skills/sdlc-verify/tools/flow-verdict.py"
SLUG="${SLUG:-SCHED-101}"
TASK_FILE="${TASK_FILE:-$REPO/test/task.md}"
ANSWERS_FILE="${ANSWERS_FILE:-$REPO/test/answers.md}"
RESUME="${RESUME:-0}"
RUN="${1:-$(mktemp -d "${TMPDIR:-/tmp}/sdlc-e2e.XXXXXX")}"
mkdir -p "$RUN/logs"
RUN="$(cd "$RUN" && pwd)"   # абсолютный путь: после cd относительный $RUN ломал бы pv
echo "run dir: $RUN · slug: $SLUG · resume: $RESUME"

# --- шаблоны в скиллах обязаны совпадать с корнем; скиллы — глобально через install.sh ----------
"$REPO/sync-templates.sh" --check
"$REPO/install.sh"

# --- рабочая копия fixture + git (пропускается при RESUME) --------------------------------------
if [ "$RESUME" != "1" ]; then
  cp -R "$REPO/test/fixture/." "$RUN/"
  # чистая рабочая копия: кэш от make test (git и так игнорирует, но пусть не мозолит diff'ы)
  # и Finder-мусор — .DS_Store fixture/.gitignore НЕ кроет и он попал бы в baseline-коммит
  find "$RUN" \( -name __pycache__ -type d -prune -exec rm -rf {} + \) \
       -o -name .DS_Store -delete 2>/dev/null || true
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

ABORTED=""   # единственный канал факта обрыва; main_flow возвращает 1 ровно когда он установлен
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
pv() { python3 "$TOOL" --print "$1" "$RUN" "$SLUG"; }   # единственный парсер артефактов
reports_count() { find "$D" -maxdepth 1 -name 'verification-report-*-attempt-*.md' 2>/dev/null | wc -l | tr -d ' '; }

main_flow() {
  if ! { [ "$RESUME" = "1" ] && [ -s "$D/readiness.md" ]; }; then
    stage 1-intent "/sdlc-intent $SLUG" || return 1
  fi
  if ! { [ "$RESUME" = "1" ] && [ -s "$D/exploration-report.md" ]; }; then
    stage 2-explore "/sdlc-explore $SLUG" || return 1
  fi
  # Прокси-предикат: артефакт самого этапа 3 условный (нет развилок — файла законно нет),
  # поэтому «этапы 3–4 дошли до конца» надёжнее всего виден по вердикту прогона 2 (его пишет
  # этап 4). При «не готова» перегон 3-ask безвреден: ask условный и идемпотентный.
  if ! { [ "$RESUME" = "1" ] && [ "$(pv readiness2)" = "yes" ]; }; then
    stage 3-ask "/sdlc-ask $SLUG" || return 1
  fi
  if ! { [ "$RESUME" = "1" ] && [ "$(pv approved)" = "yes" ]; }; then
    stage 4-plan "/sdlc-plan $SLUG" || return 1
  fi

  # цикл chunk → verify до continue/escalate или исчерпания бюджета;
  # verify обязан оставлять отчёт — «этап прошёл, отчёта не прибавилось» = немедленный обрыв
  while :; do
    local act n b
    act=$(pv action) || { ABORTED="pv-сбой"; return 1; }
    n=$(pv attempts) || { ABORTED="pv-сбой"; return 1; }
    case "$act" in
      continue) break ;;
      escalate) echo "ЭСКАЛАЦИЯ после попытки $n — handoff оформит обрыв"; break ;;
      retry|none)
        # мусорный отчёт распознаётся ДО проверки бюджета — иначе на последней попытке
        # «отчёт-без-action» маскировался бы под штатное исчерпание бюджета
        if [ "$act" = "none" ] && [ "$(reports_count)" -gt 0 ]; then
          echo "последний отчёт приёмки без валидного action — останов"
          ABORTED="отчёт-без-action"
          return 1
        fi
        b=$(pv budget) || { ABORTED="pv-сбой"; return 1; }
        if [ "$n" -ge "$b" ]; then
          echo "бюджет попыток ($b) исчерпан (action=$act) — handoff оформит обрыв"
          break
        fi
        local k=$((n + 1)) before after ch0 ch n2
        # before снимается ДО chunk-стадии: окно шире (экзотика «chunk сам написал отчёт»
        # засчитается verify), но зато «verify ничего не оставил» ловится всегда; чужой
        # отчёт от chunk'а — нарушение другого рода, его ловит рецензент и scope-гейт
        before=$(reports_count)
        ch0=$(pv chunk) || { ABORTED="pv-сбой"; return 1; }
        stage "5-chunk-attempt-$k"  "/sdlc-chunk $SLUG" || return 1
        ch=$(pv chunk) || { ABORTED="pv-сбой"; return 1; }
        n2=$(pv attempts) || { ABORTED="pv-сбой"; return 1; }
        # прогресс = новая строка в ТОМ ЖЕ журнале, либо chunk легитимно открыл следующий
        # журнал (мультичанковый план) — сравнение пары (chunk, attempts), не голых счётчиков
        if [ "$ch" = "$ch0" ] && [ "$n2" -le "$n" ]; then
          echo "chunk отработал, но строка попытки в журнале не прибавилась ($n → $n2) — останов"
          ABORTED="chunk-без-строки-попытки"
          return 1
        fi
        stage "6-verify-attempt-$k" "/sdlc-verify $SLUG $ch" || return 1
        after=$(reports_count)
        if [ "$after" -le "$before" ]; then
          echo "verify отработал, но отчёта не прибавилось ($before → $after) — останов"
          ABORTED="verify-без-отчёта"
          return 1
        fi
        ;;
      *)
        echo "неожиданное значение action от pv: «$act» — останов"
        ABORTED="pv-мусор"
        return 1
        ;;
    esac
  done
}

main_flow || echo "ОБРЫВ основного потока (этап: $ABORTED) — оформляю handoff и вердикт"

stage 7-handoff "/sdlc-handoff $SLUG" || echo "handoff тоже упал — вердикт всё равно считается"

echo
echo "=== ВЕРДИКТ ==="
rc=0
python3 "$TOOL" "$RUN" "$SLUG" | tee "$RUN/verdict.md" || rc=$?
[ -z "$ABORTED" ] || rc=1
exit "$rc"
