"""First-run checks using synthetic data, temporary paths and no Telegram network."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from import_dataset import read_dataset
from prepare_demo import prepare
from ml import load_model, predict


class ReleaseChecks(unittest.TestCase):
    def test_demo_is_balanced_unique_and_parseable(self):
        rows = read_dataset(ROOT / 'data.example.txt')
        self.assertEqual(len(rows), 200)
        self.assertEqual(sum(label == 'spam' for label, _ in rows), 100)
        self.assertEqual(len({text for _, text in rows}), 200)

    def test_clean_training_reload_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, model = Path(tmp)/'data.sqlite', Path(tmp)/'model.joblib'
            counts = prepare(ROOT/'data.example.txt', db, model)
            self.assertEqual(counts, {'spam': 100, 'ham': 100})
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 200)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM logs').fetchone()[0], 0)
            fitted = load_model(str(model))
            self.assertEqual(set(fitted.classes_), {'spam', 'ham'})
            label, score = predict(fitted, 'Спасибо за объяснение!')
            self.assertIn(label, {'spam','ham'})
            self.assertTrue(0 <= score <= 1)
            contents = db.read_bytes(), model.read_bytes()
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                prepare(ROOT/'data.example.txt', db, model)
            self.assertEqual(contents, (db.read_bytes(), model.read_bytes()))

    def test_missing_class_and_malformed_input_leave_no_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, db, model = [Path(tmp)/n for n in ['source.txt','data.sqlite','model.joblib']]
            for text in ['spam Only one class\n', 'ham \n', 'invalid label\n']:
                source.write_text(text,encoding='utf8')
                with self.assertRaises(ValueError):
                    prepare(source,db,model)
                self.assertFalse(db.exists())
                self.assertFalse(model.exists())

    def test_bot_first_start_and_dry_run_without_telegram(self):
        # subprocess keeps env/module state and the event loop separate from other tests.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prepare(ROOT/'data.example.txt',tmp/'data.sqlite',tmp/'model.joblib')
            env = os.environ.copy()
            env.update(PYTHONPATH=str(ROOT),TELEGRAM_TOKEN=str(123456789)+':'+'x'*35,
                       ADMIN_IDS='7',CHAT_IDS='-100',DATASET_PATH=str(tmp/'data.sqlite'),
                       MODEL_PATH=str(tmp/'model.joblib'),LOG_FILE=str(tmp/'bot.log'),
                       DRY_RUN='true',
                       SPAM_DELETE_THRESHOLD='0.7',SPAM_MODERATE_THRESHOLD='0.4')
            script = r'''
import asyncio,runpy,sys,socket,sqlite3
from unittest.mock import patch
from aiogram import Bot,Dispatcher
from aiogram.types import Message,Update,Chat,User
from datetime import datetime,timezone
polls=[]
async def polling(self,bot,**kw):polls.append(True)
async def forbidden(*args,**kw):raise AssertionError('Unexpected Telegram call')
original_connect=socket.socket.connect
def offline(sock,address,*args,**kw):
    # Windows asyncio creates a local TCP self-pipe. Permit only that loopback traffic.
    if isinstance(address,tuple) and address[0] in {'127.0.0.1','::1'}:
        return original_connect(sock,address,*args,**kw)
    raise AssertionError('External network forbidden')
with patch.object(Dispatcher,'start_polling',polling),patch.object(Bot,'__call__',forbidden),patch.object(socket.socket,'connect',offline):
    module=runpy.run_path(sys.argv[1],run_name='__main__')
    assert polls==[True]
    assert module['DRY_RUN'] is True
    bot,dp=module['bot'],module['dp']
    # Real dispatch/filter/middleware, forced high-spam score: dry run must not delete or notify.
    from decisions import decide
    from dataclasses import replace
    module['filter_message'].__globals__['classify_and_decide']=lambda model,text,**kw: replace(decide('spam',.99,model_version='sha256:'+'a'*64,text=text,delete_threshold=kw['delete_threshold'],moderate_threshold=kw['moderate_threshold']),dry_run=kw['dry_run'])
    msg=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=-100,type='supergroup'),from_user=User(id=8,is_bot=False,first_name='Demo'),text='Synthetic test only')
    asyncio.run(dp.feed_update(bot,Update(update_id=1,message=msg)))
    with sqlite3.connect(module['DATASET_PATH']) as con:
        assert con.execute("SELECT COUNT(*) FROM logs WHERE action='would_delete_spam'").fetchone()[0]==1
    asyncio.run(bot.session.close())
print('Offline bot startup and dry run OK')
'''
            result = subprocess.run([sys.executable,'-c',script,str(ROOT/'bot_new.py')],
                                    cwd=tmp,env=env,capture_output=True,text=True,timeout=45)
            diagnostic = (tmp/'bot.log').read_text(encoding='utf8') if (tmp/'bot.log').exists() else ''
            self.assertEqual(result.returncode,0,result.stdout+result.stderr+diagnostic)
            self.assertIn('Offline bot startup',result.stdout)

    def test_missing_setup_exits_before_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=os.environ.copy()
            for key in ['TELEGRAM_TOKEN','ADMIN_IDS','CHAT_IDS']:
                env[key]=''
            result=subprocess.run([sys.executable,str(ROOT/'bot_new.py')],cwd=tmp,env=env,
                                  capture_output=True,text=True,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Set TELEGRAM_TOKEN',result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_missing_model_with_config_is_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=os.environ.copy()
            env.update(TELEGRAM_TOKEN=str(123456789)+':'+'x'*35,ADMIN_IDS='7',CHAT_IDS='-100',
                       MODEL_PATH=str(Path(tmp)/'missing.joblib'))
            result=subprocess.run([sys.executable,str(ROOT/'bot_new.py')],cwd=tmp,env=env,
                                  capture_output=True,text=True,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('python prepare_demo.py',result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()),[])


if __name__=='__main__':
    unittest.main()
