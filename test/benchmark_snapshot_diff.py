"""Измерь прежний и сокращённый snapshot diff на одном синтетическом дереве."""

import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test.test_snapshot_base import SnapshotTests
from test.snapshot_diff_reference import full_snapshot_diff
import snapshot_base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', type=int, default=128)
    parser.add_argument('--bytes', type=int, default=65536)
    args = parser.parse_args()
    fixture = SnapshotTests()
    fixture.setUp()
    try:
        for number in range(args.files):
            (fixture.root / f'keep-{number}.bin').write_bytes(number.to_bytes(8, 'little') + b'x' * args.bytes)
        fixture.snapshot()
        (fixture.root / 'app.py').write_bytes(b'answer = 2\r\n')
        materialize = snapshot_base.materialize
        results, patches = {}, []
        for name, algorithm in [('full', full_snapshot_diff), ('changed_only', snapshot_base.snapshot_diff)]:
            count = {'files_written': 0, 'bytes_written': 0}
            def measured(files, destination):
                count['files_written'] += len(files)
                count['bytes_written'] += sum(len(data) for data, _ in files.values())
                materialize(files, destination)
            start = time.perf_counter()
            with patch.object(snapshot_base, 'materialize', side_effect=measured):
                _, base = snapshot_base.load(fixture.root, fixture.d)
                patches.append(algorithm(fixture.root, base))
            results[name] = dict(count, elapsed_seconds=round(time.perf_counter() - start, 6))
        results['patches_equal'] = patches[0] == patches[1]
        results['scope'] = 'Один прогон каждого алгоритма, одна среда и дерево; включает load и проверку всех blob-хэшей.'
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if results['patches_equal'] else 1
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    sys.exit(main())
