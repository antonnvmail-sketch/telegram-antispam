import asyncio
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import AsyncMock
import tempfile
import unittest
from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError
from aiogram.methods import DeleteMessage
from admin_review import Tickets,AdminReview,make_router
from analyze_feedback import summarize

class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'tickets.sqlite'
        self.store=Tickets(self.path);self.bot=N(send_message=AsyncMock(return_value=N(message_id=90)),delete_message=AsyncMock(return_value=True))
        self.service=AdminReview(self.bot,self.store,[10,11],[-100123])
    def tearDown(self):self.temp.cleanup()
    async def card(self,admin=10):
        await self.service.send(admin,-100123,55,'Обычная <реплика>')
        data=self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        return N(from_user=N(id=admin),message=N(chat=N(id=admin,type='private'),message_id=90,edit_text=AsyncMock()),data=data,answer=AsyncMock())
    def state(self):
        with closing(self.store.connect()) as c:return c.execute('SELECT state FROM tickets LIMIT 1').fetchone()[0]
    async def test_confirm_deletes_exact_original_once_across_restart(self):
        q=await self.card();self.service.tickets=Tickets(self.path)
        await self.service.callback(q);await self.service.callback(q)
        self.bot.delete_message.assert_awaited_once_with(-100123,55);self.assertEqual(self.state(),'deleted')
        with self.assertRaises(ValueError):await self.card()
    async def test_keep_never_deletes_or_trains(self):
        q=await self.card();q.data=q.data.replace(':spam:',':keep:');await self.service.callback(q)
        self.bot.delete_message.assert_not_awaited();self.assertEqual(self.state(),'kept')
    async def test_nonadmin_wrong_recipient_wrong_notification_and_group_rejected(self):
        for changes in ['actor','recipient','notification','group']:
            q=await self.card()
            if changes=='actor':q.from_user.id=999
            if changes=='recipient':q.from_user.id=11
            if changes=='notification':q.message.message_id=91
            if changes=='group':q.message.chat.type='supergroup'
            await self.service.callback(q)
        self.bot.delete_message.assert_not_awaited()
    async def test_removed_chat_expired_and_edited_do_not_delete(self):
        q=await self.card();self.service.chats=[];await self.service.callback(q);self.service.chats=[-100123]
        with closing(self.store.connect()) as c:c.execute('UPDATE tickets SET expires=0');c.commit()
        await self.service.callback(q)
        q=await self.card();self.store.invalidate(-100123,55);await self.service.callback(q)
        self.bot.delete_message.assert_not_awaited()
    async def test_two_admins_race_one_delete(self):
        a=await self.card(10);b=await self.card(11)
        await asyncio.gather(self.service.callback(a),self.service.callback(b))
        self.bot.delete_message.assert_awaited_once()
    async def test_timeout_is_unknown_without_retry(self):
        q=await self.card();self.bot.delete_message.side_effect=TimeoutError()
        await self.service.callback(q);await self.service.callback(q)
        self.assertEqual(self.state(),'unknown');self.bot.delete_message.assert_awaited_once()
    async def test_telegram_rejection_not_success(self):
        for error in [TelegramBadRequest,TelegramForbiddenError]:
            q=await self.card();self.bot.delete_message.side_effect=error(method=DeleteMessage(chat_id=-100123,message_id=55),message='rejected')
            await self.service.callback(q);self.assertEqual(self.state(),'rejected')
    async def test_card_edit_failure_does_not_undo_deletion(self):
        q=await self.card();q.message.edit_text.side_effect=TimeoutError()
        await self.service.callback(q);self.assertEqual(self.state(),'deleted')
    async def test_router_registers_callback_and_manual_review(self):
        router=make_router(self.service);self.assertEqual(len(router.message.handlers),4);self.assertEqual(len(router.callback_query.handlers),1)
        original=N(message_id=55,text='Реплика',caption=None)
        m=N(from_user=N(id=10),chat=N(id=-100123),reply_to_message=original,reply=AsyncMock())
        await router.message.handlers[0].callback(m);self.bot.send_message.assert_awaited_once()
        self.bot.delete_message.assert_not_awaited()
    async def test_auto_deleted_false_positive_saves_exact_evidence_no_deletion(self):
        model=dict(action='delete',p_spam=.82,model_version='sha256:fixture',policy_version='fixture-v1')
        await self.service.send(10,-100123,55,'Обычный <текст>',model,kind='deleted')
        data=self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        q=N(from_user=N(id=10),message=N(chat=N(id=10,type='private'),message_id=90,edit_text=AsyncMock()),data=data,answer=AsyncMock())
        await self.service.callback(q);await self.service.callback(q)
        report=Tickets(self.path).feedback_report();self.assertEqual(len(report['feedback']),1)
        event=report['feedback'][0]
        self.assertEqual(event['text'],'Обычный <текст>');self.assertEqual(event['model'],model)
        self.assertEqual(event['label'],'HAM');self.assertEqual(event['reason'],'false_positive_after_deletion')
        self.bot.delete_message.assert_not_awaited();self.assertFalse(report['automatic_training'])
    async def test_manual_delete_can_be_corrected_without_losing_first_decision(self):
        q=await self.card();await self.service.callback(q)
        q.data=q.message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        await self.service.callback(q)
        rows=self.store.feedback_report()['feedback']
        self.assertEqual([r['label'] for r in rows],['SPAM','HAM'])
        self.bot.delete_message.assert_awaited_once()
    async def test_forged_delete_on_deleted_card_is_refused(self):
        await self.service.send(10,-100123,55,'Текст',kind='deleted')
        data=self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data.replace(':ham:',':spam:')
        q=N(from_user=N(id=10),message=N(chat=N(id=10,type='private'),message_id=90,edit_text=AsyncMock()),data=data,answer=AsyncMock())
        await self.service.callback(q);self.bot.delete_message.assert_not_awaited();self.assertEqual(self.store.feedback_report()['feedback'],[])
    async def test_ack_failure_does_not_prevent_confirmed_action(self):
        q=await self.card();q.answer.side_effect=TimeoutError()
        await self.service.callback(q);self.bot.delete_message.assert_awaited_once();self.assertEqual(self.state(),'deleted')
    async def test_failed_claim_never_deletes(self):
        q=await self.card()
        from unittest.mock import patch
        with patch.object(self.store,'claim',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):await self.service.callback(q)
        self.bot.delete_message.assert_not_awaited()
    async def test_cancellation_is_recorded_unknown_and_not_retried(self):
        q=await self.card();self.bot.delete_message.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.service.callback(q)
        self.assertEqual(self.state(),'unknown');await self.service.callback(q);self.bot.delete_message.assert_awaited_once()
    async def test_analysis_preserves_conflicting_feedback_without_automatic_training(self):
        q=await self.card();await self.service.callback(q)
        q.data=q.message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        await self.service.callback(q)
        report=summarize(self.store.feedback_report()['feedback'])
        self.assertEqual(report['response_events'],2);self.assertEqual(report['unique_source_messages'],1)
        self.assertEqual(len(report['conflicting_exact_text_hashes']),1);self.assertFalse(report['automatic_training'])
    async def test_monthly_counts_confirmed_manual_delete_only(self):
        q=await self.card();await self.service.callback(q);await self.service.callback(q)
        self.assertEqual(self.store.stats()['months'][-1]['manual_deleted'],1)
    async def test_unknown_manual_deletion_does_not_increment_deleted_count(self):
        q=await self.card();self.bot.delete_message.side_effect=TimeoutError()
        await self.service.callback(q);self.assertEqual(self.store.stats()['months'][-1]['manual_deleted'],0)
    async def test_duplicate_false_positive_cards_count_once(self):
        for _ in range(2):
            await self.service.send(10,-100123,55,'Текст',kind='deleted')
            data=self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            q=N(from_user=N(id=10),message=N(chat=N(id=10,type='private'),message_id=90,edit_text=AsyncMock()),data=data,answer=AsyncMock())
            await self.service.callback(q)
        self.assertEqual(self.store.stats()['months'][-1]['false_positive_reports'],1)

if __name__=='__main__':unittest.main()
