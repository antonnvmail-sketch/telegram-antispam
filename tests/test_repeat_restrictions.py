import asyncio
import json
from contextlib import closing
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import RestrictChatMember

from admin_review import AdminReview, RESTRICTION_SECONDS, Tickets, restriction_subject


class RepeatRestrictionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tickets = Tickets(Path(folder.name) / 'review.sqlite')
        self.bot = SimpleNamespace(
            delete_message=AsyncMock(return_value=True),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status='member')),
            restrict_chat_member=AsyncMock(return_value=True),
        )
        self.service = AdminReview(self.bot, self.tickets, [1], [-100, -200], repeat_restrictions=True)

    async def mark(self, mid, chat=-100, subject=50, reason='spam_command'):
        token = self.tickets.claim_direct_spam(chat, mid, 1, 'Synthetic spam', reason=reason, subject=subject)
        if token:
            return await self.service.delete_claimed(token, chat, mid, origin=reason)

    def outcomes(self):
        with closing(self.tickets.connect()) as db:
            return [json.loads(row[0])['outcome'] for row in db.execute(
                "SELECT value FROM review_meta WHERE key LIKE 'repeat_restriction:%'"
            )]

    async def test_command_and_review_count_once_in_same_group_across_service_restart(self):
        await self.mark(1)
        self.assertIsNone(await self.mark(1))
        await self.mark(2, chat=-200)
        await self.mark(2, subject=51)
        self.tickets.record_auto_delete(-100, 3)
        self.bot.restrict_chat_member.assert_not_awaited()
        self.tickets = Tickets(self.tickets.path)
        self.service = AdminReview(self.bot, self.tickets, [1], [-100, -200], repeat_restrictions=True)
        card = self.tickets.create(-100, 4, 1, 'Other spam', {'restriction_subject': 50})
        self.tickets.bind(card, 88)
        query = SimpleNamespace(from_user=SimpleNamespace(id=1), data='rv:spam:' + card,
                                answer=AsyncMock(), message=SimpleNamespace(
                                    chat=SimpleNamespace(type='private', id=1), message_id=88,
                                    edit_text=AsyncMock()))
        with patch('admin_review.time.time', return_value=1_800_000_000):
            # Move the card expiry along with the synthetic clock.
            with closing(self.tickets.connect()) as db, db:
                db.execute('UPDATE tickets SET expires=? WHERE token=?', (1_800_003_600, card))
            await self.service.callback(query)
            await self.service.callback(query)
            await self.mark(5)
        request = self.bot.restrict_chat_member.await_args
        self.bot.restrict_chat_member.assert_awaited_once()
        self.assertEqual(request.args, (-100, 50))
        self.assertEqual(request.kwargs['until_date'], 1_800_000_000 + RESTRICTION_SECONDS)
        self.assertFalse(request.kwargs['permissions'].can_send_messages)
        self.assertIn('72 часа', query.message.edit_text.await_args.args[0])
        self.assertEqual(self.outcomes(), ['restricted'])

    async def test_protected_roles_unknown_author_and_dry_run(self):
        for index, role in enumerate(('administrator', 'creator', 'restricted'), 1):
            self.bot.get_chat_member.return_value.status = role
            await self.mark(index * 10, subject=index)
            await self.mark(index * 10 + 1, subject=index)
        await self.mark(41, subject=None)
        await self.mark(42, subject=None)
        self.bot.get_chat_member.side_effect = TimeoutError()
        await self.mark(51)
        await self.mark(52)
        self.bot.get_chat_member.side_effect = None
        self.bot.get_chat_member.return_value.status = 'member'
        await self.mark(53)
        self.service = AdminReview(self.bot, self.tickets, [1], [-100], dry_run=True, repeat_restrictions=True)
        await self.mark(54)
        self.bot.restrict_chat_member.assert_not_awaited()
        author = SimpleNamespace(id=50, is_bot=False)
        self.assertIsNone(restriction_subject(SimpleNamespace(
            from_user=author, sender_chat=object(), chat=SimpleNamespace(type='supergroup'))))
        self.assertIsNone(restriction_subject(SimpleNamespace(
            from_user=author, sender_chat=None, chat=SimpleNamespace(type='group'))))

    async def test_concurrent_confirmations_and_api_errors(self):
        method = RestrictChatMember(chat_id=-100, user_id=50, permissions={'can_send_messages': False})
        for index, (error, outcome) in enumerate((
            (TelegramBadRequest(method=method, message='not enough rights'), 'rejected'),
            (TimeoutError(), 'unknown'),
        )):
            self.bot.restrict_chat_member.reset_mock()
            self.bot.restrict_chat_member.side_effect = error
            subject = 70 + index
            await self.mark(index * 10 + 1, subject=subject)
            await asyncio.gather(self.mark(index * 10 + 2, subject=subject),
                                 self.mark(index * 10 + 3, subject=subject))
            self.bot.restrict_chat_member.assert_awaited_once()
            self.assertIn(outcome, self.outcomes())

    async def test_corrected_message_does_not_count(self):
        token = self.tickets.create(-100, 1, 1, 'Ordinary message', {'restriction_subject': 50})
        self.tickets.bind(token, 88)
        self.tickets.claim(token, 1, 1, 88, [-100], 'spam')
        await self.service.delete_claimed(token, -100, 1)
        correction = self.tickets.create(-100, 1, 1, 'Ordinary message', kind='deleted')
        self.tickets.bind(correction, 89)
        self.tickets.claim(correction, 1, 1, 89, [-100], 'ham')
        await self.mark(2)
        self.bot.restrict_chat_member.assert_not_awaited()
        await self.mark(3)
        self.bot.restrict_chat_member.assert_awaited_once()

    async def test_release_only_own_restriction_and_keep_audit(self):
        await self.mark(1)
        await self.mark(2)
        key = 'repeat_restriction:-100:50'
        before = self.tickets.restriction_state(key)
        self.bot.get_chat_member.return_value = SimpleNamespace(
            status='restricted', until_date=before['until'] + 5)
        await self.service.release_repeat_restriction(-100, 50, 1)
        self.bot.restrict_chat_member.assert_awaited_once()
        self.bot.get_chat_member.return_value.until_date = before['until']
        response = await self.service.release_repeat_restriction(-100, 50, 1)
        self.assertIn('Ограничение снято', response)
        self.assertTrue(self.bot.restrict_chat_member.await_args.kwargs['permissions'].can_send_messages)
        state = self.tickets.restriction_state(key)
        self.assertEqual(state['outcome'], 'restricted')
        self.assertEqual(state['release']['outcome'], 'released')
        await self.service.release_repeat_restriction(-100, 50, 1)
        self.assertEqual(self.bot.restrict_chat_member.await_count, 2)
