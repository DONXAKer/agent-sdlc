# Agent-SDLC для OpenCode

Комплект содержит десять навыков `sdlc-*` и четыре субагента.
Требуются Python 3.11+, Git и OpenCode с поддержкой формата V2.
Используются каталоги и разрешения из [документации агентов](https://opencode.ai/docs/agents/)
и [документации навыков](https://opencode.ai/docs/skills/).

Из корня репозитория выполни:

```text
python implementations/opencode/install-opencode.py <корень проекта>
python implementations/opencode/install-opencode.py <корень проекта> --check
```

Из распакованного ZIP запускай `python install-opencode.py <корень проекта>`.
Установщик копирует ресурсы в `.opencode/skills` и `.opencode/agents`.
Он проверяет контрольные суммы до записи и сохраняет изменённые файлы в `.opencode/sdlc-backup-*`.
Настройки OpenCode и чужие файлы сохраняются. Глобальная установка этим установщиком не поддерживается.

Открой новую сессию OpenCode. Напиши: «Загрузи навык sdlc-run и выполни задачу для slug example в указанном корне проекта».
Для продолжения передай тот же slug и корень. Отдельные этапы загружай инструментом `skill` по имени.
Роли `sdlc-claims`, `sdlc-locator`, `sdlc-reviewer`, `sdlc-methodologist` вызываются инструментом `subagent`.
Основной агент передаёт им абсолютный путь к установленной методологии и минимальные входные данные.
Ролям запрещены `edit`, `shell` и запуск вложенных субагентов. Это ограничивает доступные инструменты, но не создаёт песочницу.

База по умолчанию — Git commit из `base_sha`. Для dirty-базы укажи `**Вид базы:** snapshot` в плане.
До первой правки выполни `attempt-evidence.py snapshot <корень> <slug>` из `skills/sdlc-verify/tools`.
Сохрани снимок и SHA-256. Все проверки используют одну базу.

Это переносимый Markdown-флоу. Hooks Claude Code и защиты Runner в комплект не входят.
Витки со `state.json` продолжай в Runner. Коммит и push требуют явного запроса.
Живой полный виток OpenCode пока не подтверждён. Автоматические тесты проверяют комплект и установку.

Для обновления ресурсов выполни `python tools/distribution.py sync` из исходного репозитория.
Для проверки выполни `python test/test_opencode.py` и `python tools/distribution.py check`.
Для ZIP выполни `python tools/distribution.py package`. Архив появится в `dist/agent-sdlc-opencode.zip`.
