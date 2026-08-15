#!/usr/bin/env bash
# Единственная команда установки скиллов и агентов Agent-SDLC в глобальный ~/.claude.
# Семантика честной синхронизации (одна на README, CLAUDE.md и e2e-раннер):
#   - расходящиеся глобальные версии СНАЧАЛА бэкапятся в ~/.claude/sdlc-backup-<время>/;
#   - затем sdlc-* директории скиллов заменяются целиком (удалённые в репо файлы не выживают);
#   - агенты sdlc-*.md — так же, с бэкапом.
# Ничего, кроме sdlc-*, не трогается. `--check` — только сверить, exit 1 при расхождении.
set -euo pipefail

IMPL="$(cd "$(dirname "$0")/implementations/claude-code" && pwd)"
SKILLS_DST="$HOME/.claude/skills"
AGENTS_DST="$HOME/.claude/agents"

stale=0
for src in "$IMPL/skills/"sdlc-*/; do
  diff -rq "${src%/}" "$SKILLS_DST/$(basename "$src")" >/dev/null 2>&1 || stale=1
done
for src in "$IMPL/agents/"sdlc-*.md; do
  diff -q "$src" "$AGENTS_DST/$(basename "$src")" >/dev/null 2>&1 || stale=1
done

if [ "${1:-}" = "--check" ]; then
  if [ "$stale" -eq 0 ]; then echo "глобальные sdlc-* совпадают с репозиторием"; exit 0
  else echo "глобальные sdlc-* разошлись с репозиторием"; exit 1; fi
fi

if [ "$stale" -eq 0 ]; then
  echo "глобальные sdlc-* совпадают с репозиторием — установка не требуется"
  exit 0
fi

BK="$HOME/.claude/sdlc-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SKILLS_DST" "$AGENTS_DST"
backed=0
for src in "$IMPL/skills/"sdlc-*/; do
  name=$(basename "$src")
  if [ -d "$SKILLS_DST/$name" ] && ! diff -rq "${src%/}" "$SKILLS_DST/$name" >/dev/null 2>&1; then
    mkdir -p "$BK/skills"; cp -R "$SKILLS_DST/$name" "$BK/skills/"; backed=1
    rm -rf "$SKILLS_DST/$name"
  fi
done
for src in "$IMPL/agents/"sdlc-*.md; do
  name=$(basename "$src")
  if [ -f "$AGENTS_DST/$name" ] && ! diff -q "$src" "$AGENTS_DST/$name" >/dev/null 2>&1; then
    mkdir -p "$BK/agents"; cp "$AGENTS_DST/$name" "$BK/agents/"; backed=1
  fi
done
cp -r "$IMPL/skills/"sdlc-* "$SKILLS_DST/"
cp    "$IMPL/agents/"sdlc-*.md "$AGENTS_DST/"
echo "установлено: скиллы и агенты sdlc-* из $IMPL"
[ "$backed" -eq 1 ] && echo "бэкап расходившихся глобальных версий: $BK" || true
