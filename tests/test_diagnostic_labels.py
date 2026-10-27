"""Exercise only the diagnostic handler, without importing or starting a bot."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest


def load_diagnostic(source):
    tree = ast.parse(source)
    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'cmd_test_spam')
    handler.decorator_list = []
    module = ast.Module(body=[handler], type_ignores=[])
    namespace = {'types': SimpleNamespace(Message=object), 'is_admin': lambda uid: uid == 7,
                 'model': object(), 'SPAM_DELETE_THRESHOLD': 0.7, 'SPAM_MODERATE_THRESHOLD': 0.4}
    exec(compile(ast.fix_missing_locations(module), '<diagnostic-handler>', 'exec'), namespace)
    return namespace


def verify_diagnostic(source, has_dry_run):
    namespace = load_diagnostic(source)
    for dry in ([True, False] if has_dry_run else [False]):
        namespace['DRY_RUN'] = dry
        for label, score, expected in [('spam', 0.99, 'Рекомендация: удалить'),
                                       ('spam', 0.7, 'Рекомендация: передать на модерацию'),
                                       ('ham', 0.9, 'НЕ СПАМ')]:
            replies = []
            async def reply(text):
                replies.append(text)
            # No delete/send_message/database methods exist: any such action fails the test.
            message = SimpleNamespace(from_user=SimpleNamespace(id=7),
                                      text='/test_spam вымышленный пример', reply=reply)
            namespace['predict'] = lambda model, text, label=label, score=score: (label, score)
            asyncio.run(namespace['cmd_test_spam'](message))
            assert len(replies) == 1
            response = replies[0]
            assert expected in response
            assert 'Это проверка текста. Действия не выполнялись.' in response
            assert 'БУДЕТ УДАЛЕН' not in response and 'ОТПРАВЛЕН НА МОДЕРАЦИЮ' not in response
            assert ('Включён пробный режим' in response) == (has_dry_run and dry)


class DiagnosticLabels(unittest.TestCase):
    def test_addressed_command_excludes_bot_name(self):
        source = (Path(__file__).resolve().parents[1] / 'bot_new.py').read_text(encoding='utf8')
        namespace = load_diagnostic(source)
        namespace['DRY_RUN'] = True
        seen, replies = [], []
        def prediction(model, text):
            seen.append(text)
            return 'ham', .9
        namespace['predict'] = prediction
        async def reply(text):
            replies.append(text)
        for command in ['/test_spam пример', '/test_spam@TestBot пример', '/test_spam@TestBot\nпример']:
            message = SimpleNamespace(from_user=SimpleNamespace(id=7), text=command, reply=reply)
            asyncio.run(namespace['cmd_test_spam'](message))
        self.assertEqual(seen, ['пример'] * 3)
        self.assertTrue(all('@TestBot' not in text for text in replies))
        message.text = '/test_spam@TestBot'
        asyncio.run(namespace['cmd_test_spam'](message))
        self.assertIn('Используйте:', replies[-1])
        self.assertEqual(len(seen), 3)

    def test_recommendations_do_not_claim_actions(self):
        source = (Path(__file__).resolve().parents[1] / 'bot_new.py').read_text(encoding='utf8')
        verify_diagnostic(source, has_dry_run=True)


if __name__ == '__main__':
    unittest.main()
