"""Установи проектного агента обработки GitHub issue без изменения глобальных настроек."""
import shutil
import stat
import sys
import time
import tomllib
from pathlib import Path


def checked(root, relative):
    target = root / relative
    for item in (target, *target.parents):
        if not item.is_relative_to(root):
            break
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError(f'Ссылка в пути установки: {item}')
    if not target.resolve().is_relative_to(root):
        raise ValueError(f'Путь выходит за проект: {target}')
    return target


def main():
    root = Path(__file__).resolve().parents[1]
    data = (root / 'tools/global-agents/issue-maintainer.toml').read_bytes()
    assert tomllib.loads(data.decode('utf-8'))['name'] == 'issue_maintainer'
    agent = checked(root, Path('.codex/agents/issue_maintainer.toml'))
    instructions = checked(root, Path('AGENTS.md'))
    old = instructions.read_text(encoding='utf-8') if instructions.exists() else ''
    rule = '''## Обработка GitHub issue

Когда пользователь просит полный цикл обработки issue, передай выполнение проектному агенту `issue_maintainer`.
Передай репозиторий, номера issue, область и отдельные разрешения на commit, push и закрытие.
Агент изучает issue, исправляет дефект или обосновывает отклонение. Для ревью он вызывает `code_review_all`.
Для явно разрешённых Git-действий он вызывает `commit_push`. Дождись результата и сообщи итог по каждой задаче.
Если роль ещё не загружена, используй `default` с инструкциями из `.codex/agents/issue_maintainer.toml`.
'''
    content = old if '## Обработка GitHub issue' in old else old.rstrip() + '\n\n' + rule
    desired = {agent: data, instructions: content.lstrip('\n').encode('utf-8')}
    backup = Path('.codex') / ('issue-maintainer-backup-' + str(time.time_ns()))
    for target, payload in desired.items():
        if target.exists() and target.read_bytes() == payload:
            continue
        if target.exists():
            saved = checked(root, backup / target.relative_to(root))
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    print('Агент issue_maintainer установлен в проекте.')


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    main()
