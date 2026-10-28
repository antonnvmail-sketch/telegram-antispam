"""Decision replay, model identity, diagnostics and isolated SQLite migration."""
import ast
from contextlib import closing
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decisions import (LoadedModel, classify_and_decide, decide, diagnostic_text,
                       load_versioned_model, persist_decision)
from database import init_db, log_decision
from moderation_actions import execute_actions
from test_actions import Message, Capture, BadRequest, Forbidden, NotFound

VERSION = 'sha256:' + 'b' * 64


class Pipeline:
    classes_ = ['ham', 'spam']
    def __init__(self, p): self.p = p
    def predict_proba(self, texts): return [[1-self.p, self.p] for _ in texts]


def decision(p=.9, text='synthetic test text', **kwargs):
    return classify_and_decide(LoadedModel(Pipeline(p), VERSION), text,
        delete_threshold=kwargs.get('delete_threshold', .7),
        moderate_threshold=kwargs.get('moderate_threshold', .4))


def extract_functions(*names):
    tree = ast.parse((ROOT/'bot_new.py').read_text(encoding='utf-8'))
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in selected:
        node.decorator_list = []
        for arg in node.args.args: arg.annotation = None
    return compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), 'isolated_handlers', 'exec')


class DecisionTests(unittest.TestCase):
    def test_policy_matches_original_on_probability_grid(self):
        for delete, moderate in [(.7,.4), (.5,.4), (.9,.7), (1,0), (.4,.4)]:
            for n in range(1001):
                p = n/1000
                d = decision(p, delete_threshold=delete, moderate_threshold=moderate)
                label = 'spam' if p > .5 else 'ham'
                confidence = p if label=='spam' else 1-p
                expected = ('delete' if label=='spam' and confidence>delete else
                            'moderate' if label=='spam' and confidence>moderate else 'allow')
                self.assertEqual(d.action, expected)
                self.assertAlmostEqual(d.p_spam, p)

    def test_exact_thresholds(self):
        self.assertEqual(decision(.7).action, 'moderate')
        self.assertEqual(decision(.70000001).action, 'delete')
        self.assertEqual(decision(.5).action, 'allow')
        self.assertEqual(decision(.6, moderate_threshold=.6).action, 'allow')
        self.assertEqual(decision(.60000001, moderate_threshold=.6).action, 'moderate')

    def test_reversed_model_class_order_and_tie(self):
        pipe = SimpleNamespace(classes_=['spam','ham'], predict_proba=lambda texts:[[.5,.5]])
        result = classify_and_decide(LoadedModel(pipe, VERSION), 'text', delete_threshold=.7, moderate_threshold=.4)
        self.assertEqual((result.label,result.action),('spam','moderate'))

    def test_ham_probability_is_spam_probability_not_winner_confidence(self):
        result = decision(.1)
        self.assertAlmostEqual(result.confidence, .9)
        self.assertAlmostEqual(result.p_spam, .1)
        self.assertEqual(result.reason, 'predicted_ham')

    def test_invalid_probability_vectors_rejected(self):
        for values in [[float('nan'),.9], [float('inf'),.9], [-.1,1.1], [.4,.4], [.3,.3,.4]]:
            with self.subTest(values=values):
                pipe=SimpleNamespace(classes_=['ham','spam'],predict_proba=lambda texts:[values])
                with self.assertRaises(ValueError):
                    classify_and_decide(LoadedModel(pipe,VERSION),'text',delete_threshold=.7,moderate_threshold=.4)

    def test_invalid_thresholds_classes_and_model_version_rejected(self):
        for delete,moderate in [(.3,.4),(float('nan'),.4),(1.1,.4),(.7,-.1)]:
            with self.subTest(delete=delete,moderate=moderate):
                with self.assertRaises(ValueError):decision(.9,delete_threshold=delete,moderate_threshold=moderate)
        for classes in [['spam'],['spam','other'],['spam','ham','other']]:
            with self.subTest(classes=classes):
                with self.assertRaises(ValueError):
                    classify_and_decide(LoadedModel(SimpleNamespace(classes_=classes,predict_proba=lambda texts:[[.1,.9]]),VERSION),'text',delete_threshold=.7,moderate_threshold=.4)
        with self.assertRaises(ValueError):
            decide('spam',.9,delete_threshold=.7,moderate_threshold=.4,model_version='model.joblib',text='text')

    def test_loaded_identity_survives_file_replacement_during_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.joblib'; path.write_bytes(b'first model bytes')
            def loader(stream):
                self.assertEqual(stream.read(),b'first model bytes')
                path.write_bytes(b'second model bytes')
                return Pipeline(.9)
            loaded=load_versioned_model(path,loader=loader)
            self.assertEqual(loaded.version,'sha256:'+hashlib.sha256(b'first model bytes').hexdigest())
            second=load_versioned_model(path,loader=lambda stream:Pipeline(.1))
            self.assertNotEqual(second.version,loaded.version)
            self.assertEqual(classify_and_decide(loaded,'text',delete_threshold=.7,moderate_threshold=.4).action,'delete')
            self.assertEqual(classify_and_decide(second,'text',delete_threshold=.7,moderate_threshold=.4).action,'allow')

    def test_failed_load_cannot_replace_previous_model(self):
        loaded=LoadedModel(Pipeline(.9),VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'broken'; path.write_bytes(b'broken')
            def broken(stream):raise ValueError('synthetic load failure')
            with self.assertRaises(ValueError):loaded=load_versioned_model(path,loader=broken)
        self.assertEqual(loaded.version,VERSION)

    @unittest.skipUnless(importlib.util.find_spec('joblib'), 'joblib available on server test runtime')
    def test_real_joblib_bytes_roundtrip_without_training(self):
        import joblib
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.joblib'
            joblib.dump(Pipeline(.85),path)
            model=load_versioned_model(path)
            self.assertEqual(model.version,'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertAlmostEqual(classify_and_decide(model,'text',delete_threshold=.7,moderate_threshold=.4).p_spam,.85)

    def test_record_is_immutable_and_input_hash_uses_exact_text(self):
        d=decision(text='Тест\n')
        with self.assertRaises(FrozenInstanceError):d.action='allow'
        self.assertEqual(d.input_sha256,hashlib.sha256('Тест\n'.encode()).hexdigest())
        self.assertNotEqual(d.input_sha256,decision(text='Тест').input_sha256)

    def test_database_migration_preserves_history_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'test.sqlite')
            with closing(sqlite3.connect(db)) as conn:
                conn.executescript("CREATE TABLE messages(id INTEGER PRIMARY KEY,label TEXT,text TEXT); INSERT INTO messages VALUES(1,'ham','old'); CREATE TABLE logs(id INTEGER PRIMARY KEY,user_id INTEGER,username TEXT,chat_id INTEGER,message_id INTEGER,action TEXT,text TEXT,timestamp TEXT); INSERT INTO logs(id,action,text) VALUES(1,'deleted_spam','old');")
            init_db(db);init_db(db)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('select label,text from messages').fetchall(),[('ham','old')])
                self.assertEqual(conn.execute('select action,text from logs').fetchall(),[('deleted_spam','old')])
                self.assertEqual(conn.execute('select count(*) from moderation_decisions').fetchone()[0],0)

    def test_saved_decision_can_be_recomputed_from_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'test.sqlite');init_db(db)
            for p in [.1,.5,.6,.7,.9]:
                d=decision(p,text='original text')
                log_decision(db,d.to_record(),chat_id=-100,message_id=42,source='filter')
            with closing(sqlite3.connect(db)) as conn:
                conn.row_factory=sqlite3.Row
                rows=conn.execute('select * from moderation_decisions').fetchall()
            for row in rows:
                replay=decide(row['label'],row['confidence'],delete_threshold=row['delete_threshold'],
                    moderate_threshold=row['moderate_threshold'],model_version=row['model_version'],
                    text='original text',p_spam=row['p_spam'])
                for key in ['action','reason','p_spam','model_version','input_sha256','policy_version']:
                    self.assertEqual(getattr(replay,key),row[key])

    def test_persistence_failure_keeps_complete_file_record(self):
        capture=Capture();d=decision()
        def broken(*args,**kwargs):raise sqlite3.OperationalError('synthetic')
        saved=persist_decision(d,chat_id=-100,message_id=42,source='test_spam',write_record=broken,logger=capture)
        self.assertFalse(saved)
        self.assertEqual(capture.records[0]['model_version'],VERSION)
        self.assertEqual(capture.records[0]['p_spam'],d.p_spam)
        self.assertEqual(capture.records[-1]['stage'],'decision_journal')
        self.assertIn('Запись в БД не удалась',diagnostic_text(d,saved=False))


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_and_filter_use_identical_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'test.sqlite');init_db(db)
            for p in [.1,.5,.6,.7,.9]:
                sent=[]
                async def send(*args):sent.append(args)
                async def permissions(*args):return {'can_delete_messages':True}
                logger=Capture()
                ns=dict(model=LoadedModel(Pipeline(p),VERSION),logger=SimpleNamespace(info=lambda *a:None,error=lambda *a:None,warning=lambda *a:None,debug=lambda *a:None),
                    action_logger=logger,DATASET_PATH=db,log_decision=log_decision,persist_decision=persist_decision,
                    classify_and_decide=classify_and_decide,diagnostic_text=diagnostic_text,
                    DRY_RUN=False,SPAM_DELETE_THRESHOLD=.7,SPAM_MODERATE_THRESHOLD=.4,is_admin=lambda uid:True,
                    execute_actions=execute_actions,log_action=lambda *args:None,
                    ADMIN_IDS=[1],bot=SimpleNamespace(send_message=send),
                    check_bot_permissions=permissions,TelegramBadRequest=BadRequest,TelegramForbiddenError=Forbidden,
                    TelegramNotFound=NotFound)
                exec(extract_functions('filter_message','cmd_test_spam','record_decision'),ns)
                normal=Message();await ns['filter_message'](normal)
                diagnostic=Message();diagnostic.text='/test_spam '+normal.text
                replies=[]
                async def reply(text):replies.append(text)
                diagnostic.reply=reply
                before=len(sent);await ns['cmd_test_spam'](diagnostic)
                self.assertEqual(diagnostic.deletes,0)
                self.assertEqual(len(sent),before)
                with closing(sqlite3.connect(db)) as conn:
                    conn.row_factory=sqlite3.Row
                    rows=conn.execute('select * from moderation_decisions order by rowid desc limit 2').fetchall()
                self.assertEqual({r['source'] for r in rows},{'filter','test_spam'})
                for field in ['label','confidence','p_spam','action','reason','delete_threshold','moderate_threshold','model_version','input_sha256','policy_version']:
                    self.assertEqual(rows[0][field],rows[1][field],field)
                self.assertIn(rows[0]['model_version'],replies[0])
                self.assertEqual(normal.deletes,int(rows[1]['action']=='delete'))

    async def test_decision_journal_failure_preserves_action_and_correlation(self):
        msg=Message();d=decision();capture=Capture();writes=[]
        async def send(*args):pass
        async def permissions(*args):return {}
        result=await execute_actions(message=msg,decision_record=d,
            journal_decision=lambda record:False,admins=[1],send_message=send,
            write_action=writes.append,
            check_permissions=permissions,logger=capture,
            rejected_errors=(BadRequest,Forbidden,NotFound),bad_request_error=BadRequest)
        self.assertEqual(result.deletion,'deleted')
        self.assertIn('decision_record',result.journal_failures)
        self.assertFalse(writes)
        self.assertTrue(all(r['decision_id']==d.decision_id for r in capture.records))

    async def test_diagnostic_not_authorized_or_missing_model_has_no_record(self):
        for allowed,model in [(False,LoadedModel(Pipeline(.9),VERSION)),(True,None)]:
            with self.subTest(allowed=allowed):
                ns=dict(is_admin=lambda uid:allowed,model=model)
                exec(extract_functions('cmd_test_spam'),ns)
                msg=Message();msg.text='/test_spam text';replies=[]
                async def reply(text):replies.append(text)
                msg.reply=reply
                await ns['cmd_test_spam'](msg)
                self.assertEqual(len(replies),1)
                self.assertEqual(msg.deletes,0)

    async def test_training_replaces_loaded_object_and_version_together(self):
        old=LoadedModel(Pipeline(.9),VERSION);new=LoadedModel(Pipeline(.1),'sha256:'+'c'*64)
        ns=dict(model=old,is_admin=lambda uid:True,get_all_messages=lambda path:[('ham','text')],
            DATASET_PATH='unused',MODEL_PATH='unused',train_model=lambda *args:None,
            load_versioned_model=lambda path:new)
        exec(extract_functions('cmd_train'),ns)
        msg=Message()
        async def reply(text):pass
        msg.reply=reply
        await ns['cmd_train'](msg)
        self.assertIs(ns['model'],new)
        def broken(path):raise ValueError('synthetic')
        ns['load_versioned_model']=broken
        with self.assertRaises(ValueError):await ns['cmd_train'](msg)
        self.assertIs(ns['model'],new)


if __name__=='__main__':unittest.main(verbosity=2)
