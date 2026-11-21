import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
import logging
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound
from config import TELEGRAM_TOKEN, ADMIN_IDS, CHAT_IDS, DATASET_PATH, MODEL_PATH, SPAM_DELETE_THRESHOLD, SPAM_MODERATE_THRESHOLD, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT
from database import init_db, add_message, get_all_messages, log_action, log_decision
from ml import train_model, evaluate_model, pending_training_count
from moderation import can_moderate_sender, is_admin
from moderation_actions import execute_actions
from decisions import load_versioned_model, classify_and_decide, persist_decision, diagnostic_text
import os
import io
from import_dataset import import_txt_to_db
from config import AUTO_DELETE_DIRECT_USDT, DRY_RUN, EXPERIMENTAL_REVIEW_ALERTS, MODERATOR_CHAT_IDS, validate_runtime
from migration_telegram import attach_migration_review
from offer_candidate import direct_trade_decision, is_direct_trade, review_reason
from spam_waves import SpamWaves
from wave_reports import WaveReports

validate_runtime()

# Логирование в файл и stdout
APP_VERSION = "1.0.5"
WAVE_AUTO_DELETE = not DRY_RUN
spam_waves = SpamWaves()

if __name__ == "__main__":
    from runtime_ops import acquire_instance
    _ops_directory = acquire_instance()

logger = logging.getLogger(__name__)
action_logger = logging.getLogger("antispam.actions")
action_logger.setLevel(logging.INFO)
logger.setLevel(logging.WARNING)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(level=logging.WARNING, handlers=[file_handler])

# Дополнительный уровень логирования для aiogram (диагностика)
aiogram_logger = logging.getLogger("aiogram")
aiogram_logger.setLevel(logging.WARNING)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

from admin_review import AdminReview, Tickets, make_router, restriction_subject
review_service = AdminReview(
    bot, Tickets(str(DATASET_PATH) + '.admin-review.sqlite'), ADMIN_IDS, CHAT_IDS,
    dry_run=DRY_RUN, moderator_chats=MODERATOR_CHAT_IDS, repeat_restrictions=WAVE_AUTO_DELETE,
)
wave_reports = WaveReports(bot, review_service.tickets, ADMIN_IDS)
review_service.wave_reports = wave_reports
if WAVE_AUTO_DELETE:
    dp.startup.register(wave_reports.recover)
if EXPERIMENTAL_REVIEW_ALERTS:
    attach_migration_review(
        dp, CHAT_IDS, review_service.reviewers, dry_run=DRY_RUN, offer_reason=review_reason,
        review_service=review_service,
    )
review_router = make_router(review_service)
dp.callback_query.register(review_service.callback, F.data.startswith('rv:'))
for command, handler in zip(('review','review_stats','stat','stat_export','spam','moderation_log'), review_router.message.handlers):
    dp.message.register(handler.callback, Command(command))
if __name__ == '__main__':
    from runtime_ops import install_runtime
    _runtime_ops = install_runtime(bot, dp, _ops_directory)


@dp.message(Command("wave_ham", "wave_release"))
async def correct_wave_moderation(message, command):
    actor = message.from_user
    if message.chat.type != "private" or actor is None or not is_admin(actor.id):
        return
    if not WAVE_AUTO_DELETE:
        await message.reply("Исправления действий выключены в пробном режиме.")
        return
    args = (command.args or "").split()
    wave_command = command.command == "wave_ham"
    usage = ("/wave_ham ID_волны ID_группы ID_сообщения" if wave_command
             else "/wave_release ID_группы ID_участника")
    if len(args) != (3 if wave_command else 2):
        await message.reply(usage)
        return
    try:
        chat_id, target_id = map(int, args[-2:])
    except ValueError:
        await message.reply(usage)
        return
    if chat_id not in CHAT_IDS or not 0 < target_id < 2**63:
        await message.reply("Укажите подключённую группу и действительный ID.")
        return
    if wave_command:
        corrected = await wave_reports.correct_message(args[0], chat_id, target_id, actor.id)
        status = ("Исправление сохранено. История исходного удаления доступна в отчёте."
                  if corrected else "Сообщение не найдено в указанной волне.")
    else:
        status = await review_service.release_repeat_restriction(chat_id, target_id, actor.id)
    await message.reply(status)

# Глобальные перехватчики исключений
import sys

def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        return
    logger.exception("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_uncaught_exception

def handle_asyncio_exception(loop, context):
    msg = context.get("message")
    exc = context.get("exception")
    if exc:
        logger.exception(f"Asyncio exception: {msg or ''}", exc_info=exc)
    else:
        logger.error(f"Asyncio error: {msg or context}")

# Удалено: глобальная регистрация обработчика цикла до его создания
# asyncio.get_event_loop().set_exception_handler(handle_asyncio_exception)

# Middleware для логирования ошибок хендлеров
from aiogram import BaseMiddleware
from typing import Callable, Dict, Any, Awaitable

class ErrorLoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]], event: types.TelegramObject, data: Dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except Exception as e:
            logger.exception(f"Handler exception: {e}")
            raise

dp.update.middleware(ErrorLoggingMiddleware())

# Функция для проверки прав бота в чате
async def check_bot_permissions(chat_id: int) -> dict:
    """Проверяет права бота в чате и возвращает информацию о них"""
    try:
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        permissions = {
            'can_delete_messages': bot_member.can_delete_messages,
            'can_restrict_members': bot_member.can_restrict_members,
            'status': bot_member.status,
            'is_admin': bot_member.status in ['administrator', 'creator']
        }
        logger.info(f"Bot permissions in chat {chat_id}: {permissions}")
        return permissions
    except Exception as e:
        logger.error(f"Failed to check bot permissions in chat {chat_id}: {e}")
        return {
            'can_delete_messages': False,
            'can_restrict_members': False,
            'status': 'unknown',
            'is_admin': False,
            'error': str(e)
        }

# validate_runtime() already requires an explicitly prepared local model.
init_db(DATASET_PATH)
model = load_versioned_model(MODEL_PATH)

def record_decision(message, decision, source):
    return persist_decision(
        decision, chat_id=message.chat.id, message_id=message.message_id, source=source,
        write_record=lambda record, **context: log_decision(DATASET_PATH, record, **context),
        logger=action_logger,
    )

def training_status(data=None):
    if data is None:
        data = get_all_messages(DATASET_PATH)
    pending = pending_training_count(getattr(model, 'pipeline', None), data)
    if pending is None:
        return 'Новых примеров после обучения: неизвестно. Выполните /train, чтобы начать учёт.'
    text = f'Новых примеров после обучения: {pending}.'
    if pending:
        text += ' Чтобы применить их, выполните /train.'
    return text


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.reply("🤖 Я антиспам-бот. Добавьте меня в группу и дайте права администратора. Для справки используйте /help.")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.reply(
        """
Доступные команды:
/start — приветствие
/help — эта справка
/status — проверка статуса бота и его прав (только для админов)
/check_permissions — проверка прав в конкретном чате (только для админов)
/add_spam <сообщение> — добавить пример спама
/add_ham <сообщение> — добавить пример не-спама
/train — обучить модель на текущем датасете
/stats — количество примеров в датасете
/evaluate — отчёт на обучающих данных, не независимая оценка (только для админов)
/import_data — импортировать строки из data.txt в базу (только для админов)
/test_spam <текст> — проверить текст на спам (только для админов)
/here — показать текущий chat.id (для настройки CHAT_IDS)
/review — ответьте на сообщение в подключённой группе: карточка придёт вам в личку
/spam — ответьте на спам в подключённой группе: сохранить отметку и удалить исходное сообщение
/stat — текущий и два предыдущих календарных месяца, МСК (администратору в личном чате)
/stat_export — месячные счётчики в CSV без текстов и ID
/review_stats — количество ответов администраторов
/moderation_log [ID] — последние решения и удаления, только для администратора
Кнопки действуют 24 часа. Обратная связь не запускает обучение автоматически.
При DRY_RUN=true карточки действий отключены.

Загрузка файлов через Telegram пока не реализована.
Используйте /add_spam, /add_ham или локальный import_dataset.py.
        """
    )

@dp.message(Command("here"))
async def cmd_here(message: types.Message):
    await message.reply(f"Этот чат: chat.id = {message.chat.id}")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только для администраторов.")
        return

    status_info = [f"🤖 Версия бота: {APP_VERSION}"]
    status_info.append(f"🧪 Пробный режим (DRY_RUN): {DRY_RUN}")

    # Проверка модели
    if model:
        status_info.append("✅ Модель загружена")
        status_info.append(f"Версия модели: {model.version}")
    else:
        status_info.append("❌ Модель не загружена")

    # Проверка датасета
    data = get_all_messages(DATASET_PATH)
    status_info.append(f"📊 В датасете: {len(data)} сообщений")
    status_info.append(training_status(data))

    # Проверка конфигурации
    status_info.append(f"🎯 Порог удаления: {SPAM_DELETE_THRESHOLD}")
    status_info.append(f"⚠️ Порог модерации: {SPAM_MODERATE_THRESHOLD}")
    status_info.append(f"👥 ID админов: {ADMIN_IDS}")
    status_info.append(f"💬 ID чатов для фильтрации: {CHAT_IDS}")

    # Проверка прав в чатах
    for chat_id in CHAT_IDS:
        perms = await check_bot_permissions(chat_id)
        if 'error' in perms:
            status_info.append(f"❌ Чат {chat_id}: Ошибка - {perms['error']}")
        else:
            delete_status = "✅" if perms['can_delete_messages'] else "❌"
            status_info.append(f"{delete_status} Чат {chat_id}: Удаление - {perms['can_delete_messages']}, Статус - {perms['status']}")

    await message.reply("\n".join(status_info))

@dp.message(Command("check_permissions"))
async def cmd_check_permissions(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только для администраторов.")
        return

    chat_id = message.chat.id
    perms = await check_bot_permissions(chat_id)

    if 'error' in perms:
        await message.reply(f"❌ Ошибка проверки прав: {perms['error']}")
    else:
        response = f"""
🔍 Права бота в этом чате:
📋 Статус: {perms['status']}
🗑️ Может удалять сообщения: {perms['can_delete_messages']}
🚫 Может ограничивать участников: {perms['can_restrict_members']}
👑 Является админом: {perms['is_admin']}

{'✅ Бот может удалять сообщения' if perms['can_delete_messages'] else '❌ Бот НЕ может удалять сообщения - дайте права администратора!'}
        """
        await message.reply(response)

@dp.message(Command("test_spam"))
async def cmd_test_spam(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только для администраторов.")
        return

    if not model:
        await message.reply("❌ Модель не загружена")
        return

    parts = message.text.split(maxsplit=1)
    text = parts[1].strip() if len(parts) == 2 else ""
    if not text:
        await message.reply("Используйте: /test_spam <текст для проверки>")
        return

    try:
        decision = classify_and_decide(
            model, text, delete_threshold=SPAM_DELETE_THRESHOLD,
            moderate_threshold=SPAM_MODERATE_THRESHOLD, dry_run=DRY_RUN,
        )
    except Exception as exc:
        logger.error("Diagnostic classification failed: %s", type(exc).__name__)
        await message.reply("❌ Не удалось получить решение модели. Действия не выполнялись.")
        return
    if AUTO_DELETE_DIRECT_USDT and is_direct_trade(text):
        decision = direct_trade_decision(decision)
    saved = record_decision(message, decision, "test_spam")
    await message.reply(diagnostic_text(decision, saved=saved))

@dp.message(Command("add_spam"))
async def cmd_add_spam(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только администраторы могут дообучать бота.")
        return
    parts = message.text.split(maxsplit=1)
    text = parts[1].strip() if len(parts) == 2 else ""
    if not text:
        await message.reply("Используйте: /add_spam <сообщение>")
        return
    add_message(DATASET_PATH, "spam", text)
    await message.reply("✅ Сообщение добавлено в датасет как spam.\n" + training_status())

@dp.message(Command("add_ham"))
async def cmd_add_ham(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только администраторы могут дообучать бота.")
        return
    parts = message.text.split(maxsplit=1)
    text = parts[1].strip() if len(parts) == 2 else ""
    if not text:
        await message.reply("Используйте: /add_ham <сообщение>")
        return
    add_message(DATASET_PATH, "ham", text)
    await message.reply("✅ Сообщение добавлено в датасет как ham.\n" + training_status())

@dp.message(Command("import_data"))
async def cmd_import_data(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только администраторы могут импортировать данные.")
        return
    try:
        import_txt_to_db("data.txt", DATASET_PATH)
        await message.reply("✅ Импорт из data.txt завершён. Теперь можно запустить /train.")
    except Exception as e:
        logger.error(f"Ошибка импорта из data.txt: {e}")
        await message.reply("Ошибка импорта из data.txt.")

@dp.message(Command("train"))
async def cmd_train(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только администраторы могут обучать модель.")
        return
    data = get_all_messages(DATASET_PATH)
    if not data:
        await message.reply("Датасет пуст.")
        return
    train_model(data, MODEL_PATH)
    global model
    model = load_versioned_model(MODEL_PATH)
    await message.reply("✅ Модель успешно обучена.")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только для администраторов.")
        return
    data = get_all_messages(DATASET_PATH)
    await message.reply(f"В датасете {len(data)} сообщений.")

@dp.message(Command("evaluate"))
async def cmd_evaluate(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только для администраторов.")
        return
    data = get_all_messages(DATASET_PATH)
    if not data:
        await message.reply("Датасет пуст.")
        return
    try:
        report = evaluate_model(data, MODEL_PATH)
        await message.reply(f"<pre>{report}</pre>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при оценке модели: {e}")
        await message.reply("Ошибка при оценке модели.")

@dp.message(F.document)
async def handle_document_upload(message: types.Message):
    # Файл принят, никаких сообщений не отправляем
    pass

@dp.message(F.chat.id.in_(CHAT_IDS))
async def filter_message(message: types.Message, *, edited=False):
    text = message.text or getattr(message, "caption", None)
    if not text or not text.strip() or text.lstrip().startswith("/"):
        return

    entities = message.entities if message.text else message.caption_entities
    quoted_or_forwarded = (message.forward_origin or message.is_automatic_forward
                           or message.quote or message.external_reply)
    blockquote = any(entity.type in {"blockquote", "expandable_blockquote"}
                     for entity in entities or ())
    direct_trade = (
        AUTO_DELETE_DIRECT_USDT
        and not (quoted_or_forwarded or blockquote)
        and not any(entity.type == "text_link" for entity in entities or ())
        and is_direct_trade(text)
    )
    wave_eligible = (not edited and message.chat.type in {"group", "supergroup"}
                     and not (quoted_or_forwarded or blockquote))
    standalone = direct_trade or (not edited and bool(message.text))
    if not (standalone or wave_eligible):
        return
    if not model:
        logger.warning("Model not loaded, skipping message filtering")
        return
    if not DRY_RUN and not await can_moderate_sender(bot, message, logger):
        return

    try:
        # For a direct match the model supplies audit scores, not the action.
        decision = classify_and_decide(
            model, text, delete_threshold=SPAM_DELETE_THRESHOLD,
            moderate_threshold=SPAM_MODERATE_THRESHOLD, dry_run=DRY_RUN,
        )
        if direct_trade:
            decision = direct_trade_decision(decision)
    except Exception:
        logger.exception("Error during message classification")
        return

    if wave_eligible and decision.action in {"moderate", "delete"}:
        matched, earlier = spam_waves.observe(message, decision, text)
        candidate = spam_waves.recent.get((message.chat.id, message.message_id))
        if matched:
            action_logger.info("Spam wave match in chat %s message %s", message.chat.id, message.message_id)
        if matched and WAVE_AUTO_DELETE:
            try:
                wave_reports.observe(candidate, earlier)
            except Exception:
                logger.exception("Failed to open or update wave report")
                tracked = False
            else:
                tracked = bool(wave_reports.admins)
            current = apply_wave_decision(message, decision, standalone=standalone, aggregate=tracked)
            try:
                result, *_ = await asyncio.gather(
                    current, *(apply_earlier_wave_decision(item, aggregate=tracked) for item in earlier),
                )
                return result
            finally:
                if tracked:
                    try:
                        await wave_reports.publish(candidate.wave_id)
                    except Exception:
                        logger.exception("Failed to publish wave report")
    if standalone:
        return await apply_filter_decision(message, decision, edited=edited)


async def apply_earlier_wave_decision(candidate, *, aggregate):
    message = candidate.message
    if candidate.decision.action == "delete" and (
        message.text or candidate.decision.reason == "direct_usdt_trade_rule"
    ):
        # The ordinary filter has already taken responsibility for this deletion.
        return
    if (await can_moderate_sender(bot, message, logger)
            and spam_waves.recent.get(candidate.key) is candidate):
        await apply_wave_decision(message, candidate.decision, standalone=False, aggregate=aggregate)
    else:
        candidate.deletion = "skipped"
        if aggregate:
            try:
                wave_reports.record_deletion(candidate)
            except Exception:
                logger.exception("Failed to record skipped wave deletion")


async def apply_wave_decision(message, decision, *, standalone, aggregate):
    candidate = spam_waves.recent.get((message.chat.id, message.message_id))
    if candidate is None or candidate.message is not message:
        return
    if not standalone or decision.action != "delete":
        decision = replace(decision, action="delete", reason="confirmed_spam_wave",
                           decision_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
    return await apply_filter_decision(message, decision, aggregate=aggregate)


async def apply_filter_decision(message, decision, *, edited=False, aggregate=False):
    text = message.text or message.caption
    from_user_id = message.from_user.id if message.from_user else None
    from_user_username = message.from_user.username if message.from_user and message.from_user.username else ""
    user_id_safe = from_user_id if from_user_id is not None else 0

    async def review_notification(admin_id, text):
        if decision.action == "moderate":
            return await review_service.send(admin_id, message.chat.id, message.message_id, message.text or message.caption, decision.to_record(),
                                             chat_title=message.chat.title, subject=restriction_subject(message))
        if decision.action == "delete" and text.startswith("🗑️ УДАЛЕН СПАМ"):
            return await review_service.send(admin_id, message.chat.id, message.message_id, text, decision.to_record(), kind="deleted", chat_title=message.chat.title)
        return await bot.send_message(admin_id, text)

    # Telegram actions, journal writes and notifications have separate outcomes.
    result = await execute_actions(
        message=message, decision_record=decision,
        journal_decision=lambda record: record_decision(message, record, "filter"),
        admins=() if aggregate else review_service.reviewers_for(message.chat.id), send_message=review_notification,
        write_action=lambda action: log_action(
            DATASET_PATH, user_id_safe, from_user_username,
            message.chat.id, message.message_id, action, text,
        ),
        record_deletion=lambda: review_service.tickets.record_auto_delete(message.chat.id, message.message_id),
        check_permissions=check_bot_permissions, logger=action_logger,
        rejected_errors=(TelegramBadRequest, TelegramForbiddenError, TelegramNotFound),
        bad_request_error=TelegramBadRequest,
    )
    if not edited:
        candidate = spam_waves.recent.get((message.chat.id, message.message_id))
        if candidate and candidate.message is message:
            candidate.deletion = result.deletion
            if candidate.wave_id:
                try:
                    wave_reports.record_deletion(candidate)
                except Exception:
                    logger.exception("Failed to record wave deletion outcome")

    return result


@dp.edited_message(F.chat.id.in_(CHAT_IDS))
async def shadow_edited_message(message: types.Message):
    spam_waves.invalidate(message)
    review_service.tickets.invalidate(message.chat.id, message.message_id)
    await filter_message(message, edited=True)

if __name__ == "__main__":
    async def main():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(handle_asyncio_exception)
        logger.info("Starting bot...")
        logger.warning("DRY_RUN=%s: automatic deletion and moderation notifications %s",
                       DRY_RUN, "disabled" if DRY_RUN else "enabled")
        logger.info(f"Bot will filter chats: {CHAT_IDS}")
        logger.info(f"Admins: {ADMIN_IDS}")
        logger.info(f"Delete threshold: {SPAM_DELETE_THRESHOLD}")
        logger.info(f"Moderate threshold: {SPAM_MODERATE_THRESHOLD}")
        logger.warning("Antispam version=%s model_version=%s dry_run=%s", APP_VERSION, model.version, DRY_RUN)
        try:
            await dp.start_polling(bot)
        finally:
            await bot.session.close()

    asyncio.run(main())
