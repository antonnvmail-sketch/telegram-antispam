"""Training reminders use the loaded model's actual training snapshot."""
import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from database import add_message, get_all_messages
from import_dataset import import_txt_to_db
from ml import load_model, pending_training_count, train_model
from prepare_demo import prepare
from decisions import load_versioned_model

ROOT = Path(__file__).resolve().parents[1]


def handlers():
    tree = ast.parse((ROOT / 'bot_new.py').read_text(encoding='utf8'))
    names = {'training_status', 'cmd_add_spam', 'cmd_add_ham', 'cmd_train', 'cmd_status'}
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    for node in nodes:
        node.decorator_list = []
        for arg in node.args.args:
            arg.annotation = None
    return compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                   'bot_new.py', 'exec')


class TrainingStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_import_restart_and_successful_retraining(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, model_path = Path(tmp)/'data.sqlite', Path(tmp)/'model.joblib'
            prepare(ROOT/'data.example.txt', db, model_path)
            ns = dict(DATASET_PATH=str(db), MODEL_PATH=str(model_path),
                      model=load_versioned_model(model_path),
                      pending_training_count=pending_training_count,
                      get_all_messages=get_all_messages, add_message=add_message,
                      train_model=train_model,
                      load_versioned_model=load_versioned_model,
                      is_admin=lambda uid: uid == 7)
            exec(handlers(), ns)
            replies = []
            async def reply(text): replies.append(text)
            message = SimpleNamespace(from_user=SimpleNamespace(id=7), reply=reply)
            self.assertIn(': 0.', ns['training_status']())
            message.text = '/add_spam@demo Repeat synthetic spam'
            await ns['cmd_add_spam'](message)
            self.assertIn(': 1.', replies[-1])
            message.text = '/add_ham Repeat synthetic conversation'
            await ns['cmd_add_ham'](message)
            self.assertIn(': 2.', replies[-1])
            # Repeated examples count as distinct additions; importing counts too.
            source = Path(tmp)/'new.txt'
            source.write_text('spam Repeat synthetic spam\n', encoding='utf8')
            import_txt_to_db(source, db)
            ns['model'] = load_versioned_model(model_path)
            self.assertIn(': 3.', ns['training_status']())
            ns.update(APP_VERSION='test', DRY_RUN=True, SPAM_DELETE_THRESHOLD=.7,
                      SPAM_MODERATE_THRESHOLD=.4, ADMIN_IDS=[7], CHAT_IDS=[])
            await ns['cmd_status'](message)
            self.assertIn('Новых примеров после обучения: 3.', replies[-1])
            await ns['cmd_train'](message)
            self.assertIn(': 0.', ns['training_status']())
            ns['model'] = load_versioned_model(model_path)
            self.assertIn(': 0.', ns['training_status']())

    async def test_unknown_history_and_failed_training_do_not_claim_zero(self):
        ns = dict(model=SimpleNamespace(pipeline=object()),
                  pending_training_count=pending_training_count,
                  get_all_messages=lambda path: [('ham', 'old example')],
                  DATASET_PATH='unused', MODEL_PATH='unused', is_admin=lambda uid: True)
        exec(handlers(), ns)
        self.assertIn('неизвестно', ns['training_status']())
        old = ns['model']
        def fail(*args): raise ValueError('synthetic training failure')
        ns['train_model'] = fail
        message = SimpleNamespace(from_user=SimpleNamespace(id=7))
        with self.assertRaises(ValueError):
            await ns['cmd_train'](message)
        self.assertIs(ns['model'], old)
        self.assertIn('неизвестно', ns['training_status']())

    async def test_failed_save_preserves_previous_model_and_pending_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'model.joblib'
            data = [('ham','ordinary discussion')]*3 + [('spam','buy synthetic prize')]*3
            train_model(data, path)
            before = path.read_bytes()
            added = data + [('ham', 'new conversation')]
            def failed_save(model, target):
                Path(target).write_bytes(b'incomplete model')
                raise OSError('synthetic save failure')
            with patch('ml.joblib.dump', side_effect=failed_save):
                with self.assertRaises(OSError): train_model(added, path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(pending_training_count(load_model(path), added), 1)


if __name__ == '__main__':
    unittest.main()
