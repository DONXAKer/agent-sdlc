# Agent-SDLC для Qwen Code

Самостоятельный интерактивный флоу из десяти навыков `/sdlc-*` и четырёх ролей.
Требуются Python 3.11+, Git, авторизованный Qwen Code с поддержкой пользовательских навыков и субагентов, и инструменты проекта.
Runner и Claude Code не нужны. На Windows общие проверки используют Git Bash при наличии, иначе `cmd.exe`.

База патча по умолчанию — Git commit из `base_sha`. Для dirty-базы до первой правки укажи в плане `**Вид базы:** snapshot` и выполни `attempt-evidence.py snapshot <корень> <slug>` до изменений. Все попытки, scope и base-check используют этот снимок.

## Установка

Из этого каталога в исходном клоне выполни:

```text
python implementations/qwen-code/install-qwen-code.py --doctor
python implementations/qwen-code/install-qwen-code.py --project /путь/к/проекту
python implementations/qwen-code/install-qwen-code.py --project /путь/к/проекту --check
```

Для пользовательской установки замени `--project ...` на `--user`.
В распакованном ZIP перейди в `agent-sdlc-qwen-code/` и используй `python install-qwen-code.py` с теми же аргументами.
На Windows можно использовать `py -3.11`, на Linux и macOS — `python3`.

Проектная установка размещает навыки в `.qwen/skills/`, а роли — в `.qwen/agents/`.
При `--user` эти каталоги создаются в домашнем каталоге пользователя (`~/.qwen/`).
Qwen Code обнаруживает навыки и агентов в этих каталогах автоматически; после установки открой новую сессию.
Запусти `/sdlc-run <slug>` с явным корнем проекта и необязательным описанием задачи.
Этапы доступны отдельно через `/sdlc-intent`, `/sdlc-explore`, `/sdlc-ask`, `/sdlc-plan`, `/sdlc-chunk`, `/sdlc-verify`, `/sdlc-handoff`; между витками — `/sdlc-seed` и `/sdlc-retro`.
Навыки также вызываются моделью инструментом `skill` по имени `sdlc-*`; роли — инструментом `agent` с `subagent_type` `sdlc-claims`, `sdlc-locator`, `sdlc-reviewer`, `sdlc-methodologist`.

Установщик копирует только файлы из `manifest.json` и проверяет их SHA-256. Исходный клон после установки не нужен.
Отличающиеся и устаревшие собственные файлы сохраняются в `.qwen/sdlc-backup-*`.
Чужие навыки, агенты и конфигурация Qwen Code сохраняются. Ссылки и Windows reparse point в путях блокируют установку.
Режим `--check` ничего не записывает. Ключи и авторизация в комплект не входят.

## Состав и границы

Навыки: `sdlc-run`, `sdlc-intent`, `sdlc-explore`, `sdlc-ask`, `sdlc-plan`, `sdlc-chunk`, `sdlc-verify`, `sdlc-handoff`, `sdlc-seed`, `sdlc-retro`.
Агенты: `sdlc-claims`, `sdlc-locator`, `sdlc-reviewer`, `sdlc-methodologist`.

Перед CHUNK навык запускает `gates-preflight.py` из `skills/sdlc-verify/tools`.
Флаг `--builtin-review` разрешён только при доступном независимом проверяющем. Остальные включённые гейты требуют команд в обратных кавычках.
Отдельный агент разделяет контекст, но не гарантирует независимую модель или отдельную песочницу.
Ограничения `tools` в агентах действуют конструкцией; запрет записи через доступный `run_shell_command` остаётся инструкцией.
Hooks Claude Code и защиты Runner отсутствуют; scope-hook Qwen Code (`hooks:` во фронтматтере навыка) в комплект не входит — граница scope проверяется по фактическому diff.
Коммит и push требуют явного запроса.
Живой полный виток Qwen Code пока не подтверждён; проверка установки не доказывает качество выполнения этапов моделью.

Форматы описаны в официальной документации Qwen Code: навыки (`SKILL.md`, фронтматтер `name`/`description`, каталоги `.qwen/skills` и `~/.qwen/skills`) и субагенты (`.qwen/agents`, фронтматтер `name`/`description`/`tools`).

## Синхронизация и упаковка

Тексты навыков создаются из комплекта Codex заменой заголовка общего контракта на «Общий контракт Qwen Code» и добавлением строк о вызове навыков и ролей; относительные пути ресурсов (`../sdlc-verify/tools`, `../sdlc-run/references/SDLC.md`) сохраняются — Qwen Code разрешает их от каталога навыка.
Общие инструменты и шаблоны синхронизируются из канонических источников репозитория.
Меняй исходные навыки Codex, общие инструменты Claude Code, корневые шаблоны или `SDLC.md`, затем выполни `python tools/distribution.py sync` из корня репозитория.
Qwen-специфичные агенты, установщик и этот README редактируются в этом каталоге.

Проверь комплект командой `python implementations/qwen-code/test_package.py`.
Для самостоятельного архива выполни `python implementations/qwen-code/package.py`; результат появится в `implementations/agent-sdlc-qwen-code.zip`.
Общая воспроизводимая сборка всех ZIP — `python tools/distribution.py package`.
