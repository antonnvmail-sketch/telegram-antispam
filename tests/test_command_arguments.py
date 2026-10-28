import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest


class CommandArguments(unittest.TestCase):
    def test_addressed_add_commands_store_only_arguments(self):
        source = (Path(__file__).resolve().parents[1] / 'bot_new.py').read_text(encoding='utf8')
        for name, label in [('add_spam', 'spam'), ('add_ham', 'ham')]:
            tree = ast.parse(source)
            handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'cmd_' + name)
            handler.decorator_list = []
            stored, replies = [], []
            namespace = {'types': SimpleNamespace(Message=object), 'is_admin': lambda uid: True,
                         'training_status': lambda: 'Training reminder', 'DATASET_PATH': 'unused', 'add_message': lambda path, category, text: stored.append((category, text))}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])), '<handler>', 'exec'), namespace)
            async def reply(text):
                replies.append(text)
            for command in ['/' + name + ' пример', '/' + name + '@TestBot пример', '/' + name + '@TestBot\nпример', '/' + name + '@TestBot']:
                message = SimpleNamespace(from_user=SimpleNamespace(id=7), text=command, reply=reply)
                asyncio.run(namespace['cmd_' + name](message))
            self.assertEqual(stored, [(label, 'пример')] * 3)
            self.assertIn('Используйте:', replies[-1])
