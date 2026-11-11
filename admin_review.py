"""Persistent, administrator-confirmed deletion. No training or account bans."""
import asyncio
import hashlib
import json
from contextlib import closing
from datetime import datetime
import secrets
import sqlite3
import time
from pathlib import Path
from aiogram import Router,F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton,BufferedInputFile
from monthly_stats import MSK,monthly_report,format_report,csv_report

SIGNAL_NAMES = {
    'direct_usdt_trade': 'Сделка с USDT',
    'remote_work_private_contact': 'Работа с приглашением в личку',
}
from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError,TelegramNotFound

class Tickets:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS tickets(
                token TEXT PRIMARY KEY,chat_id INTEGER,message_id INTEGER,admin_id INTEGER,
                expires REAL,state TEXT NOT NULL,notification_id INTEGER,acted_by INTEGER);
                CREATE TABLE IF NOT EXISTS review_audit(id INTEGER PRIMARY KEY,token TEXT,state TEXT,at REAL);
                CREATE TABLE IF NOT EXISTS ticket_evidence(token TEXT PRIMARY KEY,kind TEXT NOT NULL,
                    text TEXT NOT NULL,text_sha256 TEXT NOT NULL,model_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback_events(id INTEGER PRIMARY KEY,token TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL CHECK(label IN ('HAM','SPAM')),admin_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS deletion_events(chat_id INTEGER,message_id INTEGER,origin TEXT NOT NULL,
                    at REAL NOT NULL,PRIMARY KEY(chat_id,message_id));
                CREATE TABLE IF NOT EXISTS feedback_keys(chat_id INTEGER,message_id INTEGER,text_sha256 TEXT,reason TEXT,
                    PRIMARY KEY(chat_id,message_id,text_sha256,reason));
                CREATE TABLE IF NOT EXISTS review_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
            c.execute('''INSERT OR IGNORE INTO feedback_keys SELECT t.chat_id,t.message_id,e.text_sha256,f.reason
                         FROM feedback_events f JOIN tickets t USING(token) JOIN ticket_evidence e USING(token)''')
            c.execute("INSERT OR IGNORE INTO review_meta VALUES('stats_started_at',?)",(str(time.time()),));c.commit()
    def connect(self):
        c=sqlite3.connect(self.path,timeout=5);c.row_factory=sqlite3.Row;return c
    def create(self,chat_id,message_id,admin_id,text='',model=None,kind='review'):
        if kind not in {'review','deleted'}:raise ValueError('Unknown card type')
        token=secrets.token_hex(12)
        with closing(self.connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            if kind=='review' and c.execute("SELECT 1 FROM tickets WHERE chat_id=? AND message_id=? AND state IN ('processing','deleted','unknown','kept','corrected')",(chat_id,message_id)).fetchone():
                raise ValueError('Сообщение уже обработано; проверьте исходный чат')
            c.execute('INSERT INTO tickets VALUES(?,?,?,?,?,?,NULL,NULL)',(token,chat_id,message_id,admin_id,time.time()+86400,'pending'))
            c.execute('INSERT INTO ticket_evidence VALUES(?,?,?,?,?)',(token,kind,text,hashlib.sha256(text.encode('utf-8')).hexdigest(),json.dumps(model or {},ensure_ascii=False)))
            c.commit()
        return token
    def bind(self,token,notification_id):
        with closing(self.connect()) as c:c.execute('UPDATE tickets SET notification_id=? WHERE token=?',(notification_id,token));c.commit()

    def claim_direct_spam(self, chat_id, message_id, admin_id, text, *, reason, chat_title=None):
        token = secrets.token_hex(12)
        now = time.time()
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        with closing(self.connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('''SELECT 1 FROM tickets WHERE chat_id=? AND message_id=?
                AND state IN ('processing','deleted','unknown','corrected')
                UNION ALL SELECT 1 FROM deletion_events WHERE chat_id=? AND message_id=?''',
                (chat_id, message_id, chat_id, message_id)).fetchone():
                return None
            inserted = c.execute('INSERT OR IGNORE INTO feedback_keys VALUES(?,?,?,?)',
                                 (chat_id, message_id, digest, reason))
            if not inserted.rowcount:
                return None
            c.execute("UPDATE tickets SET state='command_superseded' WHERE chat_id=? AND message_id=? AND state='pending'",
                      (chat_id, message_id))
            c.execute('INSERT INTO tickets VALUES(?,?,?,?,?,?,NULL,?)',
                      (token, chat_id, message_id, admin_id, now, 'processing', admin_id))
            c.execute('INSERT INTO ticket_evidence VALUES(?,?,?,?,?)',
                      (token, 'review', text, digest, json.dumps({'chat_title': chat_title}, ensure_ascii=False)))
            c.execute('INSERT INTO feedback_events(token,label,admin_id,reason,at) VALUES(?,?,?,?,?)',
                      (token, 'SPAM', admin_id, reason, now))
            c.execute('INSERT INTO review_audit(token,state,at) VALUES(?,?,?)', (token, 'processing', now))
            c.commit()
        return token
    def claim(self,token,actor,notification_chat,notification_id,allowed_chats,choice):
        with closing(self.connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM tickets WHERE token=?',(token,)).fetchone()
            if not row or row['admin_id']!=actor or notification_chat!=actor or row['notification_id']!=notification_id:
                return None,'Эта кнопка недоступна.'
            if row['chat_id'] not in allowed_chats:return None,'Чат больше не подключён.'
            if row['expires']<time.time():return None,'Срок кнопки истёк. Отправьте сообщение на проверку заново.'
            if row['state']!='pending':return None,'Уже обработано или выполняется. Повторного удаления не будет.'
            evidence=c.execute('SELECT * FROM ticket_evidence WHERE token=?',(token,)).fetchone()
            if not evidence or choice not in ({'ham'} if evidence['kind']=='deleted' else {'spam','keep'}):return None,'Кнопка не соответствует уведомлению.'
            reason='false_positive_after_deletion' if choice=='ham' else 'explicit_review'
            inserted=c.execute('INSERT OR IGNORE INTO feedback_keys VALUES(?,?,?,?)',(row['chat_id'],row['message_id'],evidence['text_sha256'],reason))
            if not inserted.rowcount:return None,'Ответ по этому сообщению уже записан.'
            # One decision per source message, even when sent to several admins.
            state={'spam':'processing','keep':'kept','ham':'corrected'}[choice]
            c.execute("UPDATE tickets SET state=?,acted_by=? WHERE chat_id=? AND message_id=? AND state='pending'",(state,actor,row['chat_id'],row['message_id']))
            c.execute('INSERT INTO feedback_events(token,label,admin_id,reason,at) VALUES(?,?,?,?,?)',
                      (token,'SPAM' if choice=='spam' else 'HAM',actor,reason,time.time()))
            c.execute('INSERT INTO review_audit(token,state,at) VALUES(?,?,?)',(token,state,time.time()));c.commit()
            return dict(row),None
    def finish(self,token,state,origin='manual'):
        with closing(self.connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT chat_id,message_id FROM tickets WHERE token=?',(token,)).fetchone()
            c.execute("UPDATE tickets SET state=? WHERE chat_id=? AND message_id=? AND state='processing'",(state,row['chat_id'],row['message_id']))
            c.execute('INSERT INTO review_audit(token,state,at) VALUES(?,?,?)',(token,state,time.time()))
            if state=='deleted':c.execute('INSERT OR IGNORE INTO deletion_events VALUES(?,?,?,?)',(row['chat_id'],row['message_id'],origin,time.time()))
            c.commit()
    def invalidate(self,chat_id,message_id):
        with closing(self.connect()) as c:
            c.execute("UPDATE tickets SET state='edited' WHERE chat_id=? AND message_id=? AND state='pending'",(chat_id,message_id));c.commit()
    def feedback_report(self):
        with closing(self.connect()) as c:
            rows=[dict(r) for r in c.execute('''SELECT f.id,f.label,f.admin_id,f.reason,f.at,
                t.chat_id,t.message_id,t.state AS action_status,e.kind,e.text,e.text_sha256,e.model_json
                FROM feedback_events f JOIN tickets t USING(token) JOIN ticket_evidence e USING(token)
                ORDER BY f.id''')]
        for row in rows:
            row['model']=json.loads(row.pop('model_json'))
            row['telegram_delete_status']='confirmed_deleted' if row['kind']=='deleted' or row['action_status']=='deleted' else row['action_status'] if row['action_status'] in {'processing','unknown','rejected','missing'} else 'not_requested'
        return dict(schema_version=1,automatic_training=False,contains_original_text=True,feedback=rows)

    def signal_stats(self):
        with closing(self.connect()) as c:
            rows = list(c.execute('''SELECT t.chat_id,t.message_id,e.model_json,f.label
                FROM tickets t JOIN ticket_evidence e USING(token)
                LEFT JOIN feedback_events f USING(token)
                WHERE t.notification_id IS NOT NULL AND e.kind='review' '''))
        counts = {signal: {'shown': set(), 'SPAM': set(), 'HAM': set()} for signal in SIGNAL_NAMES}
        for row in rows:
            signal = json.loads(row['model_json']).get('review_signal')
            if signal not in counts:
                continue
            key = (row['chat_id'], row['message_id'])
            counts[signal]['shown'].add(key)
            if row['label'] in {'SPAM', 'HAM'}:
                counts[signal][row['label']].add(key)
        return {signal: {key: len(values) for key, values in totals.items()}
                for signal, totals in counts.items() if totals['shown']}
    def evidence(self,token):
        with closing(self.connect()) as c:
            row=c.execute('SELECT * FROM ticket_evidence WHERE token=?',(token,)).fetchone()
        return dict(row)
    def record_auto_delete(self,chat_id,message_id):
        with closing(self.connect()) as c:
            c.execute('INSERT OR IGNORE INTO deletion_events VALUES(?,?,?,?)',(chat_id,message_id,'auto',time.time()));c.commit()
    def stats(self,now=None):
        with closing(self.connect()) as c:
            c.execute('BEGIN')
            start=float(c.execute("SELECT value FROM review_meta WHERE key='stats_started_at'").fetchone()[0])
            deletions=[dict(r) for r in c.execute('SELECT origin,at FROM deletion_events')]
            feedback=[dict(r) for r in c.execute('SELECT label,reason,at FROM feedback_events')]
        return monthly_report(deletions,feedback,start,now)

def source_link(chat_id, message_id):
    # Only supergroup/channel IDs support Telegram's private message links.
    value = str(chat_id)
    if value.startswith('-100') and value[4:].isdigit() and int(message_id) > 0:
        return f'https://t.me/c/{value[4:]}/{message_id}'
    return None


def deletion_failure(exc):
    message = str(exc).lower()
    if 'message to delete not found' in message or isinstance(exc, TelegramNotFound):
        return 'rejected', 'Сообщение недоступно. Возможно, его уже удалили. Бот не подтвердил удаление; проверьте чат.'
    if isinstance(exc, TelegramForbiddenError) or any(x in message for x in (
            'not enough rights', 'need administrator rights', 'chat_admin_required')):
        return 'rejected', 'Не удалось удалить: нет доступа или прав. Проверьте, что бот состоит в чате и имеет право удалять сообщения. При необходимости удалите сообщение вручную.'
    if "message can't be deleted" in message:
        return 'rejected', 'Telegram не разрешил удалить это сообщение. Возможны ограничения по возрасту или типу сообщения. Проверьте чат и при необходимости удалите его вручную.'
    return 'rejected', 'Telegram отклонил удаление. Проверьте сообщение и права бота в чате; при необходимости удалите вручную.'


class AdminReview:
    def __init__(self,bot,tickets,admins,chats,dry_run=False,*,moderator_chats=None):
        self.bot=bot;self.tickets=tickets;self.admins=admins;self.chats=chats;self.dry_run=dry_run
        self.moderator_chats = dict(moderator_chats or {})
        self.reviewers = tuple(dict.fromkeys((*admins, *self.moderator_chats)))

    def can_review(self, actor, chat_id):
        return chat_id in self.chats and (
            actor in self.admins or chat_id in self.moderator_chats.get(actor, ())
        )

    def reviewers_for(self, chat_id):
        return tuple(actor for actor in self.reviewers if self.can_review(actor, chat_id))

    async def delete_claimed(self, token, chat_id, message_id, *, origin='manual'):
        try:
            response = await self.bot.delete_message(chat_id, message_id)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound) as exc:
            outcome, status = deletion_failure(exc)
            if origin == 'wave_delete' and 'message to delete not found' in str(exc).lower():
                outcome = 'missing'
        except asyncio.CancelledError:
            self.tickets.finish(token, 'unknown', origin)
            raise
        except Exception:
            outcome = 'unknown'
            status = 'Результат удаления неизвестен. Проверьте исходный чат; автоматического повтора не будет.'
        else:
            outcome = 'deleted' if response is True else 'unknown'
            status = 'Исходное сообщение удалено.' if response is True else 'Удаление не подтверждено. Проверьте исходный чат.'
        self.tickets.finish(token, outcome, origin)
        return outcome, status
    async def send(self,admin_id,chat_id,message_id,text,model=None,kind='review',chat_title=None):
        if self.dry_run:raise ValueError('Пробный режим: карточки действий отключены.')
        if not self.can_review(admin_id,chat_id):raise ValueError('Unapproved review destination')
        with closing(self.tickets.connect()) as c:
            existing=c.execute('''SELECT 1 FROM tickets t JOIN ticket_evidence e USING(token)
                WHERE t.chat_id=? AND t.message_id=? AND t.admin_id=? AND t.state='pending'
                AND t.expires>? AND t.notification_id IS NOT NULL AND e.kind=? AND e.text_sha256=?''',
                (chat_id,message_id,admin_id,time.time(),kind,hashlib.sha256(text.encode('utf-8')).hexdigest())).fetchone()
        if existing:return None
        if chat_title:
            model = {**(model or {}), 'chat_title': chat_title}
        token=self.tickets.create(chat_id,message_id,admin_id,text,model,kind)
        buttons=[
            InlineKeyboardButton(text='Спам — удалить',callback_data='rv:spam:'+token),
            InlineKeyboardButton(text='Не спам — оставить',callback_data='rv:keep:'+token)]
        if kind=='deleted':buttons=[InlineKeyboardButton(text='Не спам — ошибка удаления',callback_data='rv:ham:'+token)]
        rows=[[button] for button in buttons]
        link=source_link(chat_id,message_id)
        if link and kind!='deleted':rows.append([InlineKeyboardButton(text='Открыть сообщение',url=link)])
        keyboard=InlineKeyboardMarkup(inline_keyboard=rows)
        heading='Бот удалил сообщение как спам' if kind=='deleted' else 'Подозрение на спам — ваше решение'
        signal_name = SIGNAL_NAMES.get((model or {}).get('review_signal'))
        if kind == 'review' and signal_name:
            heading += f'\nПричина: {signal_name}'
        footer='Отметка ошибки сохранится для анализа. Восстановить исходное сообщение нельзя.' if kind=='deleted' else 'Удаление только по вашему нажатию.'
        title=str(chat_title or chat_id).replace('\n',' ')[:160]
        excerpt=text[:2400]+('\n[Текст сокращён]' if len(text)>2400 else '')
        card=(f'{heading}\nЧат: {title} ({chat_id})\nСообщение: {message_id}\n\n{excerpt}\n\n{footer}\nКнопки действуют 24 часа.')
        result=await self.bot.send_message(admin_id,card,reply_markup=keyboard,parse_mode=None)
        self.tickets.bind(token,result.message_id)
        return result
    async def callback(self,query):
        if self.dry_run:
            await query.answer('Пробный режим: действия по кнопкам отключены.',show_alert=True);return
        actor=query.from_user.id
        if actor not in self.reviewers or not query.message or query.message.chat.type!='private':
            await query.answer('Только для назначенного администратора в личном чате.',show_alert=True);return
        parts=(query.data or '').split(':')
        if len(parts)!=3 or parts[0]!='rv' or parts[1] not in {'spam','keep','ham'}:
            await query.answer('Некорректная кнопка.',show_alert=True);return
        allowed_chats = self.chats if actor in self.admins else self.moderator_chats.get(actor, ())
        row,error=self.tickets.claim(parts[2],actor,query.message.chat.id,query.message.message_id,allowed_chats,parts[1])
        if error:await query.answer(error,show_alert=True);return
        # Ack failure must not turn into a deletion failure or prevent a confirmed action.
        try:await query.answer('Принято.')
        except Exception:pass
        markup=None
        if parts[1]=='ham':status='Ошибка удаления отмечена: НЕ СПАМ. Текст и решение модели сохранены для анализа. Восстановление и обучение не выполнялись.'
        elif parts[1]=='keep':status='НЕ СПАМ — бот не удалял сообщение. Ваше решение сохранено для анализа. В обучение не добавлено.'
        else:
            outcome,status=await self.delete_claimed(parts[2],row['chat_id'],row['message_id'])
            if outcome=='deleted':
                evidence=self.tickets.evidence(parts[2])
                correction=self.tickets.create(row['chat_id'],row['message_id'],actor,evidence['text'],json.loads(evidence['model_json']),kind='deleted')
                self.tickets.bind(correction,query.message.message_id)
                markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Не спам — ошибка удаления',callback_data='rv:ham:'+correction)]])
        evidence=self.tickets.evidence(parts[2])
        if markup is None and parts[1]!='ham' and (parts[1]=='keep' or outcome!='deleted'):
            link=source_link(row['chat_id'],row['message_id'])
            if link:markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Проверить сообщение в чате',url=link)]])
        status+=f"\nЧат: {row['chat_id']}\nСообщение: {row['message_id']}\n\n{evidence['text'][:2800]}"
        try:await query.message.edit_text(status,reply_markup=markup,parse_mode=None)
        except Exception:
            # No extra notification: avoid duplicates after an ambiguous edit timeout.
            try:await query.answer('Решение сохранено, но карточка не обновилась. Проверьте исходный чат; повторного удаления не будет.',show_alert=True)
            except Exception:pass

def make_router(service):
    router=Router(name='administrator_review')
    router.callback_query.register(service.callback,F.data.startswith('rv:'))
    @router.message(Command('review'))
    async def request(message):
        if not message.from_user or message.from_user.id not in service.reviewers:return
        if message.chat.id in service.chats and not service.can_review(message.from_user.id,message.chat.id):
            return
        if message.chat.id not in service.chats or not message.reply_to_message:
            await message.reply('Ответьте /review на исходное сообщение в подключённом чате.');return
        if service.dry_run:
            await message.reply('Пробный режим: карточки действий отключены. Для диагностики используйте /test_spam.');return
        original=message.reply_to_message
        result=await service.send(message.from_user.id,message.chat.id,original.message_id,original.text or original.caption or '[Нет текста]',chat_title=getattr(message.chat,'title',None))
        await message.reply('Карточка отправлена вам в личный чат с ботом.' if result else 'Карточка уже есть в вашем личном чате с ботом; используйте её кнопки.')
    @router.message(Command('review_stats'))
    async def stats(message):
        if not message.from_user or message.from_user.id not in service.admins or message.chat.type!='private':return
        rows=service.tickets.feedback_report()['feedback']
        summary = f"Ответов администраторов: {len(rows)}\nСпам: {sum(r['label']=='SPAM' for r in rows)}\nНе спам: {sum(r['label']=='HAM' for r in rows)}\nОтметок ошибочного удаления: {sum(r['reason']=='false_positive_after_deletion' for r in rows)}\nЭто число ответов, не точность модели. В обучение автоматически не добавляются."
        for reason, label in (('spam_command', '/spam'), ('wave_delete', 'Удаление волны')):
            actions = [r for r in rows if r['reason'] == reason]
            if actions:
                confirmed = sum(r['telegram_delete_status'] == 'confirmed_deleted' for r in actions)
                summary += f'\n{label} — отметок {len(actions)}, подтверждённых удалений {confirmed}'
        for actor in service.reviewers:
            actions = [r for r in rows if r['admin_id'] == actor]
            if actions:
                confirmed = sum(r['label'] == 'SPAM' and r['telegram_delete_status'] == 'confirmed_deleted'
                                for r in actions)
                summary += f'\nID {actor} — решений {len(actions)}, подтверждённых удалений {confirmed}'
        signals = service.tickets.signal_stats()
        if signals:
            lines = [
                f"{SIGNAL_NAMES[signal]} — показано {totals['shown']}, спам {totals['SPAM']}, не спам {totals['HAM']}"
                for signal, totals in signals.items()
            ]
            summary += '\n\nПо дополнительным сигналам (сообщения):\n' + '\n'.join(lines)
        await message.reply(summary)
    @router.message(Command('stat'))
    async def stat(message):
        if not message.from_user or message.from_user.id not in service.admins or message.chat.type!='private':return
        await message.reply(format_report(service.tickets.stats()),parse_mode=None)
    @router.message(Command('stat_export'))
    async def stat_export(message):
        if not message.from_user or message.from_user.id not in service.admins or message.chat.type!='private':return
        report=service.tickets.stats()
        await service.bot.send_document(message.chat.id,BufferedInputFile(csv_report(report),filename='antispam-monthly-'+report['as_of'][:10]+'.csv'),
                                        caption='Три календарных месяца, МСК. Текущий месяц не завершён. Только счётчики действий и отметок; исходных сообщений и ID в файле нет.')
    @router.message(Command('spam'))
    async def mark_spam(message):
        if not message.from_user or message.from_user.id not in service.reviewers:
            return
        if message.chat.id in service.chats and not service.can_review(message.from_user.id,message.chat.id):
            return
        original = message.reply_to_message
        if (message.chat.id not in service.chats or original is None
                or original.chat.id != message.chat.id):
            await message.reply('Ответьте /spam на исходное сообщение в подключённой группе.')
            return
        if service.dry_run:
            await message.reply('Пробный режим: /spam не сохраняет отметку и не удаляет сообщение.')
            return
        try:
            token = service.tickets.claim_direct_spam(
                message.chat.id, original.message_id, message.from_user.id,
                original.text or original.caption or '[Нет текста]', reason='spam_command',
                chat_title=message.chat.title,
            )
        except sqlite3.Error:
            await message.reply('Не удалось сохранить отметку. Бот не удалял сообщение.')
            return
        if token is None:
            await message.reply('Сообщение уже обработано или выполняется удаление.')
            return
        try:
            _, status = await service.delete_claimed(
                token, message.chat.id, original.message_id, origin='spam_command',
            )
        except sqlite3.Error:
            status = 'Результат удаления не удалось записать. Проверьте исходный чат.'
        await message.reply(f'Отметка «Спам» сохранена. {status}')
    @router.message(Command('moderation_log'))
    async def moderation_log(message, command):
        if not message.from_user or message.from_user.id not in service.admins or message.chat.type != 'private':
            return
        actor = (command.args or '').strip()
        if actor and not (actor.isascii() and actor.isdecimal() and len(actor) <= 20):
            await message.reply('Используйте /moderation_log или /moderation_log <Telegram ID>.')
            return
        rows = service.tickets.feedback_report()['feedback']
        recent = [row for row in rows if not actor or row['admin_id'] == int(actor)][-10:]
        if not recent:
            await message.reply('Решений пока нет.')
            return
        statuses = {
            'confirmed_deleted': 'удаление подтверждено', 'rejected': 'Telegram отказал',
            'missing': 'сообщение уже отсутствовало', 'unknown': 'результат неизвестен',
            'processing': 'результат ещё не записан', 'not_requested': 'удаление не запрашивалось',
        }
        lines = ['Последние решения модерации · МСК']
        for row in reversed(recent):
            at = datetime.fromtimestamp(row['at'], MSK).strftime('%d.%m %H:%M')
            title = str(row['model'].get('chat_title') or '').strip()[:70]
            chat = f"{title} ({row['chat_id']})" if title else str(row['chat_id'])
            label = 'Спам' if row['label'] == 'SPAM' else 'Не спам'
            status = statuses[row['telegram_delete_status']]
            if row['reason'] == 'false_positive_after_deletion':
                status = 'отметка ошибки после удаления'
            excerpt = ' '.join(row['text'].split())[:170]
            lines.append(f"{at} · ID {row['admin_id']} · {label} · {status}\n{chat} · №{row['message_id']}\n{excerpt}")
        await message.reply('\n\n'.join(lines), parse_mode=None)
    return router
