#!/usr/bin/env bash
# Единственная команда установки скиллов и агентов Agent-SDLC в глобальный ~/.claude.
# Семантика честной синхронизации (одна на README, CLAUDE.md и e2e-раннер):
#   - расходящиеся глобальные версии СНАЧАЛА бэкапятся в ~/.claude/sdlc-backup-<время>/;
#   - sdlc-* скиллы заменяются целиком, агенты sdlc-*.md — так же (симлинки заменяются,
#     а не перезаписываются сквозь);
#   - СИРОТЫ (sdlc-* сущности, которых больше нет в репо, включая файлы-тёзки скиллов)
#     детектятся, бэкапятся и удаляются;
#   - кэш-мусор игнорируется при сверке и вычищается из установки при каждом запуске.
# Ничего, кроме sdlc-*, не трогается. `--check` — только сверить, exit 1 при расхождении.
set -euo pipefail

IMPL="$(cd "$(dirname "$0")/implementations/claude-code" && pwd)"
SKILLS_DST="$HOME/.claude/skills"
AGENTS_DST="$HOME/.claude/agents"
# Единый список кэш-мусора: сверка (-x) и зачистка (find) обязаны совпадать поимённо
JUNK_NAMES=(__pycache__ .DS_Store '*.pyc')
DIFF=(diff -rq); for j in "${JUNK_NAMES[@]}"; do DIFF+=(-x "$j"); done

cleanup_junk() {
  # выполняется при каждом запуске (и при no-op): мусор не должен жить в установке
  for d in "$SKILLS_DST/"sdlc-*/; do
    [ -e "$d" ] || continue
    find "$d" \( -name "${JUNK_NAMES[0]}" -type d -prune -exec rm -rf {} + \) \
         -o -name "${JUNK_NAMES[1]}" -delete -o -name "${JUNK_NAMES[2]}" -delete || true
  done
}

# --- один проход детекта: списки расходящихся и сирот -------------------------------------------
stale_skills=(); stale_agents=(); orphan_skills=(); orphan_agents=()
for src in "$IMPL/skills/"sdlc-*/; do
  [ -e "$src" ] || continue
  name=$(basename "$src")
  "${DIFF[@]}" "${src%/}" "$SKILLS_DST/$name" >/dev/null 2>&1 || stale_skills+=("$name")
done
for src in "$IMPL/agents/"sdlc-*.md; do
  [ -e "$src" ] || continue
  name=$(basename "$src")
  diff -q "$src" "$AGENTS_DST/$name" >/dev/null 2>&1 || stale_agents+=("$name")
done
for dst in "$SKILLS_DST/"sdlc-*; do   # без слэша: ловим и файлы-тёзки скиллов
  [ -e "$dst" ] || continue
  name=$(basename "$dst")
  if [ ! -d "$dst" ] || [ ! -d "$IMPL/skills/$name" ]; then
    orphan_skills+=("$name")          # файл-аномалия или сущность, удалённая из репо
  fi
done
for dst in "$AGENTS_DST/"sdlc-*.md; do
  [ -e "$dst" ] || continue
  name=$(basename "$dst")
  [ -f "$IMPL/agents/$name" ] || orphan_agents+=("$name")
done

total=$(( ${#stale_skills[@]} + ${#stale_agents[@]} + ${#orphan_skills[@]} + ${#orphan_agents[@]} ))
orphans="${orphan_skills[*]:-}${orphan_skills[*]:+ }${orphan_agents[*]:-}"

if [ "${1:-}" = "--check" ]; then
  if [ "$total" -eq 0 ]; then echo "глобальные sdlc-* совпадают с репозиторием"; exit 0; fi
  [ -z "$orphans" ] || echo "сироты (нет в репо): $orphans"
  echo "глобальные sdlc-* разошлись с репозиторием"
  exit 1
fi

if [ "$total" -eq 0 ]; then
  cleanup_junk
  echo "глобальные sdlc-* совпадают с репозиторием — установка не требуется"
  exit 0
fi

BK="$HOME/.claude/sdlc-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SKILLS_DST" "$AGENTS_DST"
backed=0
# скиллы: бэкап расходящихся и сирот, затем удаление целиком (симлинки и файлы-тёзки — тоже)
for name in "${stale_skills[@]:-}" "${orphan_skills[@]:-}"; do
  [ -n "$name" ] && [ -e "$SKILLS_DST/$name" ] || continue
  mkdir -p "$BK/skills"; cp -R "$SKILLS_DST/$name" "$BK/skills/"; backed=1
  rm -rf "$SKILLS_DST/$name"
done
# агенты: симметрично — бэкап и удаление (rm перед cp: симлинк заменяется, не пишется сквозь)
for name in "${stale_agents[@]:-}" "${orphan_agents[@]:-}"; do
  [ -n "$name" ] && [ -e "$AGENTS_DST/$name" ] || continue
  mkdir -p "$BK/agents"; cp "$AGENTS_DST/$name" "$BK/agents/"; backed=1
  rm -f "$AGENTS_DST/$name"
done
# установка точечно, с guard'ами от пустых глобов (репо без агентов не рвёт скрипт)
for src in "$IMPL/skills/"sdlc-*/; do
  [ -e "$src" ] || continue
  cp -R "${src%/}" "$SKILLS_DST/"
done
for src in "$IMPL/agents/"sdlc-*.md; do
  [ -e "$src" ] || continue
  cp "$src" "$AGENTS_DST/"
done
cleanup_junk
echo "установлено: скиллы и агенты sdlc-* из $IMPL"
[ -z "$orphans" ] || echo "удалены сироты: $orphans"
[ "$backed" -eq 1 ] && echo "бэкап прежних глобальных версий: $BK" || true
