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
# Retry-петля крутится до бюджета попыток из журнала chunk'а; `continue` при плане, оставляющем
# пункты следующим chunk'ам, открывает следующую связку 5→6, иначе — handoff. Все значения из
# артефактов читает единственный парсер — flow-verdict.py --print (bash формат артефактов
# не разбирает и имена файлов не глобит). Verify, отработавший без нового отчёта, — немедленный
# обрыв. Обрыв любого этапа всё равно завершается handoff'ом и вердиктом. Итог: артефакты
# в <run-dir>/.sdlc/<SLUG>/, логи в <run-dir>/logs/, вердикт в verdict.md.
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
  # и Finder-мусор — .DS_Store fixture/.gitignore НЕ кроет и он попал бы в baseline-коммит.
  # Список имён — общий с install.sh (junk-names.sh); без -delete: он включает -depth
  # и ломает -prune
  source "$REPO/junk-names.sh"
  for j in "${JUNK_DIRS[@]}"; do
    find "$RUN" -name "$j" -type d -prune -exec rm -rf {} + 2>/dev/null || true
  done
  for j in "${JUNK_FILES[@]}"; do
    find "$RUN" -name "$j" -type f -exec rm -f {} + 2>/dev/null || true
  done
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
# снимок состояния одним субпроцессом: pv all → key=value → переменные S_*
# (формат имён отчётов и журналов знает только flow-verdict — bash артефакты не глобит)
snapshot() {
  local out k v
  out=$(pv all) || return 1
  while IFS='=' read -r k v; do
    case "$k" in
      action)     S_ACTION="$v" ;;
      attempts)   S_ATTEMPTS="$v" ;;
      budget)     S_BUDGET="$v" ;;
      chunk)      S_CHUNK="$v" ;;
      reports)    S_REPORTS="$v" ;;
      next_chunk) S_NEXT="$v" ;;
    esac
  done <<< "$out"
}

run_iteration() {
  # одна пара chunk → verify с детектами прогресса и нового отчёта;
  # вход: $1 — число попыток последнего журнала на момент снимка
  local n="$1" k=$((n + 1)) before="$S_REPORTS" ch0="$S_CHUNK" ch n2 after
  # before снимается ДО chunk-стадии: окно шире (экзотика «chunk сам написал отчёт»
  # засчитается verify), но зато «verify ничего не оставил» ловится всегда; чужой
  # отчёт от chunk'а — нарушение другого рода, его ловит рецензент и scope-гейт.
  # Имя лога — из состояния ДО стадии (chunk + следующая попытка): при легитимном открытии
  # следующего журнала фактический номер попытки другой, но имя уникально и не затирается
  stage "5-chunk-${ch0}-attempt-$k" "/sdlc-chunk $SLUG" || return 1
  snapshot || { ABORTED="pv-сбой"; return 1; }
  ch="$S_CHUNK"; n2="$S_ATTEMPTS"
  # прогресс = новая строка в ТОМ ЖЕ журнале, либо chunk легитимно открыл следующий
  # журнал (мультичанковый план) — сравнение пары (chunk, attempts), не голых счётчиков
  if [ "$ch" = "$ch0" ] && [ "$n2" -le "$n" ]; then
    echo "chunk отработал, но строка попытки в журнале не прибавилась ($n → $n2) — останов"
    ABORTED="chunk-без-строки-попытки"
    return 1
  fi
  stage "6-verify-${ch}-attempt-$n2" "/sdlc-verify $SLUG $ch" || return 1
  snapshot || { ABORTED="pv-сбой"; return 1; }
  after="$S_REPORTS"
  if [ "$after" -le "$before" ]; then
    echo "verify отработал, но отчёта не прибавилось ($before → $after) — останов"
    ABORTED="verify-без-отчёта"
    return 1
  fi
}

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

  # цикл chunk → verify до закрытия плана, эскалации или исчерпания бюджета;
  # verify обязан оставлять отчёт — «этап прошёл, отчёта не прибавилось» = немедленный обрыв
  while :; do
    snapshot || { ABORTED="pv-сбой"; return 1; }
    local act="$S_ACTION" n="$S_ATTEMPTS"
    case "$act" in
      continue)
        # норма verify: план закрыт — handoff; оставляет пункты следующим chunk'ам —
        # следующая связка 5→6 (бюджет попыток нового chunk'а свой, здесь не проверяется)
        [ "$S_NEXT" = "yes" ] || break
        run_iteration "$n" || return 1
        ;;
      escalate) echo "ЭСКАЛАЦИЯ после попытки $n — handoff оформит обрыв"; break ;;
      retry|none)
        # мусорный отчёт распознаётся ДО проверки бюджета — иначе на последней попытке
        # «отчёт-без-action» маскировался бы под штатное исчерпание бюджета
        if [ "$act" = "none" ] && [ "$S_REPORTS" -gt 0 ]; then
          echo "последний отчёт приёмки без валидного action — останов"
          ABORTED="отчёт-без-action"
          return 1
        fi
        if [ "$n" -ge "$S_BUDGET" ]; then
          echo "бюджет попыток ($S_BUDGET) исчерпан (action=$act) — handoff оформит обрыв"
          break
        fi
        run_iteration "$n" || return 1
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
