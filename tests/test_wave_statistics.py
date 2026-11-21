from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from wave_statistics import read_wave_statistics


class WaveStatisticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'review.sqlite'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE review_meta(key TEXT PRIMARY KEY, value TEXT)')

    def write(self, wave_id, messages, *, corrections=(), restrictions=None):
        report = dict(messages=messages, corrections=corrections, restrictions=restrictions or {},
                      closed=True, duration=29, deliveries={'777': {'text': 'private notification'}})
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO review_meta VALUES(?,?)', (f'wave_report:{wave_id}', json.dumps(report)))

    def test_scoped_totals_and_page_do_not_expose_other_groups_or_content(self):
        self.write('recent', {
            '-100:1': {'deletion': 'deleted', 'type': 'Спам по модели', 'published_at': 200, 'text': 'private'},
            '-100:2': {'deletion': 'unknown', 'type': 'Спам по модели', 'published_at': 212},
            '-999:3': {'deletion': 'deleted', 'type': 'Сделка USDT', 'published_at': 500},
        }, corrections=[{'message': '-100:1', 'actor': 777}, {'message': '-999:3', 'actor': 888}],
            restrictions={'-100:44': {'outcome': 'released'}, '-999:55': {'outcome': 'restricted'}})
        self.write('old', {'-100:4': {'deletion': 'deleted', 'published_at': 100}})
        self.write('excluded', {'-999:5': {'deletion': 'deleted', 'published_at': 900}})
        before = self.path.read_bytes()
        export = read_wave_statistics(self.path, [-100], limit=1)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(export['schema_version'], 1)
        self.assertEqual(export['summary']['waves'], 2)
        self.assertEqual(export['summary']['detected'], 3)
        self.assertEqual(export['summary']['deleted'], 2)
        self.assertEqual(export['summary']['deletion_rate'], 0.5)
        self.assertEqual(export['next_offset'], 1)
        wave, = export['waves']
        self.assertEqual(wave['wave_id'], 'recent')
        self.assertEqual(wave['group_ids'], ['-100'])
        self.assertEqual(wave['last_published_at'], 212)
        self.assertEqual(wave['duration_seconds'], 12)
        self.assertIsNone(wave['closed'])
        self.assertEqual(wave['signal_types'], ['model_spam'])
        self.assertEqual(wave['restricted'], 1)
        self.assertEqual(wave['restrictions_released'], 1)
        self.assertEqual(wave['delete_unknown'], 1)
        self.assertEqual(wave['retained_deletions'], 0)
        serialized = json.dumps(export)
        for private in ('private', '-999', '777', '888', 'excluded'):
            self.assertNotIn(private, serialized)
        self.assertEqual(read_wave_statistics(self.path, [-100], limit=1, offset=1)['waves'][0]['wave_id'],
                         'old')

    def test_legacy_reports_and_missing_source(self):
        self.write('legacy', {'-100:1': {'deletion': 'pending'}})
        wave, = read_wave_statistics(self.path, [-100])['waves']
        self.assertIsNone(wave['first_published_at'])
        self.assertEqual(wave['duration_seconds'], 29)
        self.assertTrue(wave['closed'])
        self.assertEqual(wave['delete_pending'], 1)
        self.assertEqual(read_wave_statistics(self.path, [])['waves'], [])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TABLE review_meta')
        export = read_wave_statistics(self.path, [-100])
        self.assertFalse(export['available'])
        self.assertIsNone(export['summary']['deletion_rate'])
        missing = self.path.with_name('missing.sqlite')
        with self.assertRaises(sqlite3.OperationalError):
            read_wave_statistics(missing, [-100])
        self.assertFalse(missing.exists())
