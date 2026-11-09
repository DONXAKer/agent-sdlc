# Agent-SDLC для Kimi Code

Самостоятельный интерактивный флоу из десяти навыков `/skill:sdlc-*` и четырёх ролей.
Требуются Python 3.11+, Git, авторизованный Kimi Code с поддержкой пользовательских агентов и инструменты проекта.
Runner и Claude Code не нужны. На Windows общие проверки используют Git Bash при наличии, иначе `cmd.exe`.

База патча по умолчанию — Git commit из `base_sha`. Для dirty-базы до первой правки укажи в плане `**Вид базы:** snapshot` и выполни `attempt-evidence.py snapshot <корень> <slug>` до изменений. Все попытки, scope и base-check используют этот снимок.

## Установка

Из этого каталога в исходном клоне выполни:

```text
python implementations/kimi-code/install-kimi-code.py --doctor
python implementations/kimi-code/install-kimi-code.py --project /путь/к/проекту
python implementations/kimi-code/install-kimi-code.py --project /путь/к/проекту --check
```

Для пользовательской установки замени `--project ...` на `--user`.
В распакованном ZIP перейди в `agent-sdlc-kimi-code/` и используй `python install-kimi-code.py` с теми же аргументами.
На Windows можно использовать `py -3.11`, на Linux и macOS — `python3`.

Проектная установка размещает навыки в `.kimi-code/skills/`, а роли — в `.kimi-code/agents/`.
При `--user` эти каталоги создаются в домашнем каталоге пользователя. Открыть новую сессию или применить `/reload` после установки.
Запусти `/skill:sdlc-run <slug>` с явным корнем проекта и необязательным описанием задачи.
Этапы доступны отдельно через `/skill:sdlc-intent`, `/skill:sdlc-explore`, `/skill:sdlc-ask`, `/skill:sdlc-plan`, `/skill:sdlc-chunk`, `/skill:sdlc-verify`, `/skill:sdlc-handoff`; между витками — `/skill:sdlc-seed` и `/skill:sdlc-retro`.

Установщик копирует только файлы из `manifest.json` и проверяет их SHA-256. Исходный клон после установки не нужен.
Отличающиеся и устаревшие собственные файлы сохраняются в `.kimi-code/sdlc-backup-*`.
Чужие навыки, агенты и конфигурация Kimi Code сохраняются. Ссылки и Windows reparse point в путях блокируют установку.
Режим `--check` ничего не записывает. Ключи и авторизация в комплект не входят.

## Состав и границы

Навыки: `sdlc-run`, `sdlc-intent`, `sdlc-explore`, `sdlc-ask`, `sdlc-plan`, `sdlc-chunk`, `sdlc-verify`, `sdlc-handoff`, `sdlc-seed`, `sdlc-retro`.
Агенты: `sdlc-claims`, `sdlc-locator`, `sdlc-reviewer`, `sdlc-methodologist`.

Перед CHUNK навык запускает `gates-preflight.py` из `skills/sdlc-verify/tools`.
Флаг `--builtin-review` разрешён только при доступном независимом проверяющем. Остальные включённые гейты требуют команд в обратных кавычках.
Отдельный агент разделяет контекст, но не гарантирует независимую модель или отдельную песочницу.
Ограничения `tools` в агентах действуют конструкцией; запрет записи через доступный `Bash` остаётся инструкцией.
Hooks Claude Code и защиты Runner отсутствуют. Коммит и push требуют явного запроса.
Живой полный виток Kimi Code пока не подтверждён; проверка установки не доказывает качество выполнения этапов моделью.

Форматы описаны в официальной документации: [навыки](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/skills.html), [агенты](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/agents.html).

## Синхронизация и упаковка

Тексты навыков создаются из комплекта Codex заменой обращений к среде и путей ресурсов на формат Kimi Code.
Общие инструменты и шаблоны синхронизируются из канонических источников репозитория.
Меняй исходные навыки Codex, общие инструменты Claude Code, корневые шаблоны или `SDLC.md`, затем выполни `python tools/distribution.py sync` из корня репозитория.
Kimi-специфичные агенты, установщик и этот README редактируются в этом каталоге.

Проверь комплект командой `python implementations/kimi-code/test_package.py`.
Для самостоятельного архива выполни `python implementations/kimi-code/package.py`; результат появится в `implementations/agent-sdlc-kimi-code.zip`.
Общая воспроизводимая сборка всех ZIP — `python tools/distribution.py package`.
