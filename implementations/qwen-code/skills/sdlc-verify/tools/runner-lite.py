"""Выполни механические операции витка через установленные инструменты."""

import argparse
import os
import json
import re
import subprocess
import sys
from pathlib import Path

import gate_evidence
import chunk_preconditions
from sdlc_common import ARTIFACTS_DIR, command_of, enabled_gates, read


def run(script, *args):
    subprocess.run([sys.executable, str(Path(__file__).with_name(script)), *map(str, args)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    for name in ('prepare', 'check', 'review', 'verdict'):
        command = commands.add_parser(name)
        command.add_argument('root', type=Path)
        command.add_argument('slug')
        if name != 'verdict':
            command.add_argument('chunk', type=int)
        if name in ('check', 'review'):
            command.add_argument('attempt', type=int)
        if name == 'prepare':
            command.add_argument('--locator', required=True)
            command.add_argument('--builtin-review', action='store_true')
        if name == 'check':
            command.add_argument('--executor-model', required=True)
            command.add_argument('--layer', help='Слой проверки, объявленный в контексте команды')
        if name == 'review':
            command.add_argument('answer', type=Path)
    args = parser.parse_args()
    root = Path(os.path.abspath(args.root))
    d = root / ARTIFACTS_DIR / args.slug
    if (not re.fullmatch(r'[\w.-]+', args.slug) or args.slug in ('.', '..')
            or d.resolve().parent != (root / ARTIFACTS_DIR).resolve()
            or min(getattr(args, 'attempt', 1), getattr(args, 'chunk', 1)) < 1):
        parser.error('Недопустимый адрес витка или попытки')
    cycle = (root, args.slug)
    tool = 'attempt-evidence.py'
    if args.operation == 'prepare':
        flags = ['--builtin-review'] if args.builtin_review else []
        run('gates-preflight.py', root, '--slug', args.slug, *flags)
        if chunk_preconditions.receipt_path(d, args.chunk).exists():
            problems = chunk_preconditions.errors(root, d, args.chunk)
            if problems:
                parser.exit(1, '\n'.join(problems) + '\n')
        else:
            run(tool, 'prepare', *cycle, args.chunk, '--locator', args.locator)
    elif args.operation == 'verdict':
        run('flow-verdict.py', *cycle, '--at', 'verify')
    else:
        attempt = (*cycle, args.chunk, args.attempt)
        problems = chunk_preconditions.errors(root, d, args.chunk)
        if problems:
            parser.exit(1, '\n'.join(problems) + '\n')
        record = d / f'chunk-{args.chunk}-attempt-{args.attempt}-evidence.json'
        if args.operation == 'check':
            layer = ['--layer', args.layer] if args.layer else []
            if record.exists():
                if json.loads(record.read_bytes()).get('layer') != args.layer:
                    parser.exit(1, 'Слой отличается от записанной попытки. Укажи исходный слой.\n')
            else:
                run(tool, 'write', *attempt, '--resume', '--executor-model', args.executor_model, *layer)
        run(tool, 'verify', *attempt)
        if args.operation == 'review':
            run('review-validate.py', *attempt, args.answer)
        else:
            for name, _, how in enabled_gates(read(d.parent / 'gates.md', ''), 'этап 6'):
                if name in ('Сборка', 'Тесты') or gate_evidence.needs_receipt(name, command_of(how)):
                    if gate_evidence.errors(root, d, args.chunk, args.attempt, name):
                        run(tool, 'run-gate', *attempt, '--gate', name, *layer)
                        problems = gate_evidence.errors(root, d, args.chunk, args.attempt, name)
                        if problems:
                            parser.exit(1, '\n'.join(problems) + '\n')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
