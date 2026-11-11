"""Синтетические квитанции для проверок парсера. Не используются в рабочем процессе."""

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'implementations/claude-code/skills/sdlc-verify/tools'
sys.path.insert(0, str(TOOLS))
import chunk_preconditions
import snapshot_base


def parser_receipt(d, n=1):
    root = d.parents[1]
    base, _ = snapshot_base.load(root, d)
    locator = d / f'locator-{n}.md'
    locator.write_text('Синтетический отчёт: точка правки указана в плане.\n', encoding='utf-8')
    baseline = d / f'.chunk-{n}-baseline.json'
    baseline.write_text('{}\n', encoding='utf-8')
    archived = d / f'.chunk-{n}-plan.md'
    archived.write_bytes((d / 'plan.md').read_bytes())
    record = {'version': 1, 'tool': 'attempt-evidence.py prepare', 'status': 'ready',
              'slug': d.name, 'chunk': n, 'base': base,
              'plan_sha256': chunk_preconditions.text_digest(d / 'plan.md'),
              'locator': {'path': locator.name, 'sha256': chunk_preconditions.text_digest(locator)},
              'baseline_sha256': chunk_preconditions.text_digest(baseline),
              'initial_diff_sha256': snapshot_base.digest(b''), 'recorded_at': '2000-01-01T00:00:00+00:00'}
    record['plan_path'] = archived.name
    journal = d / f'.chunk-{n}-initial-journal.md'
    journal.write_bytes((d / f'chunk-{n}-journal.md').read_bytes())
    record['journal'] = {'path': journal.name, 'sha256': chunk_preconditions.text_digest(journal)}
    intent = d / 'intent.md'
    record['contour'] = chunk_preconditions.contour(intent.read_text(encoding='utf-8') if intent.exists() else '')
    # SHA квитанции должен сохраняться после Git-нормализации в чистом checkout.
    chunk_preconditions.receipt_path(d, n).write_bytes((json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
