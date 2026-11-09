# Agent-SDLC для Cursor

Комплект содержит десять навыков `/sdlc-*`, четыре агента и правило `agent-sdlc.mdc`.
Навыки и общие ресурсы создаёт `python tools/distribution.py sync` из комплекта Codex.
Меняй исходные навыки Codex и общие инструменты Claude Code. Затем повтори синхронизацию.

Установи комплект в целевой проект:

```sh
python implementations/cursor/install-cursor.py /путь/к/проекту
python implementations/cursor/install-cursor.py /путь/к/проекту --check
```

Установщик создаёт `.cursor/skills`, `.cursor/agents` и `.cursor/rules`.
Перед заменой отличающихся файлов он сохраняет резервную копию в `.cursor/sdlc-backup-*`.
Он сохраняет посторонние файлы. Режим `--check` проверяет комплект без записи.
Установщик читает только файлы из `manifest.json` и проверяет их SHA-256.
Локальные `.env` и другие незаявленные файлы исходного каталога не устанавливаются.
Ссылки и Windows reparse point в путях источника и назначения блокируют установку.
Запусти новый чат Cursor и вызови `/sdlc-run <slug>` с явным корнем проекта.

Навыки: `sdlc-run`, `sdlc-intent`, `sdlc-explore`, `sdlc-ask`, `sdlc-plan`,
`sdlc-chunk`, `sdlc-verify`, `sdlc-handoff`, `sdlc-seed`, `sdlc-retro`.
Агенты: `sdlc-claims`, `sdlc-locator`, `sdlc-reviewer`, `sdlc-methodologist`.

Перед CHUNK запусти `gates-preflight.py <корень> --json` из `skills/sdlc-verify/tools`.
Укажи `--builtin-review`, только если вызывающая среда зарегистрировала доступного независимого проверяющего.
Остальные включённые гейты требуют команд в обратных кавычках. Проза без исполнителя блокирует preflight.
Команды используют оболочку общих инструментов: POSIX, Git Bash на Windows при наличии, иначе cmd.exe.

Форматы описаны в документации Cursor: [навыки](https://cursor.com/docs/skills),
[агенты](https://cursor.com/docs/subagents), [правила](https://cursor.com/docs/rules).
Поддержка агентов зависит от среды Cursor. Проверяй доступность перед запуском витка.
Комплект не устанавливает hooks Claude Code и не предоставляет защит Runner.
Отдельный агент не гарантирует независимую модель. Живой полный виток Cursor пока не проверен.
