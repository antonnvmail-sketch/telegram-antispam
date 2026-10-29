"""Public entrypoint with synthetic Telegram transport and temporary demo data."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from prepare_demo import prepare

ROOT=Path(__file__).resolve().parents[1]

class ReviewDispatcher(unittest.TestCase):
    def test_active_review_commands_callbacks_and_monthly_export(self):
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d);prepare(ROOT/'data.example.txt',temp/'data.sqlite',temp/'model.joblib')
            env=os.environ.copy();env.update(PYTHONPATH=str(ROOT),PYTHONUTF8='1',TELEGRAM_TOKEN='123456789:'+'x'*35,
                ADMIN_IDS='7,8',CHAT_IDS='-100123',DATASET_PATH=str(temp/'data.sqlite'),MODEL_PATH=str(temp/'model.joblib'),
                LOG_FILE=str(temp/'bot.log'),DRY_RUN='false',SPAM_DELETE_THRESHOLD='0.7',SPAM_MODERATE_THRESHOLD='0.4')
            script=r'''
import asyncio,json,runpy,socket,sys,sqlite3,uuid
from dataclasses import replace
from datetime import datetime,timezone
from unittest.mock import patch
import aiogram
from aiogram import types
calls=[]
class FakeBot(aiogram.Bot):
    async def __call__(self,method,request_timeout=None):
        item=dict(method=method.__api_method__,chat=getattr(method,'chat_id',None),message=getattr(method,'message_id',None),text=getattr(method,'text',None),markup=getattr(method,'reply_markup',None));calls.append(item)
        if item['method'] in {'deleteMessage','answerCallbackQuery','editMessageText'}:return True
        if item['method'] in {'sendMessage','sendDocument'}:
            item['id']=90000+len(calls)
            if item['method']=='sendDocument':item['csv']=method.document.data
            return types.Message(message_id=item['id'],date=datetime.now(timezone.utc),chat=types.Chat(id=item['chat'],type='private' if item['chat']>0 else 'supergroup'),text=item['text'])
        raise AssertionError(item['method'])
    async def get_me(self,**kwargs):return types.User(id=self.id,is_bot=True,first_name='Synthetic')
original=socket.socket.connect
def offline(sock,address,*args,**kwargs):
    if isinstance(address,tuple) and address[0] in {'127.0.0.1','::1'}:return original(sock,address,*args,**kwargs)
    raise AssertionError('External connection forbidden')
with patch.object(aiogram,'Bot',FakeBot),patch.object(socket.socket,'connect',offline):
    ns=runpy.run_path(sys.argv[1],run_name='review_smoke');bot=ns['bot'];dp=ns['dp'];service=ns['review_service']
    reference=ns['classify_and_decide'](ns['model'],'Спасибо за помощь',delete_threshold=.7,moderate_threshold=.4)
    def classify(model,text,**kwargs):
        action='delete' if text=='SYNTHETIC_DELETE' else 'moderate' if text=='SYNTHETIC_REVIEW' else 'allow'
        return replace(reference,decision_id=str(uuid.uuid4()),action=action,label='ham' if action=='allow' else 'spam',confidence=.9,p_spam=.9,dry_run=False)
    ns['filter_message'].__globals__['classify_and_decide']=classify
    async def run():
        counter=100
        async def feed(text,chat=-100123,reply=None):
            nonlocal counter
            counter+=1;before=len(calls)
            msg=types.Message(message_id=counter,date=datetime.now(timezone.utc),chat=types.Chat(id=chat,type='private' if chat>0 else 'supergroup',title='Synthetic group' if chat<0 else None),from_user=types.User(id=7,is_bot=False,first_name='Synthetic'),text=text,reply_to_message=reply)
            await dp.feed_update(bot,types.Update(update_id=counter,message=msg));return counter,calls[before:]
        async def press(card,choice):
            nonlocal counter
            counter+=1;before=len(calls)
            data=next(b.callback_data for row in card['markup'].inline_keyboard for b in row if b.callback_data and b.callback_data.startswith('rv:'+choice+':'))
            q=types.CallbackQuery(id=str(counter),chat_instance='fixture',from_user=types.User(id=7,is_bot=False,first_name='Synthetic'),message=types.Message(message_id=card['id'],date=datetime.now(timezone.utc),chat=types.Chat(id=7,type='private'),text='Synthetic'),data=data)
            await dp.feed_update(bot,types.Update(update_id=counter,callback_query=q));return calls[before:]
        mid,out=await feed('SYNTHETIC_DELETE');assert [(c['chat'],c['message']) for c in out if c['method']=='deleteMessage']==[(-100123,mid)]
        card=next(c for c in out if c['method']=='sendMessage' and c['chat']==7);await press(card,'ham')
        mid,out=await feed('SYNTHETIC_REVIEW');card=next(c for c in out if c['method']=='sendMessage' and c['chat']==7)
        out=await press(card,'spam');assert [(c['chat'],c['message']) for c in out if c['method']=='deleteMessage']==[(-100123,mid)]
        assert not any(c['method']=='deleteMessage' for c in await press(card,'spam'))
        _,out=await feed('SYNTHETIC_REVIEW');card=next(c for c in out if c['method']=='sendMessage' and c['chat']==7);await press(card,'keep')
        counts=service.tickets.stats()['months'][-1];assert [counts[k] for k in ['auto_deleted','manual_deleted','false_positive_reports','reviewed_ham']]==[1,1,1,1]
        _,out=await feed('/stat',chat=7);assert any('месяц не завершён' in (c['text'] or '') for c in out)
        _,out=await feed('/stat_export',chat=7);csv=next(c['csv'] for c in out if c['method']=='sendDocument').decode('utf-8-sig');assert len(csv.splitlines())==4 and 'chat_id' not in csv
        _,out=await feed('/review_stats',chat=7);assert any('Ответов администраторов: 3' in (c['text'] or '') for c in out)
        original=types.Message(message_id=500,date=datetime.now(timezone.utc),chat=types.Chat(id=-100123,type='supergroup'),text='Synthetic review only')
        _,out=await feed('/review',reply=original);assert any(c['markup'] for c in out)
        with sqlite3.connect(ns['DATASET_PATH']) as c:assert c.execute('SELECT count(*) FROM messages').fetchone()[0]==200
        await bot.session.close()
    asyncio.run(run())
print('Public review dispatcher OK')
'''
            r=subprocess.run([sys.executable,'-c',script,str(ROOT/'bot_new.py')],cwd=temp,env=env,capture_output=True,text=True,timeout=60)
            log=(temp/'bot.log').read_text(encoding='utf-8') if (temp/'bot.log').exists() else ''
            self.assertEqual(r.returncode,0,r.stdout+r.stderr+log)
            self.assertIn('Public review dispatcher OK',r.stdout)
