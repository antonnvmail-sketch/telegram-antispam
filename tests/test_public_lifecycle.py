"""Public safety defaults and durable decision accounting, with synthetic data."""
import asyncio
from unittest.mock import patch
import importlib.util
import os
from contextlib import closing
from dataclasses import replace
import logging
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from database import init_db,log_action,log_decision
from decisions import decide,persist_decision
from moderation_actions import execute_actions


class PublicLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_safe_configuration_defaults(self):
        path=Path(__file__).parents[1]/'config.py'
        with patch.dict(os.environ,{},clear=True),patch('dotenv.load_dotenv',return_value=False):
            spec=importlib.util.spec_from_file_location('defaults_probe',path)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertTrue(module.DRY_RUN)

    async def test_real_aiogram_error_types_without_requests(self):
        from aiogram.exceptions import (TelegramBadRequest,TelegramForbiddenError,TelegramNotFound,
            TelegramNetworkError,TelegramServerError,TelegramRetryAfter)
        from aiogram.methods import DeleteMessage
        method=DeleteMessage(chat_id=-100,message_id=1)
        examples=[(TelegramBadRequest(method=method,message='synthetic'),'rejected'),
            (TelegramForbiddenError(method=method,message='synthetic'),'rejected'),
            (TelegramNotFound(method=method,message='synthetic'),'rejected'),
            (TelegramNetworkError(method=method,message='synthetic'),'unknown'),
            (TelegramServerError(method=method,message='synthetic'),'unknown'),
            (TelegramRetryAfter(method=method,message='synthetic',retry_after=1),'unknown')]
        async def forbidden(*args):raise AssertionError('Unexpected send')
        async def permissions(*args):return {'can_delete_messages':True}
        for error,expected in examples:
            async def delete():raise error
            record=decide('spam',.99,delete_threshold=.7,moderate_threshold=.4,model_version='sha256:'+'a'*64,text='synthetic')
            result=await execute_actions(message=SimpleNamespace(chat=SimpleNamespace(id=-100),message_id=1,
                from_user=None,text='synthetic',delete=delete),decision_record=record,journal_decision=lambda r:True,
                admins=[7],send_message=forbidden,write_action=lambda action:None,
                check_permissions=permissions,logger=logging.getLogger('test'),
                rejected_errors=(TelegramBadRequest,TelegramForbiddenError,TelegramNotFound),bad_request_error=TelegramBadRequest)
            self.assertEqual(result.deletion,expected)

    async def test_dry_run_persists_without_telegram(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'test.sqlite');init_db(db)
            def forbidden(*args,**kwargs):raise AssertionError('External action in dry-run')
            async def async_forbidden(*args,**kwargs):forbidden()
            msg=SimpleNamespace(chat=SimpleNamespace(id=-100),message_id=1,text='synthetic only',
                from_user=SimpleNamespace(id=7,username='synthetic'),delete=async_forbidden)
            for action,p in [('delete',.99),('moderate',.6),('allow',.4)]:
                d=replace(decide('spam',p,delete_threshold=.7,moderate_threshold=.4,
                    model_version='sha256:'+'a'*64,text=msg.text),dry_run=True)
                result=await execute_actions(message=msg,decision_record=d,
                    journal_decision=lambda record:persist_decision(record,chat_id=-100,message_id=1,source='filter',
                        write_record=lambda data,**context:log_decision(db,data,**context),logger=logging.getLogger('test')),
                    admins=[7],send_message=async_forbidden,
                    write_action=lambda event:log_action(db,7,'synthetic',-100,1,event,msg.text),
                    check_permissions=async_forbidden,logger=logging.getLogger('test'),
                    rejected_errors=(ValueError,),bad_request_error=ValueError)
                self.assertTrue(result.dry_run)
                self.assertEqual(result.decision,action)
                self.assertEqual(result.deletion,'not_attempted')
            with closing(sqlite3.connect(db)) as conn:
                rows=conn.execute('SELECT action,dry_run FROM moderation_decisions').fetchall()
                self.assertEqual(rows,[('delete',1),('moderate',1),('allow',1)])
                self.assertEqual(conn.execute("SELECT count(*) FROM logs WHERE action LIKE 'would_%'").fetchone()[0],3)

    async def test_legacy_accounting_failure_still_keeps_full_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'test.sqlite');init_db(db)
            d=replace(decide('spam',.99,delete_threshold=.7,moderate_threshold=.4,
                model_version='sha256:'+'a'*64,text='synthetic'),dry_run=True)
            async def forbidden(*args):raise AssertionError('Unexpected Telegram action')
            def broken(*args):raise sqlite3.OperationalError('synthetic journal failure')
            result=await execute_actions(message=SimpleNamespace(chat=SimpleNamespace(id=-100),message_id=2),
                decision_record=d,journal_decision=lambda record:persist_decision(record,chat_id=-100,message_id=2,
                    source='filter',write_record=lambda data,**ctx:log_decision(db,data,**ctx),logger=logging.getLogger('test')),
                admins=[],send_message=forbidden,write_action=broken,
                check_permissions=forbidden,logger=logging.getLogger('test'),rejected_errors=(ValueError,),bad_request_error=ValueError)
            self.assertTrue(result.journal_failures)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM moderation_decisions').fetchone()[0],1)

    async def test_migration_adds_mode_to_existing_technical_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'test.sqlite')
            sql=(Path(__file__).parents[1]/'migrations/002_moderation_decisions.sql').read_text()
            old=sql.replace(',\n    dry_run INTEGER NOT NULL DEFAULT 0 CHECK(dry_run IN (0, 1))','')
            with closing(sqlite3.connect(db)) as conn:conn.executescript(old)
            init_db(db);init_db(db)
            with closing(sqlite3.connect(db)) as conn:
                self.assertIn('dry_run',{r[1] for r in conn.execute('PRAGMA table_info(moderation_decisions)')})


if __name__=='__main__':unittest.main()
