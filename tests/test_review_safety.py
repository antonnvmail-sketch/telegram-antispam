import asyncio
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import AsyncMock
import tempfile
import unittest
from admin_review import AdminReview,Tickets,make_router
from health_check import local_issues,notification
from setup_service import units

class ReviewSafety(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_refuses_old_callback_without_changing_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            store=Tickets(Path(d)/'review.sqlite');token=store.create(-100123,55,10,'Synthetic only');store.bind(token,90)
            bot=N(delete_message=AsyncMock(),send_message=AsyncMock())
            service=AdminReview(bot,store,[10],[-100123],dry_run=True)
            q=N(from_user=N(id=10),message=N(chat=N(id=10,type='private'),message_id=90),data='rv:spam:'+token,answer=AsyncMock())
            await service.callback(q)
            bot.delete_message.assert_not_awaited()
            self.assertEqual(store.feedback_report()['feedback'],[])
            with closing(store.connect()) as c:self.assertEqual(c.execute('SELECT state FROM tickets').fetchone()[0],'pending')
    async def test_dry_run_manual_review_does_not_send_card(self):
        with tempfile.TemporaryDirectory() as d:
            bot=N(send_message=AsyncMock());service=AdminReview(bot,Tickets(Path(d)/'review.sqlite'),[10],[-100123],dry_run=True)
            m=N(from_user=N(id=10),chat=N(id=-100123),reply_to_message=N(message_id=55,text='Synthetic'),reply=AsyncMock())
            await make_router(service).message.handlers[0].callback(m)
            bot.send_message.assert_not_awaited();self.assertIn('Пробный режим',m.reply.call_args.args[0])
            with self.assertRaises(ValueError):await service.send(10,-100123,55,'Synthetic')
    def test_quiet_chat_with_successful_poll_is_healthy(self):
        self.assertEqual(local_issues({'ActiveState':'active','MainPID':'1'},dict(pid=1,started_at=1,loop_at=100,last_poll_success=100,inflight=0),100),[])
        self.assertIsNone(notification({},[],0))
        self.assertIsNone(notification({'issues':[],'restarts':2},[],0))
    def test_units_use_selected_install_location(self):
        rendered=units(Path.cwd()/'path with spaces',Path('python'))
        self.assertEqual(len(rendered),3)
        self.assertIn('WatchdogSec=60',rendered['antispam.service'])
        self.assertIn('Restart=always',rendered['antispam.service'])
        self.assertIn('path with spaces',rendered['antispam.service'])
