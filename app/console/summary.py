"""Reads the JSON summary that k6/lib/common.js writes (handleSummary)."""

import json

FIELDS = ('requests', 'throughput', 'error_rate', 'avg_ms', 'med_ms', 'p95_ms', 'max_ms',
          'peak_memory_avg', 'peak_memory_max')


def load(path: str) -> dict:
    with open(path, encoding='utf-8') as file:
        return json.load(file)


def rows(summary: dict) -> list[dict]:
    """The sustain rows: 'all' first, then one per endpoint"""
    result = []
    for row in summary.get('sustain', []):
        result.append({'endpoint': row['endpoint'], **{field: row.get(field) for field in FIELDS}})
    return result
