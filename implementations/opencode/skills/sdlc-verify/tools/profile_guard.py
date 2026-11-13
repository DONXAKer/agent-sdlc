"""Проверка установленного Codex-профиля по явно выбранной доверенной поставке.

python profile_guard.py <корень установки> <manifest.json доверенной поставки>
Проверка не загружает обновления и не изменяет установку или архивы витков.
"""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def plain(base, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Недопустимый путь поставки: ' + str(name))
    target = base / relative
    for part in (target, *target.parents):
        if not part.is_relative_to(base):
            break
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
                raise ValueError('Ссылка в установленном профиле: ' + str(part))
    return target


def check(base, manifest_path, active_tools=None):
    base = base.resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if not manifest.get('version') or not isinstance(manifest.get('files'), dict):
        raise ValueError('У доверенной поставки нет версии или списка файлов')
    record_path = plain(base, '.codex/sdlc-installation.json')
    if not record_path.is_file():
        raise ValueError(f'Нет идентификации установки: {record_path}. Выполни install-codex.py из доверенной поставки')
    record = json.loads(record_path.read_bytes())
    release = digest(raw)
    if record.get('manifest_sha256') != release:
        raise ValueError(f'Устаревшая или неопознанная установка: {record_path}; ожидается поставка {release}')
    mapped = {}
    for name, expected in manifest['files'].items():
        relative = Path(name)
        if relative.parts[0] not in ('skills', 'agents'):
            continue
        prefix = '.agents' if relative.parts[0] == 'skills' else '.codex'
        target = plain(base, Path(prefix) / relative)
        if not target.is_file() or digest(target.read_bytes()) != expected:
            raise ValueError('Компонент отличается от поставки: ' + str(target))
        mapped[name] = str(target)
    if active_tools is not None and active_tools.resolve() != (base / '.agents/skills/sdlc-verify/tools').resolve():
        raise ValueError('Инструмент загружен из другого корня: ' + str(active_tools))
    legacy = base / '.codex/skills'
    if legacy.exists() and list(legacy.glob('sdlc-*')):
        raise ValueError('Обнаружен второй корень SDLC: ' + str(legacy) + '; сохрани копию и явно устрани конфликт загрузки')
    return {'manifest_sha256': release, 'version': manifest['version'], 'paths': mapped,
            'manifest_path': str(manifest_path.resolve())}


def before_prepare(tools, project_root=None):
    """Для установленного Codex требуй внешнюю точку сравнения до ready-квитанции."""
    tools = tools.resolve()
    skills = tools.parent.parent
    if skills.name != 'skills' or skills.parent.name not in ('.agents', '.codex'):
        return None
    base = skills.parent.parent
    if project_root is not None:
        project = project_root.resolve()
        for scope in (project, *project.parents):
            if scope == base:
                break
            candidates = (scope / '.agents/skills', scope / '.codex/skills', scope / '.codex/agents')
            if any(path.exists() and list(path.glob('sdlc-*')) for path in candidates):
                raise ValueError('Конфликт project/user SDLC-профилей: ' + str(scope) + '; укажи единый корень компонентов')
    selected = os.environ.get('SDLC_PROFILE_MANIFEST')
    if not selected:
        raise ValueError(f'Не задан SDLC_PROFILE_MANIFEST доверенной поставки; активные инструменты: {tools}')
    return check(base, Path(selected).resolve(), tools)


if __name__ == '__main__':
    try:
        print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2])), ensure_ascii=False, indent=2))
    except (ValueError, OSError, IndexError) as error:
        print(str(error))
        sys.exit(1)
