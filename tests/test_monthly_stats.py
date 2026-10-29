from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace as N
import tempfile
import unittest
from unittest.mock import AsyncMock
from monthly_stats import monthly_report,format_report,csv_report,MSK
from admin_review import Tickets,AdminReview,make_router

def stamp(text):return datetime.fromisoformat(text).timestamp()

class CalendarTests(unittest.TestCase):
    def test_calendar_boundaries_use_moscow_not_utc_or_rolling_days(self):
        start=stamp('2026-08-01T00:00:00+03:00')
        events=[dict(origin='auto',at=stamp(t)) for t in ['2026-08-31T20:59:59+00:00','2026-08-31T21:00:00+00:00','2026-09-30T21:00:00+00:00']]
        r=monthly_report(events,[],start,datetime(2026,10,29,15,tzinfo=MSK))
        self.assertEqual([m['month'] for m in r['months']],['2026-08','2026-09','2026-10'])
        self.assertEqual([m['auto_deleted'] for m in r['months']],[1,1,1])
        self.assertEqual([m['current'] for m in r['months']],[False,False,True])
    def test_current_month_and_midmonth_collection_explicit_missing_prior_data(self):
        r=monthly_report([],[],stamp('2026-10-29T11:15:00+03:00'),datetime(2026,10,29,15,tzinfo=MSK))
        self.assertIsNone(r['months'][0]['auto_deleted']);self.assertIsNone(r['months'][1]['auto_deleted'])
        self.assertEqual(r['months'][2]['auto_deleted'],0)
        text=format_report(r)
        self.assertIn('Октябрь 2026 — месяц не завершён',text);self.assertIn('29.10.2026 11:15',text)
        self.assertIn('Данных нет',text);self.assertFalse(r['is_model_accuracy'])
    def test_year_transition_leap_month_and_first_instant_of_month(self):
        r=monthly_report([],[],stamp('2025-01-01T00:00:00+03:00'),datetime(2026,1,1,tzinfo=MSK))
        self.assertEqual([m['month'] for m in r['months']],['2025-11','2025-12','2026-01'])
        r=monthly_report([dict(origin='manual',at=stamp('2024-02-29T23:59:59+03:00'))],[],stamp('2024-01-01T00:00:00+03:00'),datetime(2024,3,1,tzinfo=MSK))
        self.assertEqual(r['months'][1]['manual_deleted'],1);self.assertEqual(r['months'][2]['manual_deleted'],0)
    def test_feedback_counted_in_month_of_answer_not_month_of_deletion(self):
        r=monthly_report([dict(origin='auto',at=stamp('2026-09-30T23:59:00+03:00'))],
             [dict(label='HAM',reason='false_positive_after_deletion',at=stamp('2026-10-01T00:01:00+03:00')),
              dict(label='HAM',reason='explicit_review',at=stamp('2026-10-02T00:01:00+03:00'))],
              stamp('2026-08-01T00:00:00+03:00'),datetime(2026,10,29,tzinfo=MSK))
        self.assertEqual(r['months'][1]['auto_deleted'],1);self.assertEqual(r['months'][2]['false_positive_reports'],1)
        self.assertEqual(r['months'][2]['reviewed_ham'],1)
    def test_csv_is_aggregated_and_marks_partial_month(self):
        r=monthly_report([],[],stamp('2026-10-15T00:00:00+03:00'),datetime(2026,10,29,tzinfo=MSK))
        content=csv_report(r).decode('utf-8-sig')
        self.assertEqual(len(content.splitlines()),4);self.assertIn('2026-10',content)
        self.assertNotIn('chat_id',content);self.assertNotIn('text_sha256',content)
        self.assertTrue(csv_report(r).startswith(b'\xef\xbb\xbf'))
    def test_auto_deletion_deduplicates_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.sqlite';store=Tickets(path)
            store.record_auto_delete(-100,7);store.record_auto_delete(-100,7)
            self.assertEqual(Tickets(path).stats()['months'][-1]['auto_deleted'],1)

class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_stat_and_export_only_private_admin_and_no_original_text(self):
        with tempfile.TemporaryDirectory() as directory:
            bot=N(send_document=AsyncMock());service=AdminReview(bot,Tickets(Path(directory)/'state.sqlite'),[7],[-100])
            router=make_router(service)
            stat=router.message.handlers[2].callback;export=router.message.handlers[3].callback
            for actor,chat_type in [(8,'private'),(7,'supergroup')]:
                m=N(from_user=N(id=actor),chat=N(id=actor,type=chat_type),reply=AsyncMock())
                await stat(m);await export(m);m.reply.assert_not_awaited()
            bot.send_document.assert_not_awaited()
            m=N(from_user=N(id=7),chat=N(id=7,type='private'),reply=AsyncMock())
            await stat(m);await export(m);m.reply.assert_awaited_once()
            file=bot.send_document.call_args.args[1];self.assertTrue(file.filename.endswith('.csv'))
            self.assertNotIn(b'chat_id',file.data)

if __name__=='__main__':unittest.main()
