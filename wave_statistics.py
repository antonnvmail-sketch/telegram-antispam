"""Read-only JSON projection for a future AST Console API."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 1
MAX_PAGE_SIZE = 100
SIGNAL_TYPES = {'Сделка USDT': 'direct_usdt_trade', 'Спам по модели': 'model_spam'}


def _group(key):
    return key.partition(':')[0]


def _counts(messages, corrected, restrictions):
    outcomes = Counter(message.get('deletion') for message in messages.values())
    restricted = sum(item.get('outcome') in {'restricted', 'released'} for item in restrictions)
    return {
        'detected': len(messages),
        'deleted': outcomes['deleted'],
        'delete_rejected': outcomes['rejected'],
        'delete_unknown': outcomes['unknown'],
        'delete_skipped': outcomes['skipped'],
        'delete_pending': outcomes['pending'],
        'delete_not_attempted': outcomes['not_attempted'],
        'message_corrections': len(corrected),
        'retained_detections': len(messages) - len(corrected),
        'retained_deletions': sum(key not in corrected and message.get('deletion') == 'deleted'
                                  for key, message in messages.items()),
        'restricted': restricted,
        'restrictions_released': sum(item.get('outcome') == 'released' for item in restrictions),
    }


def _project(wave_id, report, groups):
    source = report['messages']
    messages = {key: value for key, value in source.items() if _group(key) in groups}
    if not messages:
        return None
    corrected = {item.get('message') for item in report.get('corrections', ())} & messages.keys()
    restrictions = [item for key, item in report.get('restrictions', {}).items() if _group(key) in groups]
    complete = len(source) == len(messages)
    dates = [message.get('published_at') for message in messages.values()]
    dated = all(type(date) is int for date in dates)
    return {
        'wave_id': wave_id,
        'group_ids': sorted({_group(key) for key in messages}),
        'closed': bool(report['closed']) if complete else None,
        'first_published_at': min(dates) if dated else None,
        'last_published_at': max(dates) if dated else None,
        'duration_seconds': max(dates) - min(dates) if dated else report.get('duration') if complete else None,
        'signal_types': sorted({SIGNAL_TYPES.get(item.get('type'), 'unknown') for item in messages.values()}),
        **_counts(messages, corrected, restrictions),
    }


def _with_rate(counts):
    retained = counts['retained_detections']
    return {**counts, 'deletion_rate': counts['retained_deletions'] / retained if retained else None}


def read_wave_statistics(database, allowed_group_ids, *, limit=50, offset=0):
    """The caller supplies groups from its authenticated account scope."""
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or type(offset) is not int or offset < 0:
        raise ValueError('Expected limit 1–100 and a non-negative offset')
    groups = {str(group) for group in allowed_group_ids}
    waves = []
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        available = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_meta'").fetchone())
        if available and groups:
            for key, value in db.execute("SELECT key, value FROM review_meta WHERE key GLOB 'wave_report:*'"):
                wave = _project(key.split(':', 1)[1], json.loads(value), groups)
                if wave is not None:
                    waves.append(wave)

    waves.sort(key=lambda wave: (wave['first_published_at'] is not None,
                                 wave['first_published_at'] or 0, wave['wave_id']), reverse=True)
    totals = {key: sum(wave[key] for wave in waves) for key in _counts({}, set(), ())}
    page = waves[offset:offset + limit]
    return {
        'schema_version': SCHEMA_VERSION,
        'available': available,
        'summary': {'waves': len(waves), **_with_rate(totals)},
        'waves': [_with_rate(wave) for wave in page],
        'next_offset': offset + limit if offset + limit < len(waves) else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True, help='Path to the existing admin-review SQLite database')
    parser.add_argument('--group', action='append', type=int, required=True, help='Authorized Telegram group ID, repeatable')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--offset', type=int, default=0)
    args = parser.parse_args()
    try:
        result = read_wave_statistics(args.db, args.group, limit=args.limit, offset=args.offset)
    except (ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
