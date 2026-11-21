"""One editable report per wave and global administrator."""
import asyncio
from contextlib import closing
import json
import logging
from time import time

from spam_waves import QUIET_SECONDS


logger = logging.getLogger(__name__)


class WaveReports:
    def __init__(self, bot, tickets, admins):
        self.bot = bot
        self.tickets = tickets
        self.admins = tuple(dict.fromkeys(admins))
        self.timers = {}
        self.delivery_lock = asyncio.Lock()

    @staticmethod
    def message_key(chat_id, message_id):
        return f'{chat_id}:{message_id}'

    def change(self, wave_id, update):
        key = f'wave_report:{wave_id}'
        with closing(self.tickets.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM review_meta WHERE key=?', (key,)).fetchone()
            report = json.loads(row['value']) if row else None
            changed = update(db, report)
            if changed is not None:
                db.execute('INSERT OR REPLACE INTO review_meta VALUES(?,?)', (key, json.dumps(changed)))
            return changed

    def get(self, wave_id):
        with closing(self.tickets.connect()) as db:
            row = db.execute('SELECT value FROM review_meta WHERE key=?', (f'wave_report:{wave_id}',)).fetchone()
        return json.loads(row['value']) if row else None

    def observe(self, current, earlier):
        wave_id = current.wave_id

        def add(db, report):
            report = report or dict(start=min(c.at for c in (*earlier, current)), duration=0,
                                    messages={}, deliveries={}, corrections=[], restrictions={}, closed=False)
            report['duration'] = max(report['duration'], round(current.at - report['start']))
            for candidate in (*earlier, current):
                key = self.message_key(*candidate.key)
                report['messages'].setdefault(key, {
                    'deletion': candidate.deletion,
                    'type': 'Сделка USDT' if candidate.decision.reason == 'direct_usdt_trade_rule' else 'Спам по модели',
                    'published_at': int(candidate.message.date.timestamp()),
                })
                db.execute('INSERT OR REPLACE INTO review_meta VALUES(?,?)', (f'wave_message:{key}', wave_id))
            return report

        self.change(wave_id, add)
        if wave_id in self.timers:
            self.timers[wave_id].cancel()
        loop = asyncio.get_running_loop()
        self.timers[wave_id] = loop.call_later(QUIET_SECONDS, lambda: loop.create_task(self.close(wave_id)))

    def record_deletion(self, candidate):
        if not candidate.wave_id:
            return

        def update(db, report):
            if report:
                report['messages'][self.message_key(*candidate.key)]['deletion'] = candidate.deletion
            return report

        self.change(candidate.wave_id, update)

    def wave_for(self, chat_id, message_id):
        with closing(self.tickets.connect()) as db:
            key = self.message_key(chat_id, message_id)
            row = db.execute('SELECT value FROM review_meta WHERE key=?', (f'wave_message:{key}',)).fetchone()
        return row['value'] if row else None

    async def close(self, wave_id):
        self.timers.pop(wave_id, None)

        def close_report(db, report):
            report['closed'] = True
            return report

        try:
            self.change(wave_id, close_report)
            await self.publish(wave_id)
        except Exception:
            logger.exception('Failed to close wave report %s', wave_id)

    async def recover(self):
        with closing(self.tickets.connect()) as db:
            incomplete = [row['key'].split(':', 1)[1] for row in db.execute(
                "SELECT key FROM review_meta WHERE key LIKE 'wave_report:%' AND json_extract(value, '$.closed')=0"
            )]
        for wave_id in incomplete:
            await self.close(wave_id)

    @staticmethod
    def render(wave_id, report):
        messages = report['messages']
        deleted = sum(item['deletion'] == 'deleted' for item in messages.values())
        corrected = {event['message'] for event in report['corrections'] if 'message' in event}
        confirmed = len(messages) - len(corrected)
        effective = sum(item['deletion'] == 'deleted' and key not in corrected for key, item in messages.items())
        share = f'{effective / confirmed:.0%}' if confirmed else '—'
        groups = sorted({key.split(':')[0] for key in messages})
        types = sorted({item['type'] for item in messages.values()})
        restricted = sum(item['outcome'] == 'restricted' for item in report['restrictions'].values())
        released = sum(item['outcome'] == 'released' for item in report['restrictions'].values())
        users = ', '.join(list(report['restrictions'])[:12]) or '—'
        examples = ', '.join(list(messages)[-12:])
        status = 'Завершена' if report['closed'] else 'Активна'
        return (
            f'Спам-волна {wave_id} · {status}\n'
            f'Обнаружено: {len(messages)} · Удалено: {deleted} · Исправлений по сообщениям: {len(corrected)}\n'
            f'Ограничено участников: {restricted + released} · Ограничений снято: {released}\n'
            f'Участники (группа:ID): {users}\n'
            f'Группы ({len(groups)}): {", ".join(groups[:30])}' + (' …' if len(groups) > 30 else '') + '\n'
            f'Продолжительность: {report["duration"]} с\nТип: {", ".join(types)}\n'
            f'Доля подтверждённых удалений: {share} ({effective}/{confirmed})\n'
            f'Последние сообщения (группа:ID): {examples}\n'
            f'Исправление: /wave_ham {wave_id} ID_группы ID_сообщения\n'
            '/wave_release ID_группы ID_участника'
        )

    async def publish(self, wave_id):
        async with self.delivery_lock:
            report = self.get(wave_id)
            if not report:
                return
            text = self.render(wave_id, report)
            for admin in self.admins:
                key = str(admin)
                delivery = report['deliveries'].get(key)
                try:
                    if delivery is None:
                        self.save_delivery(wave_id, key, {'id': None, 'text': None})
                        message = await self.bot.send_message(admin, text, parse_mode=None)
                        self.save_delivery(wave_id, key, {'id': message.message_id, 'text': text})
                    elif delivery['id'] is not None and delivery['text'] != text:
                        await self.bot.edit_message_text(chat_id=admin, message_id=delivery['id'],
                                                         text=text, parse_mode=None)
                        self.save_delivery(wave_id, key, {'id': delivery['id'], 'text': text})
                except Exception as exc:
                    logger.warning('Wave report delivery failed for %s: %s', wave_id, type(exc).__name__)

    def save_delivery(self, wave_id, key, delivery):
        def update(db, report):
            report['deliveries'][key] = delivery
            return report
        self.change(wave_id, update)

    async def correct_message(self, wave_id, chat_id, message_id, actor):
        key = self.message_key(chat_id, message_id)

        def correct(db, report):
            if not report or key not in report['messages']:
                return None
            inserted = db.execute('INSERT OR IGNORE INTO review_meta VALUES(?,?)',
                                  (f'wave_ham:{key}', wave_id))
            if inserted.rowcount:
                report['corrections'].append({'message': key, 'actor': actor, 'at': time()})
            return report

        changed = self.change(wave_id, correct)
        if changed:
            await self.publish(wave_id)
        return changed is not None

    async def restriction(self, wave_id, chat_id, user_id, outcome, actor=None):
        key = self.message_key(chat_id, user_id)

        def update(db, report):
            if report:
                report['restrictions'][key] = {'outcome': outcome}
                if actor is not None:
                    report['corrections'].append({'user': key, 'actor': actor, 'at': time()})
            return report

        self.change(wave_id, update)
        await self.publish(wave_id)
