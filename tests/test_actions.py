"""Offline tests: no bot imports, credentials, network, or production database."""
import ast
import asyncio
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moderation_actions import execute_actions
from decisions import decide
from database import init_db, log_action
from monitor_sc_antispam import record_batch


class BadRequest(Exception): pass
class Forbidden(Exception): pass
class NotFound(Exception): pass


class Message:
    def __init__(self, error=None, response=True):
        self.chat = SimpleNamespace(id=-100)
        self.message_id = 42
        self.text = 'synthetic test text'
        self.from_user = SimpleNamespace(id=7, username='test')
        self.error, self.response, self.deletes = error, response, 0

    async def delete(self):
        self.deletes += 1
        if self.error:
            raise self.error
        return self.response


class Capture:
    def __init__(self): self.records = []
    def info(self, text): self.records.append(json.loads(text))
    def error(self, text): self.records.append(json.loads(text))


class ActionsTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, *, message=None, label='spam', confidence=.9,
                       journal_fail=None, sends=None, admins=(1, 2), perms=None,
                       monitor_error=False, monitor_response=True):
        self.message = message or Message()
        self.actions, self.deliveries, self.batches = [], [], []
        self.logs = Capture()
        def write(action):
            self.actions.append(action)
            if journal_fail == '*' or action == journal_fail:
                raise sqlite3.OperationalError('synthetic journal failure')
        async def send(admin, text):
            self.deliveries.append((admin, text))
            if sends and sends.get(admin): raise sends[admin]
        async def permissions(chat):
            if isinstance(perms, Exception): raise perms
            return perms if perms is not None else {'can_delete_messages': True}
        def monitor(ok, failed):
            self.batches.append((ok, failed))
            if monitor_error: raise RuntimeError('synthetic monitor error')
            return monitor_response
        return await execute_actions(
            message=self.message, decision_record=decide(label, confidence,
                delete_threshold=.7, moderate_threshold=.4,
                model_version='sha256:' + 'a'*64, text=self.message.text),
            journal_decision=lambda record: True, admins=admins,
            send_message=send, write_action=write, monitor_batch=monitor,
            check_permissions=permissions, logger=self.logs,
            rejected_errors=(BadRequest, Forbidden, NotFound), bad_request_error=BadRequest)

    async def test_successful_deletion_order(self):
        result = await self.run_case()
        self.assertEqual((result.decision, result.deletion, result.notification),
                         ('delete', 'deleted', 'sent'))
        self.assertEqual(self.actions[:4], ['decision_delete', 'classified_spam',
                                           'delete_attempted', 'deleted_spam'])
        self.assertLess(self.actions.index('deleted_spam'), self.actions.index('notification_sent'))
        self.assertEqual(self.message.deletes, 1)
        self.assertEqual(self.batches, [(True, False)])

    async def test_every_journal_failure_preserves_telegram_result(self):
        for action in ['decision_delete', 'classified_spam', 'delete_attempted',
                       'deleted_spam', 'notification_sent', '*']:
            with self.subTest(action=action):
                result = await self.run_case(journal_fail=action)
                self.assertEqual(result.deletion, 'deleted')
                self.assertEqual(result.notification, 'sent')
                self.assertTrue(result.journal_failures)
                self.assertEqual(self.message.deletes, 1)
                self.assertEqual(len(self.deliveries), 2)
                self.assertTrue(any(r['stage'] == 'journal' and r['status'] == 'failed'
                                    for r in self.logs.records))
                if action == '*': self.assertEqual(len(self.actions), 1)

    async def test_rejected_delete_never_reports_deleted(self):
        for exc in (BadRequest(), Forbidden(), NotFound()):
            with self.subTest(error=type(exc).__name__):
                result = await self.run_case(message=Message(error=exc))
                self.assertEqual(result.shadow_action, 'delete_rejected')
                self.assertNotIn('deleted_spam', self.actions)
                self.assertEqual(self.deliveries, [])
                self.assertEqual(self.message.deletes, 1)

    async def test_uncertain_delete_never_retries_or_announces_success(self):
        for exc in (TimeoutError(), OSError(), RuntimeError()):
            with self.subTest(error=type(exc).__name__):
                result = await self.run_case(message=Message(error=exc))
                self.assertEqual(result.shadow_action, 'delete_unknown')
                self.assertEqual(self.deliveries, [])
                self.assertEqual(self.message.deletes, 1)
                self.assertNotIn('deleted_spam', self.actions)

    async def test_false_telegram_response_is_not_success(self):
        result = await self.run_case(message=Message(response=False))
        self.assertEqual(result.deletion, 'unknown')
        self.assertEqual(self.deliveries, [])

    async def test_partial_notifications_do_not_change_deletion(self):
        result = await self.run_case(sends={1: Forbidden()})
        self.assertEqual((result.deletion, result.notification), ('deleted', 'partial'))
        self.assertEqual(len(self.deliveries), 2)
        self.assertEqual(self.batches, [(True, True)])

    async def test_notification_timeout_is_unknown(self):
        result = await self.run_case(sends={1: TimeoutError(), 2: TimeoutError()})
        self.assertEqual((result.deletion, result.notification), ('deleted', 'unknown'))
        self.assertEqual(self.batches, [(False, True)])

    async def test_all_rejected_notifications(self):
        result = await self.run_case(sends={1: Forbidden(), 2: BadRequest()})
        self.assertEqual(result.notification, 'failed')
        self.assertEqual(result.deletion, 'deleted')
        self.assertEqual(self.batches, [(False, True)])

    async def test_moderation_is_notification_only(self):
        result = await self.run_case(confidence=.6)
        self.assertEqual(result.shadow_action, 'moderation_notification_sent')
        self.assertEqual(self.message.deletes, 0)
        self.assertEqual(len(self.deliveries), 2)

    async def test_allow_and_threshold_boundaries(self):
        for label, p, expected in [('ham', .99, 'allow'), ('spam', .4, 'allow'),
                                    ('spam', .7, 'moderate'), ('spam', .700001, 'delete')]:
            with self.subTest(label=label, p=p):
                result = await self.run_case(label=label, confidence=p)
                self.assertEqual(result.decision, expected)
                self.assertEqual(self.message.deletes, int(expected == 'delete'))
                if expected == 'allow': self.assertFalse(self.deliveries)

    async def test_no_admins_is_skipped_not_delivered(self):
        result = await self.run_case(admins=())
        self.assertEqual(result.notification, 'skipped')
        self.assertEqual(self.batches, [(False, False)])

    async def test_permission_diagnosis_distinguishes_unknown(self):
        for perms in ({'error': 'unavailable', 'can_delete_messages': False}, RuntimeError()):
            with self.subTest(perms=str(perms)):
                await self.run_case(message=Message(error=BadRequest()), perms=perms)
                self.assertFalse(self.deliveries)
                self.assertIn('permissions_unknown', self.actions)

    async def test_confirmed_missing_permission_notification(self):
        result = await self.run_case(message=Message(error=BadRequest()),
                                     perms={'can_delete_messages': False})
        self.assertEqual(result.deletion, 'rejected')
        self.assertEqual(result.notification, 'sent')
        self.assertEqual(self.batches, [])  # Original Monitor SC covers only two branches.
        self.assertTrue(all('НЕТ ПРАВ' in t for _, t in self.deliveries))

    async def test_monitor_failure_is_separate(self):
        for settings in ({'monitor_error': True}, {'monitor_response': False}):
            with self.subTest(settings=settings):
                result = await self.run_case(**settings)
                self.assertEqual((result.deletion, result.notification), ('deleted', 'sent'))
                self.assertTrue(any(r['stage'] == 'monitor' for r in self.logs.records))

    async def test_cancelled_deletion_is_unknown_and_cancellation_propagates(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.run_case(message=Message(error=asyncio.CancelledError()))
        self.assertIn('delete_unknown', self.actions)
        self.assertFalse(self.deliveries)

    async def test_cancelled_notification_preserves_deleted_result_in_journal(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.run_case(sends={1: asyncio.CancelledError()})
        self.assertIn('deleted_spam', self.actions)
        self.assertIn('notification_unknown', self.actions)
        self.assertEqual(self.message.deletes, 1)

    async def test_monitor_batch_timestamps_with_explicit_temporary_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'monitor.json'
            self.assertFalse(record_batch(False, False, path=path))
            self.assertFalse(path.exists())
            self.assertTrue(record_batch(True, True, path=path))
            state = json.loads(path.read_text())
            self.assertEqual(state['last_success_at'], state['last_failure_at'])
            self.assertTrue(record_batch(True, False, path=path))
            state2 = json.loads(path.read_text())
            self.assertEqual(state2['last_failure_at'], state['last_failure_at'])
            self.assertGreaterEqual(state2['last_success_at'], state2['last_failure_at'])

    async def test_missing_sender(self):
        message = Message(); message.from_user = None
        result = await self.run_case(message=message)
        self.assertEqual(result.deletion, 'deleted')

    async def test_real_sqlite_journal_in_temporary_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / 'test.sqlite')
            init_db(db)
            async def send(*args): pass
            async def permissions(*args): return {}
            result = await execute_actions(message=Message(), decision_record=decide('spam', .9,
                delete_threshold=.7, moderate_threshold=.4,
                model_version='sha256:' + 'a'*64, text='synthetic test text'),
                journal_decision=lambda record: True, admins=[1], send_message=send,
                write_action=lambda action: log_action(db, 7, 'test', -100, 42, action, 'synthetic'),
                monitor_batch=lambda *args: True, check_permissions=permissions,
                logger=Capture(), rejected_errors=(BadRequest, Forbidden, NotFound),
                bad_request_error=BadRequest)
            with closing(sqlite3.connect(db)) as connection:
                actions = [r[0] for r in connection.execute('select action from logs order by id')]
            self.assertIn('deleted_spam', actions)
            self.assertEqual(actions[-1], 'notification_batch_sent')
            self.assertFalse(result.journal_failures)


def extracted_handler(path, namespace):
    """Compile just the handler; never execute entrypoint/module startup code."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'filter_message')
    fn.decorator_list = []
    fn.args.args[0].annotation = None
    isolated = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    exec(compile(isolated, str(path), 'exec'), namespace)
    return namespace['filter_message']


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, path, label, confidence, *, journal_error=False, delete_error=None):
        sent, observed, actions = [], [], []
        async def send(*args): sent.append(args)
        async def observe(*args, **kwargs): observed.append(kwargs)
        async def permissions(*args): return {'can_delete_messages': True}
        def write(*args):
            actions.append(args[-2])
            if journal_error: raise sqlite3.OperationalError('synthetic')
        ns = dict(logger=logging.getLogger('test'), action_logger=Capture(), model=object(),
            predict=lambda *args: (label, confidence), DATASET_PATH='unused',
            classify_and_decide=lambda model, text, dry_run=False, **kw: decide(label, confidence,
                model_version='sha256:' + 'a'*64, text=text, **kw),
            record_decision=lambda *args: True, DRY_RUN=False, MONITOR_SC_ENABLED=True,
            log_action=write, SPAM_DELETE_THRESHOLD=.7, SPAM_MODERATE_THRESHOLD=.4,
            ADMIN_IDS=[1, 2], bot=SimpleNamespace(send_message=send),
            monitor_record_batch=lambda *args: True, check_bot_permissions=permissions,
            TelegramBadRequest=BadRequest, TelegramForbiddenError=Forbidden,
            TelegramNotFound=NotFound, execute_actions=execute_actions, observe_shadow=observe)
        msg = Message(error=delete_error)
        await extracted_handler(path, ns)(msg)
        return msg, sent, observed, actions

    async def test_expected_actions_and_shadow_probability(self):
        for label, p in [('ham', .95), ('spam', .4), ('spam', .6), ('spam', .7), ('spam', .9)]:
            with self.subTest(label=label, p=p):
                after = await self.invoke(ROOT / 'bot_new.py', label, p)
                expected_delete = label == 'spam' and p > .7
                expected_notify = label == 'spam' and p > .4
                self.assertEqual(after[0].deletes, int(expected_delete))
                self.assertEqual(len(after[1]), 2 if expected_notify else 0)
                self.assertEqual(after[2][0]['production_p_spam'], p if label == 'spam' else 1-p)
                self.assertEqual(len(after[2]), 1)

    async def test_journal_failure_keeps_shadow_and_deletion(self):
        msg, sent, observed, _ = await self.invoke(ROOT / 'bot_new.py', 'spam', .9, journal_error=True)
        self.assertEqual(msg.deletes, 1)
        self.assertEqual(len(sent), 2)
        self.assertEqual(observed[0]['production_action'], 'deleted')

    async def test_unknown_delete_reaches_shadow(self):
        _, sent, observed, _ = await self.invoke(ROOT / 'bot_new.py', 'spam', .9, delete_error=TimeoutError())
        self.assertFalse(sent)
        self.assertEqual(observed[0]['production_action'], 'delete_unknown')

    async def test_skipped_inputs_have_no_actions(self):
        for text in ['', '/command']:
            with self.subTest(text=text):
                calls = []
                async def unexpected(**kwargs): calls.append(kwargs)
                ns = dict(logger=logging.getLogger('test'), model=object(), execute_actions=unexpected)
                message = Message(); message.text = text
                await extracted_handler(ROOT / 'bot_new.py', ns)(message)
                self.assertFalse(calls)


if __name__ == '__main__':
    unittest.main(verbosity=2)
