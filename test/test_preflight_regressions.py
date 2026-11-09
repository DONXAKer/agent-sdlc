"""Регрессии issue №2 и №3: исполнитель гейта и кавычки оболочки."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'implementations/claude-code/skills/sdlc-verify/tools'
sys.path.insert(0, str(TOOLS))
import sdlc_common as common

spec = importlib.util.spec_from_file_location('preflight_regressions', TOOLS / 'gates-preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.sdlc').mkdir()

    def write(self, command, review=None):
        names = common.MANDATORY_GATES
        rows = ['| Гейт | Вкл | Где отчитывается | Чем реализован |', '|---|---|---|---|']
        for name in names:
            how = review if review is not None and name == names[-1] else command
            rows.append(f'| {name} | да — минимум | этап 6 | {how} |')
        (self.root / '.sdlc/gates.md').write_text('\n'.join(rows), encoding='utf-8')

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, str(TOOLS / 'gates-preflight.py'), str(self.root),
                                 '--json', *args], capture_output=True, encoding='utf-8')
        return result.returncode, json.loads(result.stdout)

    def test_positive_control_and_missing_executable(self):
        if ' ' not in sys.executable:
            self.write(f'`{Path(sys.executable).as_posix()} -c "pass"`')
            self.assertEqual(self.run_cli()[0], 0)
        self.write(f'`"{Path(sys.executable).as_posix()}" -c "pass"`')
        self.assertEqual(self.run_cli()[0], 0)
        self.write('`definitely-missing-preflight-xyz -c "pass"`')
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertTrue(result['blocked_env'])
        self.assertFalse(result['configuration_error'])

    def test_prose_without_executor_blocks_cli(self):
        self.write(f'`"{Path(sys.executable).as_posix()}" -c "pass"`')
        path = self.root / '.sdlc/gates.md'
        text = path.read_text(encoding='utf-8')
        lines = text.splitlines()
        lines = [line.rsplit('|', 2)[0] + '| Исполнитель тестов отсутствует |'
                 if line.startswith('| Тесты |') else line for line in lines]
        path.write_text('\n'.join(lines), encoding='utf-8')
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertTrue(result['configuration_error'])
        self.assertFalse(result['blocked_env'])

    def test_missing_mandatory_gate_blocks_preflight(self):
        self.write(f'`"{Path(sys.executable).as_posix()}" -c "pass"`')
        path = self.root / '.sdlc/gates.md'
        lines = path.read_text(encoding='utf-8').splitlines()
        path.write_text('\n'.join(line for line in lines if not line.startswith('| Тесты |')), encoding='utf-8')
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertTrue(result['configuration_error'])
        self.assertFalse(result['blocked_env'])

    def test_aligned_markdown_separator_is_allowed(self):
        self.write(f'`"{Path(sys.executable).as_posix()}" -c "pass"`')
        path = self.root / '.sdlc/gates.md'
        path.write_text(path.read_text(encoding='utf-8').replace('|---|---|---|---|', '|:---|---:|:---:|---|'),
                        encoding='utf-8')
        self.assertEqual(self.run_cli()[0], 0)

    def test_only_registered_review_is_allowed_without_cli(self):
        self.write(f'`"{Path(sys.executable).as_posix()}" -c "pass"`', 'агент sdlc-reviewer')
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.run_cli('--builtin-review')[0], 0)
        self.write('только описание')
        self.assertEqual(self.run_cli('--builtin-review')[0], 1)

    def test_quoted_paths_and_script_arguments(self):
        directory = self.root / 'tools with spaces'
        directory.mkdir()
        executable = directory / ('python.exe' if sys.platform == 'win32' else 'python')
        shutil.copy2(sys.executable, executable)
        if sys.platform == 'win32':
            for library in Path(sys.executable).parent.glob('*.dll'):
                shutil.copy2(library, directory / library.name)
        script = directory / 'check script.py'
        script.write_text('pass', encoding='utf-8')
        # Положительный контроль: тот же executable действительно запускается.
        self.assertEqual(subprocess.run([str(executable), '--version'], capture_output=True).returncode, 0)
        self.write(f'`"{executable.as_posix()}" "{script.as_posix()}"`')
        self.assertEqual(self.run_cli()[0], 0)
        script.unlink()
        self.assertEqual(self.run_cli()[0], 1)

    def test_shell_specific_escaping_and_assignments(self):
        self.assertEqual(common.command_tokens(r'A="a b" ./my\ tool "script name.py"', posix=True),
                         ['./my tool', 'script name.py'])
        self.assertEqual(common.command_tokens(r'"C:\Program Files\Python\python.exe" "a b.py"', posix=False),
                         [r'C:\Program Files\Python\python.exe', 'a b.py'])
        self.assertEqual(common.command_tokens(r'C:\my^ tool\python.exe a^ b.py', posix=False),
                         [r'C:\my tool\python.exe', 'a b.py'])
        with patch.object(common, 'git_bash', return_value=None), patch.object(common.sys, 'platform', 'win32'):
            self.assertEqual(common.first_token(r'"C:\Program Files\python.exe" -c "pass"'),
                             r'C:\Program Files\python.exe')

    def test_invalid_quotes_are_configuration_error(self):
        self.write('`"unfinished`')
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertTrue(result['configuration_error'])

    def test_posix_double_quote_escapes_match_shell(self):
        if sys.platform == 'win32' and not common.git_bash():
            self.skipTest('Для проверки нужен POSIX shell')
        script = self.root / 'check$file.py'
        script.write_text('pass', encoding='utf-8')
        escaped_script = script.as_posix().replace('$', r'\$')
        command = f'"{Path(sys.executable).as_posix()}" "{escaped_script}"'
        self.assertEqual(common.run_shell(command, self.root)[0], 0)
        self.write(f'`{command}`')
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(common.command_tokens(r'python "check\`file.py"', posix=True), ['python', 'check`file.py'])

    def test_posix_comment_quotes_do_not_block_valid_command(self):
        command = f'"{Path(sys.executable).as_posix()}" -c "pass" # "'
        if sys.platform == 'win32' and not common.git_bash():
            self.skipTest('Для проверки нужен POSIX shell')
        self.assertEqual(common.run_shell(command, self.root)[0], 0)
        self.write(f'`{command}`')
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(common.command_tokens('python file#name.py', posix=True), ['python', 'file#name.py'])
        self.assertEqual(common.command_tokens('python "#name.py" # "', posix=True), ['python', '#name.py'])

    def test_unknown_gate_state_blocks_preflight(self):
        self.write(f'`"{Path(sys.executable).as_posix()}" -c "pass"`')
        path = self.root / '.sdlc/gates.md'
        valid = path.read_text(encoding='utf-8')
        for value in ('настройка', 'позже', '', '‹да/нет›'):
            path.write_text(valid + f'\n| Секреты в diff | {value} | этап 6 | описание |', encoding='utf-8')
            code, result = self.run_cli()
            self.assertEqual(code, 1, value)
            self.assertTrue(result['configuration_error'], value)
        for value in ('да', 'нет'):
            debt = ('\n\n## Долг\n\n| Гейт | Где должен стоять | Как закрывается | Дата | Кто | Область | Свидетельство / условие пересмотра |\n'
                    '|---|---|---|---|---|---|---|\n'
                    '| Секреты в diff | этап 6 | риск принят: тестовая фикстура | 2026-11-09 | Тестовый оператор | проект | пересмотр: изменение фикстуры |')
            path.write_text(valid + f'\n| Секреты в diff | {value} | этап 6 | `git --version` |' + debt, encoding='utf-8')
            self.assertEqual(self.run_cli()[0], 0, value)
        path.write_text(valid + '\n| Секреты в diff | нет | этап 6 | н/п — долг |', encoding='utf-8')
        self.assertEqual(self.run_cli()[0], 1)

    def test_escaped_pipe_in_closed_debt_is_not_a_column(self):
        self.write(f'`"{Path(sys.executable).as_posix()}" -c "pass"`')
        path = self.root / '.sdlc/gates.md'
        valid = path.read_text(encoding='utf-8')
        for reason in ('A и B', r'A \| B'):
            path.write_text(valid + '\n| Секреты в diff | нет | этап 6 | н/п — долг |\n\n'
                            '## Долг\n\n| Гейт | Где должен стоять | Как закрывается | Дата | Кто | Область | Свидетельство / условие пересмотра |\n'
                            '|---|---|---|---|---|---|---|\n'
                            f'| Секреты в diff | этап 6 | риск принят: {reason} | 2026-11-09 | Оператор | проект | пересмотр: изменение профиля |',
                            encoding='utf-8')
            self.assertEqual(self.run_cli()[0], 0, reason)

    def test_unknown_state_turns_green_flow_verdict_red(self):
        source = ROOT / 'test/flow-verdict-corpus/green/.sdlc'
        shutil.copytree(source, self.root / '.sdlc', dirs_exist_ok=True)
        args = [sys.executable, str(TOOLS / 'flow-verdict.py'), '--at', 'verify', str(self.root), 'green']
        before = subprocess.run(args, capture_output=True, encoding='utf-8')
        self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
        gates = self.root / '.sdlc/gates.md'
        gates.write_text(gates.read_text(encoding='utf-8').replace(
            '\n\n## Долг', '\n| Секреты в diff | настройка | этап 6 | описание |\n\n## Долг', 1), encoding='utf-8')
        after = subprocess.run(args, capture_output=True, encoding='utf-8')
        self.assertEqual(after.returncode, 1, after.stdout + after.stderr)
        self.assertIn('Неизвестное значение', after.stdout)


if __name__ == '__main__':
    unittest.main()
