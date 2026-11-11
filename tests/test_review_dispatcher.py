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
                ADMIN_IDS='7',MODERATOR_CHAT_IDS='{"8":[-100123,-100124]}',CHAT_IDS='-100123,-100124',DATASET_PATH=str(temp/'data.sqlite'),MODEL_PATH=str(temp/'model.joblib'),
                LOG_FILE=str(temp/'bot.log'),DRY_RUN='false',SPAM_DELETE_THRESHOLD='0.7',SPAM_MODERATE_THRESHOLD='0.4',
                EXPERIMENTAL_REVIEW_ALERTS='true')
            script=r'''
import asyncio,json,runpy,socket,sys,sqlite3,uuid
from dataclasses import replace
from datetime import datetime,timezone
from unittest.mock import patch
import aiogram
from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
calls=[]
wave_missing=set()
wave_forbidden=set()
wave_unknown=set()
class FakeBot(aiogram.Bot):
    async def __call__(self,method,request_timeout=None):
        item=dict(method=method.__api_method__,chat=getattr(method,'chat_id',None),message=getattr(method,'message_id',None),text=getattr(method,'text',None),markup=getattr(method,'reply_markup',None));calls.append(item)
        if item['method']=='deleteMessage' and item['message'] in {501,502}:
            assert any(r['message_id']==item['message'] and r['reason']=='spam_command' for r in service.tickets.feedback_report()['feedback'])
            if item['message']==502:
                raise TelegramForbiddenError(method=method,message='Forbidden')
        if item['method']=='deleteMessage' and item['message'] in wave_missing:
            raise TelegramBadRequest(method=method,message='message to delete not found')
        if item['method']=='deleteMessage' and item['message'] in wave_forbidden:
            raise TelegramForbiddenError(method=method,message='Forbidden')
        if item['method']=='deleteMessage' and item['message'] in wave_unknown:
            raise TimeoutError()
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
        action='delete' if text=='SYNTHETIC_DELETE' else 'moderate' if text in {'SYNTHETIC_REVIEW','Куплю USDT'} else 'allow'
        return replace(reference,decision_id=str(uuid.uuid4()),action=action,label='ham' if action=='allow' else 'spam',confidence=.9,p_spam=.9,dry_run=False)
    ns['filter_message'].__globals__['classify_and_decide']=classify
    async def run():
        counter=100
        async def feed(text,chat=-100123,reply=None,edit_id=None,entities=None,actor=7):
            nonlocal counter
            counter+=1;before=len(calls)
            msg=types.Message(message_id=edit_id or counter,date=datetime.now(timezone.utc),chat=types.Chat(id=chat,type='private' if chat>0 else 'supergroup',title='Synthetic group' if chat<0 else None),from_user=types.User(id=actor,is_bot=False,first_name='Synthetic'),text=text,reply_to_message=reply,entities=entities)
            update = {'edited_message': msg} if edit_id else {'message': msg}
            await dp.feed_update(bot,types.Update(update_id=counter,**update));return counter,calls[before:]
        async def press(card,choice,prefix='rv',actor=7):
            nonlocal counter
            counter+=1;before=len(calls)
            data=next(b.callback_data for row in card['markup'].inline_keyboard for b in row if b.callback_data and b.callback_data.startswith(prefix+':'+choice+':'))
            q=types.CallbackQuery(id=str(counter),chat_instance='fixture',from_user=types.User(id=actor,is_bot=False,first_name='Synthetic'),message=types.Message(message_id=card['id'],date=datetime.now(timezone.utc),chat=types.Chat(id=card['chat'],type='private'),text='Synthetic'),data=data)
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
        _,out=await feed('Где посмотреть курс USDT?');assert out==[]
        _,out=await feed('Есть кто может ЮСДТ продать?')
        assert len(out)==2 and all('Причина: Сделка с USDT' in c['text'] and c['markup'] for c in out)
        assert any(c['method']=='deleteMessage' for c in await press(out[0],'spam'))
        _,out=await feed('Куплю USDT');assert len(out)==2 and all(c['markup'] for c in out)
        _,out=await feed('Есть предложение по работе из дома. Расскажу подробнее в личке.')
        assert len(out)==2 and all('Причина: Работа с приглашением в личку' in c['text'] and c['markup'] for c in out)
        assert not any(c['method']=='deleteMessage' for c in await press(out[0],'keep'))
        _,out=await feed('/review_stats',chat=7)
        assert any('Сделка с USDT — показано 1, спам 1, не спам 0' in c['text'] for c in out)
        assert any('Работа с приглашением в личку — показано 1, спам 0, не спам 1' in c['text'] for c in out)
        _,out=await feed('Куплю USDT',chat=-100999);assert out==[]
        notice='Чат переносится в новую группу. Перейдите по ссылке.'
        mid,out=await feed(notice);assert out==[]
        hidden=[types.MessageEntity(type='text_link',offset=0,length=3,url='https://t.me/example')]
        _,out=await feed(notice,edit_id=mid,entities=hidden)
        assert len(out)==2 and all('Проверьте объявление о переносе' in c['text'] for c in out)
        _,out=await feed(notice+' Тест.',edit_id=mid,entities=hidden);assert out==[]
        first,out=await feed('@Data_Migration_ROBOT 1a23456b7d');assert out==[]
        second,out=await feed('@Connect_TG_ROBOT 1a23456b7d',chat=-100124)
        assert len(out)==2 and all(c['method']=='sendMessage' and 'Проверьте серию' in c['text'] for c in out)
        destinations={c['chat']:c['id'] for c in out}
        urls={f'https://t.me/c/123/{first}',f'https://t.me/c/124/{second}'}
        following=[]
        for count in range(3,11):
            mid,out=await feed('@Connect_TG_ROBOT 1a23456b7d',chat=-100124)
            following.append(mid)
            urls.add(f'https://t.me/c/124/{mid}')
            assert len(out)==2 and all(c['method']=='editMessageText' for c in out)
            assert all(c['message']==destinations[c['chat']] and f'Сообщения: {count} · группы: 2' in c['text'] for c in out)
            assert all({button.url for row in c['markup'].inline_keyboard for button in row if button.url}==urls for c in out)
        wave_card=dict(next(c for c in out if c['chat']==8),id=destinations[8])
        preview_calls=await press(wave_card,'preview',prefix='wave',actor=8)
        preview=next(c for c in preview_calls if c['method']=='sendMessage')
        assert 'Подтвердите удаление' in preview['text'] and not any(c['method']=='deleteMessage' for c in preview_calls)
        assert not any(c['method']=='deleteMessage' for c in await press(preview,'confirm',prefix='wave',actor=7))
        _,out=await feed('@Connect_TG_ROBOT 1a23456b7d',chat=-100124,edit_id=second);assert out==[]
        later,_=await feed('@Connect_TG_ROBOT 1a23456b7d',chat=-100124)
        wave_missing.add(first)
        wave_forbidden.add(following[0])
        wave_unknown.add(following[1])
        out=await press(preview,'confirm',prefix='wave',actor=8)
        targets={(c['chat'],c['message']) for c in out if c['method']=='deleteMessage'}
        assert len(targets)==9 and (-100123,first) in targets and (-100124,second) not in targets and (-100124,later) not in targets
        result=next(c for c in out if c['method']=='editMessageText')
        assert 'Удалено: 6\nУже отсутствовали: 1' in result['text'] and 'Пропущено: 1' in result['text']
        assert 'нет доступа или прав' in result['text'] and 'Результат удаления неизвестен' in result['text']
        assert {button.url for row in result['markup'].inline_keyboard for button in row} == {
            f'https://t.me/c/124/{following[0]}',f'https://t.me/c/124/{following[1]}',
        }
        assert not any(c['method']=='deleteMessage' for c in await press(preview,'confirm',prefix='wave',actor=8))
        wave_stats=service.tickets.stats()['months'][-1]
        assert wave_stats['wave_reports']==9 and wave_stats['wave_deleted']==6
        _,out=await feed('@Data_Migration_ROBOT 1a23456b8d');assert out==[]
        _,out=await feed('@Connect_TG_ROBOT 1a23456b8d',chat=-100124)
        assert len(out)==2 and all(c['method']=='sendMessage' and 'Сообщения: 2 · группы: 2' in c['text'] for c in out)
        target=original.model_copy(update={'message_id':501,'text':'Пропущенный спам'})
        _,out=await feed('/spam',reply=target,actor=99);assert out==[]
        _,out=await feed('/spam',reply=target,actor=8)
        assert [(c['chat'],c['message']) for c in out if c['method']=='deleteMessage']==[(-100123,501)]
        assert any('Отметка «Спам» сохранена. Исходное сообщение удалено.' in (c['text'] or '') for c in out)
        _,out=await feed('/spam',reply=target);assert all(c['method']!='deleteMessage' for c in out)
        _,out=await feed('/spam',reply=target.model_copy(update={'message_id':502}))
        assert any('Отметка «Спам» сохранена.' in (c['text'] or '') for c in out)
        direct=[r for r in service.tickets.feedback_report()['feedback'] if r['reason']=='spam_command']
        assert [(r['text'],r['telegram_delete_status']) for r in direct]==[('Пропущенный спам','confirmed_deleted'),('Пропущенный спам','rejected')]
        assert direct[0]['admin_id']==8 and direct[0]['model']['chat_title']=='Synthetic group'
        counts=service.tickets.stats()['months'][-1]
        assert counts['spam_command_reports']==2 and counts['spam_command_deleted']==1
        _,out=await feed('/review_stats',chat=7)
        assert any('/spam — отметок 2, подтверждённых удалений 1' in (c['text'] or '') for c in out)
        assert any('ID 8 — решений 10, подтверждённых удалений 7' in (c['text'] or '') for c in out)
        _,out=await feed('/moderation_log 8',chat=7)
        assert any('ID 8' in c['text'] and 'Synthetic group' in c['text'] and 'Пропущенный спам' in c['text'] for c in out)
        for command in ('/review_stats','/stat','/stat_export','/moderation_log'):
            _,out=await feed(command,chat=8,actor=8);assert out==[]
        for command in ('/train','/add_spam Test','/add_ham Test','/import_data'):
            _,out=await feed(command,chat=8,actor=8)
            assert any('Только администраторы' in c['text'] for c in out)
        _,out=await feed('/stat_export',chat=7)
        assert 'Отметок командой /spam;Удалено командой /spam' in next(c['csv'] for c in out if c['method']=='sendDocument').decode('utf-8-sig')
        _,out=await feed('Куплю USDT',chat=-100124)
        old_card=next(c for c in out if c['chat']==8)
        service.moderator_chats[8]=frozenset({-100123})
        assert not any(c['method']=='deleteMessage' for c in await press(old_card,'spam',actor=8))
        _,out=await feed('Куплю USDT',chat=-100124);assert {c['chat'] for c in out}=={7}
        _,out=await feed('Есть кто может ЮСДТ продать?',chat=-100124);assert {c['chat'] for c in out}=={7}
        outside=original.model_copy(update={'message_id':503,'chat':types.Chat(id=-100124,type='supergroup')})
        for command in ('/review','/spam'):
            _,out=await feed(command,chat=-100124,reply=outside,actor=8);assert out==[]
        in_scope,out=await feed('@Data_Migration_ROBOT 1a23456bcd');assert out==[]
        outside_id,out=await feed('@Connect_TG_ROBOT 1a23456bcd',chat=-100124)
        scoped=next(c for c in out if c['chat']==8)
        assert f'Сообщения: 1 · группы: 1' in scoped['text']
        assert 'Во всей волне: 2' in scoped['text']
        assert {b.url for row in scoped['markup'].inline_keyboard for b in row if b.url}=={f'https://t.me/c/123/{in_scope}'}
        another_outside,out=await feed('@Connect_TG_ROBOT 1a23456bcd',chat=-100124)
        assert {c['chat'] for c in out}=={7,8}
        assert 'Во всей волне: 3' in next(c for c in out if c['chat']==8)['text']
        out=await press(scoped,'preview',prefix='wave',actor=8)
        scoped_preview=next(c for c in out if c['method']=='sendMessage')
        assert '-100124' not in scoped_preview['text'] and 'В других группах: 2' in scoped_preview['text']
        wave_unknown.add(another_outside)
        out=await press(scoped_preview,'confirm',prefix='wave',actor=8)
        assert {(c['chat'],c['message']) for c in out if c['method']=='deleteMessage'}=={
            (-100123,in_scope),(-100124,outside_id),(-100124,another_outside),
        }
        report=next(c for c in out if c['method']=='sendMessage' and c['chat']==7)
        assert 'Модератор Synthetic (ID 8)' in report['text'] and 'Удалено: 2 из 3' in report['text']
        assert '-100124' in report['text'] and report['markup'].inline_keyboard[0][0].url==f'https://t.me/c/124/{another_outside}'
        result=next(c for c in out if c['method']=='editMessageText')
        assert 'В других группах: 1' in result['text'] and '-100124' not in result['text'] and not result['markup']
        with sqlite3.connect(ns['DATASET_PATH']) as c:assert c.execute('SELECT count(*) FROM messages').fetchone()[0]==200
        await bot.session.close()
    asyncio.run(run())
print('Public review dispatcher OK')
'''
            r=subprocess.run([sys.executable,'-c',script,str(ROOT/'bot_new.py')],cwd=temp,env=env,capture_output=True,text=True,timeout=60)
            log=(temp/'bot.log').read_text(encoding='utf-8') if (temp/'bot.log').exists() else ''
            self.assertEqual(r.returncode,0,r.stdout+r.stderr+log)
            self.assertIn('Public review dispatcher OK',r.stdout)
