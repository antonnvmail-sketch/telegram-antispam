"""Send supplemental manual review alerts through the Telegram dispatcher."""

import asyncio
from collections import deque
import logging
import secrets
import sqlite3
from time import monotonic
from urllib.parse import urlsplit

from aiogram import BaseMiddleware, F
from aiogram.enums import MessageEntityType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from admin_review import source_link
from migration_candidate import MARKER, MAX_WAVE_LINKS, MigrationWave, Observation, migration_notice
from moderation_actions import ActionResult


def has_hidden_telegram_link(entities):
    for entity in entities or ():
        if entity.type != MessageEntityType.TEXT_LINK or not entity.url:
            continue
        try:
            url = urlsplit(entity.url)
            host = url.hostname
        except ValueError:
            continue
        if url.scheme in {"https", "http"} and host in {"t.me", "telegram.me"}:
            return True
        if url.scheme == "tg" and host in {"resolve", "join"}:
            return True
    return False


class MigrationMiddleware(BaseMiddleware):
    def __init__(self, chats, admins, *, dry_run, clock=monotonic, offer_reason=None, review_service=None):
        self.chats = frozenset(chats)
        self.admins = tuple(dict.fromkeys(admins))
        self.dry_run = dry_run
        self.clock = clock
        self.offer_reason = offer_reason
        self.review_service = review_service
        self.wave = MigrationWave()
        self.wave_lock = asyncio.Lock()
        self.wave_deliveries = {}
        self.confirmations = {}
        self.recent_notices = deque()
        self.notice_ids = set()

    def can_review(self, actor, chat_id):
        return not self.review_service or self.review_service.can_review(actor, chat_id)

    async def update_wave(self, bot, message, text):
        async with self.wave_lock:
            wave = self.wave.observe(Observation(
                self.clock(), message.chat.id, message.message_id, text, message.chat.title or "",
            ))
            self.wave_deliveries = {
                code: delivery for code, delivery in self.wave_deliveries.items()
                if self.wave.active.get(code) is delivery[0]
            }
            if wave is None:
                return
            _, deliveries = self.wave_deliveries.setdefault(wave.code, (wave, {}))
            total = len(wave.messages)
            for admin in self.admins:
                messages = tuple(item for item in wave.messages.values() if self.can_review(admin, item.chat_id))
                view = (total, tuple(item.key for item in messages))
                if not messages or (admin in deliveries and deliveries[admin][1] == view):
                    continue
                groups = len({item.chat_id for item in messages})
                count = f"первые {MAX_WAVE_LINKS}" if len(messages) == MAX_WAVE_LINKS else str(len(messages))
                lines = ["Проверьте серию похожих сообщений.", f"Сообщения: {count} · группы: {groups}"]
                if total > len(messages):
                    lines.append(f"Во всей волне: {total}. Здесь ссылки на ваши группы.")
                buttons = []
                for item in messages:
                    title = " ".join(item.chat_title.split())[:48] or str(item.chat_id)
                    label = f"{title} · №{item.message_id}"
                    url = source_link(item.chat_id, item.message_id)
                    if url:
                        buttons.append([InlineKeyboardButton(text=label, url=url)])
                    else:
                        lines.append(label)
                if self.review_service:
                    buttons.append([InlineKeyboardButton(
                        text=f"Удалить всю волну · {total}",
                        callback_data=f"wave:preview:{wave.code}",
                    )])
                markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
                body = "\n".join(lines)
                try:
                    if admin not in deliveries:
                        deliveries[admin] = (None, view)
                        sent = await bot.send_message(admin, body, reply_markup=markup, parse_mode=None)
                        deliveries[admin] = (sent.message_id, view)
                    elif deliveries[admin][0] is not None:
                        notification_id = deliveries[admin][0]
                        await bot.edit_message_text(
                            text=body, chat_id=admin, message_id=notification_id,
                            reply_markup=markup, parse_mode=None,
                        )
                        deliveries[admin] = (notification_id, view)
                except Exception:
                    logging.getLogger(__name__).exception("Wave review notification failed")

    async def wave_callback(self, query):
        admin = query.from_user.id
        message = query.message
        if self.dry_run or admin not in self.admins or not message or message.chat.id != admin:
            await query.answer("Эта кнопка недоступна.", show_alert=True)
            return
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Эта кнопка недоступна.", show_alert=True)
            return
        _, action, key = parts
        now = self.clock()
        self.confirmations = {
            token: item for token, item in self.confirmations.items() if item[0] > now
        }
        if action == "preview":
            delivery = self.wave_deliveries.get(key)
            if not delivery or delivery[1].get(admin, (None, ()))[0] != message.message_id:
                await query.answer("Карточка волны устарела.", show_alert=True)
                return
            wave = delivery[0]
            targets = tuple(wave.messages.values())
            visible = tuple(item for item in targets if self.can_review(admin, item.chat_id))
            if now - wave.last_at > self.wave.quiet_seconds or not visible:
                await query.answer("Окно удаления волны закрыто.", show_alert=True)
                return
            if wave.processing:
                await query.answer("Удаление этой волны уже выполняется.", show_alert=True)
                return
            await query.answer()
            token = secrets.token_hex(12)
            lines = ["Подтвердите удаление следующих сообщений:"]
            for item in visible:
                title = " ".join(item.chat_title.split())[:48] or str(item.chat_id)
                lines.append(f"{title} ({item.chat_id}) · №{item.message_id}")
            if len(visible) != len(targets):
                lines.append(f"В других группах: {len(targets) - len(visible)} совпадений.")
                lines.append(f"Подтверждение удалит всю серию из {len(targets)} сообщений.")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f"Подтвердить удаление · {len(targets)}", callback_data=f"wave:confirm:{token}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"wave:cancel:{token}"),
            ]])
            sent = await self.review_service.bot.send_message(
                admin, "\n".join(lines), reply_markup=keyboard, parse_mode=None,
            )
            self.confirmations[token] = (self.clock() + 60, admin, sent.message_id, wave, targets)
            return
        pending = self.confirmations.get(key)
        if not pending or pending[1:3] != (admin, message.message_id):
            await query.answer("Подтверждение истекло или уже обработано.", show_alert=True)
            return
        if action not in {"confirm", "cancel"}:
            await query.answer("Эта кнопка недоступна.", show_alert=True)
            return
        self.confirmations.pop(key)
        _, _, _, wave, targets = pending
        if action == "confirm":
            if wave.processing:
                await query.answer("Удаление этой волны уже выполняется.", show_alert=True)
                return
            if (self.wave.active.get(wave.code) is not wave
                    or not any(item.key in wave.messages and self.can_review(admin, item.chat_id) for item in targets)):
                await query.answer("Карточка волны устарела.", show_alert=True)
                return
            wave.processing = True
            self.confirmations = {
                token: item for token, item in self.confirmations.items() if item[3] is not wave
            }
        try:
            try:
                await query.answer()
            except Exception:
                logging.getLogger(__name__).exception("Wave confirmation acknowledgement failed")
            if action == "cancel":
                await message.edit_text("Удаление волны отменено.", reply_markup=None, parse_mode=None)
                return
            await self.delete_wave(query, wave, targets)
        finally:
            if action == "confirm":
                wave.processing = False

    async def delete_wave(self, query, wave, targets):
        admin = query.from_user.id
        counts = dict(deleted=0, missing=0, rejected=0, unknown=0, skipped=0)
        unresolved = []
        group_counts = {}
        for item in targets:
            if (self.wave.active.get(wave.code) is not wave
                    or item.key not in wave.messages or item.chat_id not in self.chats):
                counts["skipped"] += 1
                continue
            token = None
            try:
                token = self.review_service.tickets.claim_direct_spam(
                    item.chat_id, item.message_id, admin, item.text, reason="wave_delete",
                    chat_title=item.chat_title,
                )
                if token is None:
                    counts["skipped"] += 1
                    continue
                outcome, status = await self.review_service.delete_claimed(
                    token, item.chat_id, item.message_id, origin="wave_delete",
                )
            except sqlite3.Error:
                outcome = "unknown"
                status = (
                    "Результат удаления не удалось записать."
                    if token else "Не удалось сохранить отметку. Удаление не запрашивалось."
                )
                logging.getLogger(__name__).exception("Wave review result could not be recorded")
            counts[outcome] += 1
            outcomes = group_counts.setdefault(item.chat_id, {})
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcome in {"rejected", "unknown"}:
                unresolved.append((item, status.partition(". ")[0].rstrip(".")[:100]))
        summary = (
            f"Удалено: {counts['deleted']}\nУже отсутствовали: {counts['missing']}\n"
            f"Telegram отказал: {counts['rejected']}\nРезультат неизвестен: {counts['unknown']}\n"
            f"Пропущено: {counts['skipped']}"
        )
        buttons = []
        if unresolved:
            lines = ["Проверьте вручную:"]
            hidden = 0
            for item, status in unresolved:
                if not self.can_review(admin, item.chat_id):
                    hidden += 1
                    continue
                title = " ".join(item.chat_title.split())[:36] or str(item.chat_id)
                label = f"{title} · №{item.message_id}"
                lines.append(f"{label} — {status}")
                url = source_link(item.chat_id, item.message_id)
                if url:
                    buttons.append([InlineKeyboardButton(text=f"Открыть {label}", url=url)])
            if hidden:
                lines.append(f"В других группах: {hidden}. Подробности отправлены администратору.")
            summary += "\n\n" + "\n".join(lines)
        if admin not in self.review_service.admins and group_counts:
            name = " ".join(query.from_user.full_name.split())[:80] or str(admin)
            admin_lines = [
                f"Модератор {name} (ID {admin}) подтвердил удаление спам-волны.",
                f"Удалено: {counts['deleted']} из {len(targets)}.",
                f"Уже отсутствовали: {counts['missing']} · отказ: {counts['rejected']} · "
                f"неизвестно: {counts['unknown']} · пропущено: {counts['skipped']}.",
            ]
            titles = {item.chat_id: item.chat_title for item in targets}
            for chat_id, outcomes in group_counts.items():
                title = " ".join(titles[chat_id].split())[:48] or str(chat_id)
                admin_lines.append(f"{title} ({chat_id}) — удалено {outcomes.get('deleted', 0)} из {sum(outcomes.values())}.")
            admin_buttons = []
            for item, status in unresolved:
                admin_lines.append(f"{item.chat_id} · №{item.message_id} — {status}")
                url = source_link(item.chat_id, item.message_id)
                if url:
                    admin_buttons.append([InlineKeyboardButton(text=f"Открыть №{item.message_id}", url=url)])
            for owner in self.review_service.admins:
                try:
                    await self.review_service.bot.send_message(
                        owner, "\n".join(admin_lines), parse_mode=None,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=admin_buttons) if admin_buttons else None,
                    )
                except Exception:
                    logging.getLogger(__name__).exception("Wave result notification to administrator failed")
        await query.message.edit_text(
            summary, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
            parse_mode=None,
        )

    async def __call__(self, handler, message, data):
        update = data.get("event_update")
        is_edit = bool(message.edit_date or (update and update.edited_message))
        if is_edit and message.chat.id in self.chats:
            self.wave.invalidate((message.chat.id, message.message_id))
        result = await handler(message, data)
        if self.dry_run or message.chat.id not in self.chats:
            return result

        text = message.text or message.caption
        if not text or text.lstrip().startswith("/"):
            return result

        at = self.clock()
        entities = message.entities if message.text else message.caption_entities
        notice = migration_notice(text, hidden_telegram_link=has_hidden_telegram_link(entities))
        if MARKER.fullmatch(text.strip().casefold()):
            if not is_edit:
                await self.update_wave(data["bot"], message, text)
            return result
        offer = self.offer_reason(text) if self.offer_reason else None
        if isinstance(result, ActionResult) and result.decision != "allow":
            notice = False
            offer = None
        if notice or offer:
            while self.recent_notices and self.recent_notices[0][0] < at - 60:
                _, old_key = self.recent_notices.popleft()
                self.notice_ids.remove(old_key)
            key = (message.chat.id, message.message_id)
            if key in self.notice_ids:
                return result
            self.notice_ids.add(key)
            self.recent_notices.append((at, key))
        else:
            return result

        if notice:
            signal, heading = "notice", "Проверьте объявление о переносе группы."
        elif offer == "direct_usdt_trade":
            signal, heading = offer, "Проверьте предложение сделки с USDT."
        else:
            signal, heading = offer, "Проверьте предложение работы с приглашением в личку."
        body = f"{heading}\nЧат: {message.chat.id}\nСообщение: {message.message_id}\n\n{text[:2400]}"
        for admin in self.admins:
            if not self.can_review(admin, message.chat.id):
                continue
            try:
                if offer and not notice and self.review_service and not message.edit_date:
                    await self.review_service.send(
                        admin, message.chat.id, message.message_id, text,
                        model={"review_signal": offer}, chat_title=message.chat.title,
                    )
                else:
                    await data["bot"].send_message(admin, body, parse_mode=None)
            except Exception:
                logging.getLogger(__name__).exception("Migration review notification failed")
            else:
                logging.getLogger(__name__).info(
                    "Migration review sent: signal=%s chat=%s message=%s",
                    signal, message.chat.id, message.message_id,
                )
        return result


def attach_migration_review(dispatcher, chats, admins, *, dry_run, clock=monotonic,
                            offer_reason=None, review_service=None):
    middleware = MigrationMiddleware(
        chats, admins, dry_run=dry_run, clock=clock, offer_reason=offer_reason,
        review_service=review_service,
    )
    dispatcher.message.outer_middleware(middleware)
    dispatcher.edited_message.outer_middleware(middleware)
    if review_service:
        dispatcher.callback_query.register(middleware.wave_callback, F.data.startswith("wave:"))
