"""Два независимых сценария: восстановление локального документа и пакетный помощник."""

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test import test_snapshot_base as fixtures
sys.path.insert(0, str(fixtures.TOOLS))
import runtime_session
import runtime_protocol
import verification_runs
import profile_guard
import snapshot_base


class RuntimeTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git

    def helper(self, failure=False, extra='', batch=False):
        operation = ('sum([1,2,3])' if batch else '7')
        code = ('import os,json,pathlib,datetime; '
                'd=pathlib.Path(os.environ["SDLC_RUNTIME_DIAGNOSTICS"]); '
                'sid=os.environ["SDLC_RUNTIME_SESSION_ID"]; '
                'now=datetime.datetime.now(datetime.timezone.utc).isoformat(); '
                '(d/"ready.json").write_text(json.dumps({"sessionId":sid,"observedAt":now,"state":"ready"})); '
                '(d/"sample.json").write_text(json.dumps({"sessionId":sid,"observedAt":now,"value":' + operation + '})); '
                '(pathlib.Path(os.environ["TMP"])/"owned.tmp").write_text("temporary"); '
                + extra + f'raise SystemExit({int(failure)})')
        return runtime_session.launch(self.root, self.root.parent / (self.root.name + '-runtime'), sys.executable,
                                      ['-c', code], timeout=20)

    def spec(self, name='document', restart=False):
        return {'runId': name, 'claim_id': 'claim-1',
                'revision': {'head_sha': self.base, 'diff_sha256': 'a' * 64,
                             'source_sha256': runtime_session.tree_digest(runtime_session.source_tree(self.root))},
                'initial_state': {'value': 7}, 'environment': {'os': 'test', 'viewport': [800, 600]},
                'inputs': ['Открыть локальный документ', 'Прочитать значение'], 'expected': 'value=7',
                'procedure': 'Сверить первичный JSON', 'readiness_state': 'ready', 'material_conditions': {'role': 'operator'},
                'nonmaterial_reasons': {}, 'preparation': ['Фиксация камеры не доказывает визуальную приёмку'],
                'restoration': ['Повторно проверить условия'], 'restart_required': restart}

    def observations(self, spec, sessions):
        result = {'revision': spec['revision'], 'sessions': [str(s) for s, _ in sessions],
                  'conditions': spec['material_conditions'], 'outcome': 'success', 'ready': {}, 'primary': []}
        for directory, ended in sessions:
            sid = ended['sessionId']
            path = directory / 'diagnostics/ready.json'
            result['ready'][sid] = {'path': str(path), 'sha256': runtime_protocol.pin(path),
                                   'expected': 'ready', 'conditions_rechecked': True}
            primary = directory / 'diagnostics/sample.json'
            result['primary'].append({'path': str(primary), 'sha256': runtime_protocol.pin(primary),
                                      'sessionId': sid, 'observedAt': json.loads(primary.read_bytes())['observedAt']})
        return result

    def tearDown(self):
        import shutil
        storage = self.root.parent / (self.root.name + '-runtime')
        if storage.exists():
            shutil.rmtree(storage)

    def test_success_failure_diagnostics_source_change_and_cleanup(self):
        for failure in (False, True):
            session, ended = self.helper(failure)
            self.assertEqual(ended['exitCode'], int(failure))
            self.assertEqual(ended['isolation'], 'observed_clean')
            runtime_protocol.session(session)
            runtime_session.cleanup_temporary(session.parent, ended['sessionId'])
            self.assertTrue((session / 'diagnostics/sample.json').is_file())
            self.assertFalse((session / 'temporary').exists())
        session, ended = self.helper(extra=f'pathlib.Path({str(self.root / "app.py")!r}).write_text("changed"); ')
        self.assertEqual(ended['source_changes'], ['app.py'])
        self.assertEqual(ended['isolation'], 'violated')

    def test_start_error_path_escape_and_foreign_temporary(self):
        session, ended = runtime_session.launch(self.root, self.root.parent / (self.root.name + '-runtime'),
                                                self.root / 'missing.exe')
        self.assertIsNotNone(ended['launchError'])
        self.assertIsNone(ended['process'])
        with self.assertRaises(ValueError):
            runtime_session.launch(self.root, self.root / 'runtime', sys.executable)
        session, ended = self.helper()
        (session / 'temporary/foreign.tmp').write_text('foreign')
        with self.assertRaises(ValueError):
            runtime_session.cleanup_temporary(session.parent, ended['sessionId'])
        self.assertTrue((session / 'temporary/foreign.tmp').is_file())
        with self.assertRaises(ValueError):
            runtime_session.cleanup_temporary(session.parent, '../escape')

    def test_foreign_empty_directory_and_failed_started_receipt(self):
        session, ended = self.helper()
        (session / 'temporary/foreign-empty').mkdir()
        with self.assertRaises(ValueError):
            runtime_session.cleanup_temporary(session.parent, ended['sessionId'])
        self.assertTrue((session / 'temporary/foreign-empty').is_dir())
        original = runtime_session.write
        processes = []
        original_popen = runtime_session.subprocess.Popen
        def start(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process
        def fail_started(path, value):
            if path.name == 'started.json':
                raise OSError('Контроль отказа записи квитанции')
            return original(path, value)
        with patch.object(runtime_session, 'write', side_effect=fail_started), patch.object(runtime_session.subprocess, 'Popen', side_effect=start):
            directory, result = runtime_session.launch(self.root, session.parent, sys.executable, ['-c', 'import time;time.sleep(10)'])
        self.assertIsNotNone(result['launchError'])
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_external_diagnostics_keep_equal_bytes_and_overlapping_roots(self):
        # Каталог диагностики находится вне исходников проверяемого репозитория.
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            external = Path(temp)
            (external / 'nested').mkdir()
            (external / 'foreign.log').write_text('foreign', encoding='utf-8')
            code = ('import os,pathlib; sid=os.environ["SDLC_RUNTIME_SESSION_ID"]; '
                    f'p=pathlib.Path({str(external)!r}); '
                    '(p/"one.log").write_text(sid); (p/"nested/two.log").write_text(sid)')
            session, ended = runtime_session.launch(self.root, self.root.parent / (self.root.name + '-runtime'),
                sys.executable, ['-c', code], diagnostic_roots=[external, external / 'nested', external])
            attachments = ended['external_diagnostics']
            self.assertEqual(len(attachments), 2)
            self.assertEqual(len({item['path'] for item in attachments}), 2)
            self.assertEqual(len({item['sha256'] for item in attachments}), 1)
            self.assertEqual((external / 'foreign.log').read_text(encoding='utf-8'), 'foreign')
            runtime_protocol.session(session)

    def test_two_scenarios_comparison_and_restart(self):
        for name in ('document', 'batch-helper'):
            spec = self.spec(name, restart=True)
            batch = name == 'batch-helper'
            if batch:
                spec['initial_state'] = {'batch': [1, 2, 3]}
                spec['inputs'] = ['Вычислить сумму пакета', 'Сверить результат помощника после restart']
                spec['expected'] = 'sum=6'
                spec['procedure'] = 'Сверить вычисленную сумму с независимым ожиданием'
            directory = self.d / name
            runtime_protocol.declare(directory, spec)
            sessions = [self.helper(batch=batch), self.helper(batch=batch)]
            observations = self.observations(spec, sessions)
            result = runtime_protocol.finish(directory, observations)
            self.assertEqual(result['acceptance'], 'requires_review')
            self.assertTrue(runtime_protocol.compare(directory, directory)['comparable'])
            self.assertNotEqual(sessions[0][1]['process']['instanceId'], sessions[1][1]['process']['instanceId'])
            for session, _ in sessions:
                observed = json.loads((session / 'diagnostics/sample.json').read_bytes())
                self.assertEqual(observed['value'], 6 if batch else 7)

    def test_mode_changes_source_digest(self):
        with patch.object(snapshot_base, 'project_files', return_value={'app.py': (b'answer=1', 0o644)}):
            first = runtime_session.tree_digest(runtime_session.source_tree(self.root))
        with patch.object(snapshot_base, 'project_files', return_value={'app.py': (b'answer=1', 0o755)}):
            second = runtime_session.tree_digest(runtime_session.source_tree(self.root))
        self.assertNotEqual(first, second)

    def test_protocol_rejects_changed_conditions_not_ready_no_restart_and_tamper(self):
        directory = self.d / 'protocol'
        spec = self.spec(restart=True)
        runtime_protocol.declare(directory, spec)
        sessions = [self.helper()]
        observations = self.observations(spec, sessions)
        with self.assertRaisesRegex(ValueError, 'restart'):
            runtime_protocol.finish(directory, observations)
        sessions.append(self.helper())
        observations = self.observations(spec, sessions)
        incomplete = copy.deepcopy(observations)
        incomplete['primary'] = incomplete['primary'][:1]
        with self.assertRaisesRegex(ValueError, 'каждого сеанса'):
            runtime_protocol.finish(directory, incomplete)
        altered = copy.deepcopy(observations)
        altered['conditions']['role'] = 'other'
        with self.assertRaisesRegex(ValueError, 'условия'):
            runtime_protocol.finish(directory, altered)
        altered = copy.deepcopy(observations)
        next(iter(altered['ready'].values()))['expected'] = 'not-ready'
        with self.assertRaisesRegex(ValueError, 'не готова'):
            runtime_protocol.finish(directory, altered)
        altered = copy.deepcopy(observations)
        altered['revision']['diff_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'ревизии'):
            runtime_protocol.finish(directory, altered)
        (sessions[0][0] / 'diagnostics/sample.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'изменена'):
            runtime_protocol.finish(directory, observations)

    def test_compare_rechecks_files_and_declared_source(self):
        spec = self.spec()
        directory = self.d / 'compare'
        runtime_protocol.declare(directory, spec)
        sessions = [self.helper()]
        observations = self.observations(spec, sessions)
        runtime_protocol.finish(directory, observations)
        (sessions[0][0] / 'diagnostics/sample.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):
            runtime_protocol.compare(directory, directory)
        directory = self.d / 'changed-source'
        runtime_protocol.declare(directory, spec)
        (self.root / 'app.py').write_bytes(b'answer=42')
        sessions = [self.helper()]
        with self.assertRaisesRegex(ValueError, 'исходники'):
            runtime_protocol.finish(directory, self.observations(spec, sessions))


class VerificationRunTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git
    tool = fixtures.SnapshotTests.tool
    snapshot = fixtures.SnapshotTests.snapshot
    write = fixtures.SnapshotTests.write

    def prepare(self, command='git --version', budget=2):
        policy = {'version': 1, 'fix_id': 'fix-one', 'candidate_budget': budget,
                  'verification_run_limit': 3, 'status': 'active'}
        raw = verification_runs.encoded(policy)
        (self.d / 'verification-policy.json').write_bytes(raw)
        self.plan.write_text(self.plan.read_text(encoding='utf-8') +
            '\n**Политика повторной верификации:** ' + snapshot_base.digest(raw), encoding='utf-8')
        (self.d / 'locator.md').write_text('app.py', encoding='utf-8')
        (self.d / 'chunk-1-journal.md').write_text('Начальная подготовка', encoding='utf-8')
        self.snapshot()
        result = self.tool('attempt-evidence.py', 'prepare', self.root, 'T-1', 1, '--locator', 'locator.md')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / 'app.py').write_text('answer = 2\n')
        self.write(extra=('--tests-cmd', command))

    def test_unchanged_new_evidence_changed_test_and_chunk_budget(self):
        self.prepare()
        empty = {'files': []}
        first = verification_runs.record(self.root, self.d, 1, 1, empty)
        second = verification_runs.record(self.root, self.d, 1, 1, empty)
        self.assertEqual(first['candidate_digest'], second['candidate_digest'])
        self.assertEqual(second['candidate_attempt'], 1)
        self.assertEqual(second['verification_run_id'], 2)
        old_bytes = (self.d / 'verification-runs/run-0001.json').read_bytes()
        (self.root / 'test_app.py').write_text('assert True\n')
        self.write(2)
        third = verification_runs.record(self.root, self.d, 1, 2, empty)
        self.assertEqual(third['candidate_attempt'], 2)
        self.assertNotEqual(first['candidate_digest'], third['candidate_digest'])
        self.assertEqual((self.d / 'verification-runs/run-0001.json').read_bytes(), old_bytes)
        (self.root / 'test_app.py').write_text('assert 1\n')
        denied = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 3)
        self.assertEqual(denied.returncode, 1)
        self.assertIn('бюджет', denied.stdout)
        self.assertFalse((self.d / 'chunk-1-attempt-3-evidence.json').exists())

    def test_source_attempts_before_first_run_cannot_reset_budget(self):
        self.prepare(budget=1)
        (self.root / 'app.py').write_text('answer = 3\n')
        denied = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 2)
        self.assertEqual(denied.returncode, 1)
        self.assertIn('бюджет', denied.stdout)

    def test_new_chunk_keeps_candidate_budget(self):
        self.prepare(budget=1)
        (self.d / 'locator-2.md').write_text('app.py', encoding='utf-8')
        (self.d / 'chunk-2-journal.md').write_text('Следующий chunk того же исправления', encoding='utf-8')
        prepared = self.tool('attempt-evidence.py', 'prepare', self.root, 'T-1', 2, '--locator', 'locator-2.md')
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        (self.root / 'app.py').write_text('answer=3\n', encoding='utf-8')
        denied = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 2, 1)
        self.assertEqual(denied.returncode, 1)
        self.assertIn('бюджет', denied.stdout)

    def test_new_primary_evidence_keeps_candidate_and_history(self):
        self.prepare()
        (self.d / 'chunk-1-attempt-1-review-evidence.json').write_text('{}', encoding='utf-8')
        first = verification_runs.record(self.root, self.d, 1, 1, {'files': []})
        path = self.d / 'observation.json'
        path.write_bytes(b'{"answer": 2}')
        revision = {'head_sha': self.base, 'diff_sha256': snapshot_base.digest((self.d / 'chunk-1-attempt-1-diff.patch').read_bytes())}
        manifest = {'files': [{'path': '.sdlc/T-1/observation.json', 'claims': ['claim-1'], 'kind': 'observation',
                               'source': 'Контрольный помощник', 'sha256': snapshot_base.digest(path.read_bytes()),
                               'revision': revision}]}
        second = verification_runs.record(self.root, self.d, 1, 1, manifest)
        self.assertEqual(first['candidate_digest'], second['candidate_digest'])
        self.assertEqual(second['candidate_attempt'], 1)
        copied = self.d / 'verification-runs/run-0002/evidence-0000.bin'
        self.assertEqual(copied.read_bytes(), path.read_bytes())

    def test_repeat_failure_stops_after_restart(self):
        self.prepare(command='git rev-parse --verify absent-reference')
        for _ in range(2):
            value = verification_runs.record(self.root, self.d, 1, 1, {'files': []})
            self.assertNotEqual(value['tests_exit'], 0)
        with self.assertRaisesRegex(ValueError, 'одинакового отказа'):
            verification_runs.record(self.root, self.d, 1, 1, {'files': []})
        self.assertEqual(len(list((self.d / 'verification-runs').glob('run-*.json'))), 2)

    def test_changed_command_consumes_candidate_budget(self):
        self.prepare(budget=1)
        denied = self.tool('attempt-evidence.py', 'write', self.root, 'T-1', 1, 2, '--tests-cmd', 'git status --short')
        self.assertEqual(denied.returncode, 1)
        self.assertIn('бюджет', denied.stdout)


class ProfileTests(unittest.TestCase):
    setUp = fixtures.SnapshotTests.setUp
    git = fixtures.SnapshotTests.git

    def test_installed_prepare_rejects_noncanonical_missing_and_extra_claims(self):
        import subprocess
        source = ROOT / 'implementations/codex'
        installed = subprocess.run([sys.executable, str(source / 'install-codex.py'), '--project', str(self.root)], capture_output=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        tool = self.root / '.agents/skills/sdlc-verify/tools/attempt-evidence.py'
        env = dict(os.environ, SDLC_PROFILE_MANIFEST=str(source / 'manifest.json'))
        def run(*args):
            return subprocess.run([sys.executable, str(tool), *map(str, args)], capture_output=True, text=True, encoding='utf-8', env=env)
        self.assertEqual(run('snapshot', self.root, 'T-1').returncode, 0)
        (self.d / 'locator.md').write_text('app.py', encoding='utf-8')
        (self.d / 'chunk-1-journal.md').write_text('До первой правки', encoding='utf-8')
        original = self.plan.read_text(encoding='utf-8')
        for mapping in ('Claim-11', 'нет покрытия', 'claim-1, claim-2'):
            self.plan.write_text(original.replace('Покрытие: claim-1.', 'Покрытие: ' + mapping + '.'), encoding='utf-8')
            result = run('prepare', self.root, 'T-1', 1, '--locator', 'locator.md')
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse((self.d / '.chunk-1-preconditions.json').exists())
        self.plan.write_text(original, encoding='utf-8')
        result = run('prepare', self.root, 'T-1', 1, '--locator', 'locator.md')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_current_old_mixed_and_legacy_roots(self):
        import subprocess
        source = ROOT / 'implementations/codex'
        install = subprocess.run([sys.executable, str(source / 'install-codex.py'), '--project', str(self.root)],
                                 capture_output=True, text=True)
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
        expected = source / 'manifest.json'
        tools = self.root / '.agents/skills/sdlc-verify/tools'
        self.assertIn('manifest_sha256', profile_guard.check(self.root, expected, tools))
        with patch.dict(os.environ, {'SDLC_PROFILE_MANIFEST': str(expected)}):
            self.assertIsNotNone(profile_guard.before_prepare(tools))
            nested = self.root / 'project'
            overlay = nested / '.codex/agents'
            overlay.mkdir(parents=True)
            (overlay / 'sdlc-reviewer.toml').write_bytes(b'old')
            with self.assertRaisesRegex(ValueError, 'Конфликт'):
                profile_guard.before_prepare(tools, nested)
        target = tools / 'chunk_preconditions.py'
        original = target.read_bytes()
        target.write_bytes(b'old-version')
        with self.assertRaisesRegex(ValueError, 'Компонент'):
            profile_guard.check(self.root, expected, tools)
        target.write_bytes(original)
        record = self.root / '.codex/sdlc-installation.json'
        record.write_text(json.dumps({'version': 'legacy', 'files': []}))
        with self.assertRaisesRegex(ValueError, 'Устаревшая'):
            profile_guard.check(self.root, expected, tools)


if __name__ == '__main__':
    unittest.main()
