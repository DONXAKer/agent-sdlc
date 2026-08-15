#!/usr/bin/env bash
# Единственная команда установки скиллов и агентов Agent-SDLC в глобальный ~/.claude.
# Семантика честной синхронизации (одна на README, CLAUDE.md и e2e-раннер):
#   - расходящиеся глобальные версии СНАЧАЛА бэкапятся в ~/.claude/sdlc-backup-<время>/;
#   - sdlc-* скиллы заменяются целиком, агенты sdlc-*.md — так же;
#   - СИРОТЫ (sdlc-* сущности, которых больше нет в репо) детектятся, бэкапятся и удаляются;
#   - кэш-мусор (__pycache__, .DS_Store, *.pyc) игнорируется при сверке и не устанавливается.
# Ничего, кроме sdlc-*, не трогается. `--check` — только сверить, exit 1 при расхождении.
set -euo pipefail

IMPL="$(cd "$(dirname "$0")/implementations/claude-code" && pwd)"
SKILLS_DST="$HOME/.claude/skills"
AGENTS_DST="$HOME/.claude/agents"
DIFF=(diff -rq -x __pycache__ -x .DS_Store -x '*.pyc')

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
for dst in "$SKILLS_DST/"sdlc-*/; do
  [ -e "$dst" ] || continue
  name=$(basename "$dst")
  [ -d "$IMPL/skills/$name" ] || orphan_skills+=("$name")
done
for dst in "$AGENTS_DST/"sdlc-*.md; do
  [ -e "$dst" ] || continue
  name=$(basename "$dst")
  [ -f "$IMPL/agents/$name" ] || orphan_agents+=("$name")
done

total=$(( ${#stale_skills[@]} + ${#stale_agents[@]} + ${#orphan_skills[@]} + ${#orphan_agents[@]} ))

if [ "${1:-}" = "--check" ]; then
  if [ "$total" -eq 0 ]; then echo "глобальные sdlc-* совпадают с репозиторием"; exit 0; fi
  [ ${#orphan_skills[@]} -eq 0 ] && [ ${#orphan_agents[@]} -eq 0 ] \
    || echo "сироты (нет в репо): ${orphan_skills[*]:-} ${orphan_agents[*]:-}"
  echo "глобальные sdlc-* разошлись с репозиторием"
  exit 1
fi

if [ "$total" -eq 0 ]; then
  echo "глобальные sdlc-* совпадают с репозиторием — установка не требуется"
  exit 0
fi

BK="$HOME/.claude/sdlc-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SKILLS_DST" "$AGENTS_DST"
backed=0
for name in "${stale_skills[@]:-}" "${orphan_skills[@]:-}"; do
  [ -n "$name" ] && [ -d "$SKILLS_DST/$name" ] || continue
  mkdir -p "$BK/skills"; cp -R "$SKILLS_DST/$name" "$BK/skills/"; backed=1
  rm -rf "$SKILLS_DST/$name"
done
for name in "${stale_agents[@]:-}" "${orphan_agents[@]:-}"; do
  [ -n "$name" ] && [ -f "$AGENTS_DST/$name" ] || continue
  mkdir -p "$BK/agents"; cp "$AGENTS_DST/$name" "$BK/agents/"; backed=1
done
for name in "${orphan_agents[@]:-}"; do
  [ -n "$name" ] || continue
  rm -f "$AGENTS_DST/$name"
done
cp -r "$IMPL/skills/"sdlc-* "$SKILLS_DST/"
cp    "$IMPL/agents/"sdlc-*.md "$AGENTS_DST/"
# кэш-мусор из рабочей копии не должен жить в установке (и не должен давать вечный «разошлись»)
find "$SKILLS_DST/"sdlc-* \( -name __pycache__ -type d \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$SKILLS_DST/"sdlc-* -name .DS_Store -delete 2>/dev/null || true
echo "установлено: скиллы и агенты sdlc-* из $IMPL"
[ ${#orphan_skills[@]} -eq 0 ] && [ ${#orphan_agents[@]} -eq 0 ] \
  || echo "удалены сироты: ${orphan_skills[*]:-} ${orphan_agents[*]:-}"
[ "$backed" -eq 1 ] && echo "бэкап прежних глобальных версий: $BK" || true
