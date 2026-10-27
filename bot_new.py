from monitor_sc_antispam import record_batch as monitor_record_batch
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound
from config import TELEGRAM_TOKEN, ADMIN_IDS, CHAT_IDS, DATASET_PATH, MODEL_PATH, SPAM_DELETE_THRESHOLD, SPAM_MODERATE_THRESHOLD, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, SHADOW_MODE_ENABLED, SHADOW_MODEL_PATH, SHADOW_MODEL_VERSION, SHADOW_DB_PATH, SHADOW_DB_TIMEOUT_SECONDS
from database import init_db, add_message, get_all_messages, log_action
from ml import train_model, load_model, predict, evaluate_model
from moderation import is_admin
from shadow import ShadowRuntime, admin_command_allowed
import os
import io
from import_dataset import import_txt_to_db
from config import DRY_RUN, validate_runtime

validate_runtime()

# Логирование в файл и stdout
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(level=logging.WARNING, handlers=[file_handler])

# Дополнительный уровень логирования для aiogram (диагностика)
aiogram_logger = logging.getLogger("aiogram")
aiogram_logger.setLevel(logging.WARNING)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

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
model = load_model(MODEL_PATH)

shadow_runtime = ShadowRuntime.load(
    enabled=SHADOW_MODE_ENABLED,
    model_path=SHADOW_MODEL_PATH,
    model_version=SHADOW_MODEL_VERSION,
    database_path=SHADOW_DB_PATH,
    database_timeout=SHADOW_DB_TIMEOUT_SECONDS,
)

async def observe_shadow(message: types.Message, *, text: str, source_kind: str, production_label=None, production_p_spam=None, production_action="production_skipped", production_skipped=True):
    user_id = message.from_user.id if message.from_user else 0
    username = message.from_user.username if message.from_user and message.from_user.username else ""
    content_type = message.content_type.value if hasattr(message.content_type, "value") else str(message.content_type)
    await shadow_runtime.observe(
        chat_id=message.chat.id, user_id=user_id, username=username, message_id=message.message_id,
        message_type=content_type, source_kind=source_kind, text=text, has_caption=bool(message.caption),
        production_label=production_label, production_p_spam=production_p_spam,
        production_action=production_action, production_skipped=production_skipped,
    )

class ShadowPassiveCoverageMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        if isinstance(event, types.Message) and event.chat.id in CHAT_IDS:
            if event.text and event.text.startswith('/'):
                await observe_shadow(event, text=event.text, source_kind="slash_message")
            elif event.caption:
                await observe_shadow(event, text=event.caption, source_kind="caption")
        return result

dp.message.outer_middleware(ShadowPassiveCoverageMiddleware())

@dp.message(Command("shadow_status"))
async def cmd_shadow_status(message: types.Message):
    user_id = message.from_user.id if message.from_user else None
    if not admin_command_allowed(user_id, ADMIN_IDS):
        await message.reply("⛔ Только для администраторов.")
        return
    stats = await shadow_runtime.stats()
    await message.reply(
        f"Shadow enabled: {shadow_runtime.enabled}\n"
        f"Model version: {shadow_runtime.model_version}\n"
        f"Loaded: {shadow_runtime.loaded}\n"
        f"Predictions: {stats.get('total', 0)}\n"
        f"Disagreements: {stats.get('disagree', 0)}\n"
        f"Errors: {stats.get('stored_errors', 0) + stats.get('db_errors', 0)}"
    )

@dp.message(Command("shadow_stats"))
async def cmd_shadow_stats(message: types.Message):
    user_id = message.from_user.id if message.from_user else None
    if not admin_command_allowed(user_id, ADMIN_IDS):
        await message.reply("⛔ Только для администраторов.")
        return
    stats = await shadow_runtime.stats()
    await message.reply(
        f"Total: {stats.get('total', 0)}\nAgree: {stats.get('agree', 0)}\n"
        f"Disagree: {stats.get('disagree', 0)}\n"
        f"Prod spam / shadow ham: {stats.get('prod_spam_shadow_ham', 0)}\n"
        f"Prod ham / shadow spam: {stats.get('prod_ham_shadow_spam', 0)}"
    )

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

    status_info = []
    status_info.append(f"🧪 Пробный режим (DRY_RUN): {DRY_RUN}")

    # Проверка модели
    if model:
        status_info.append("✅ Модель загружена")
    else:
        status_info.append("❌ Модель не загружена")

    # Проверка датасета
    data = get_all_messages(DATASET_PATH)
    status_info.append(f"📊 В датасете: {len(data)} сообщений")

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

    text = message.text[len("/test_spam"):].strip()
    if not text:
        await message.reply("Используйте: /test_spam <текст для проверки>")
        return

    label, confidence = predict(model, text)

    response = f"""
🔍 Результат анализа:
📝 Текст: "{text}"
🏷️ Классификация: {label}
📊 Уверенность: {confidence:.3f}

Это проверка текста. Действия не выполнялись.
{'🧪 Включён пробный режим (DRY_RUN).' if DRY_RUN else ''}

{'🗑️ Рекомендация: удалить' if label == 'spam' and confidence > SPAM_DELETE_THRESHOLD else ''}
{'⚠️ Рекомендация: передать на модерацию' if label == 'spam' and SPAM_MODERATE_THRESHOLD < confidence <= SPAM_DELETE_THRESHOLD else ''}
{'✅ НЕ СПАМ' if label == 'ham' or confidence <= SPAM_MODERATE_THRESHOLD else ''}
    """

    await message.reply(response)

@dp.message(Command("add_spam"))
async def cmd_add_spam(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только администраторы могут дообучать бота.")
        return
    text = message.text[len("/add_spam"):].strip()
    if not text:
        await message.reply("Используйте: /add_spam <сообщение>")
        return
    add_message(DATASET_PATH, "spam", text)
    await message.reply("✅ Сообщение добавлено в датасет как spam. Для применения обучите модель командой /train.")

@dp.message(Command("add_ham"))
async def cmd_add_ham(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.reply("⛔ Только администраторы могут дообучать бота.")
        return
    text = message.text[len("/add_ham"):].strip()
    if not text:
        await message.reply("Используйте: /add_ham <сообщение>")
        return
    add_message(DATASET_PATH, "ham", text)
    await message.reply("✅ Сообщение добавлено в датасет как ham. Для применения обучите модель командой /train.")

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
    model = load_model(MODEL_PATH)
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
async def filter_message(message: types.Message):
    # Детальное логирование начала обработки
    logger.info(f"Processing message from chat {message.chat.id}, user {message.from_user.id if message.from_user else 'None'}")

    if not model:
        logger.warning("Model not loaded, skipping message filtering")
        return

    if not message.text or not message.text.strip():
        logger.debug("Message has no text, skipping")
        return

    from_user_id = message.from_user.id if message.from_user else None
    from_user_username = message.from_user.username if message.from_user and message.from_user.username else ""

    # Проверяем, что это не команда бота
    if message.text.startswith('/'):
        logger.debug("Message is a command, skipping spam check")
        return

    # Классификация сообщения
    try:
        label, confidence = predict(model, message.text)
        production_p_spam = confidence if label == "spam" else 1.0 - confidence
        production_action = "allow"
        logger.info(f"Message classified as '{label}' with confidence {confidence:.3f} from user {from_user_id}")
    except Exception as e:
        logger.error(f"Error during message classification: {e}")
        return

    user_id_safe = from_user_id if from_user_id is not None else 0

    # Логируем классификацию
    log_action(
        DATASET_PATH,
        user_id_safe,
        from_user_username,
        message.chat.id,
        message.message_id,
        f"classified_{label}",
        message.text
    )

    if DRY_RUN:
        action = "would_allow"
        if label == "spam" and confidence > SPAM_DELETE_THRESHOLD:
            action = "would_delete_spam"
        elif label == "spam" and confidence > SPAM_MODERATE_THRESHOLD:
            action = "would_notify_moderators"
        log_action(DATASET_PATH, user_id_safe, from_user_username, message.chat.id,
                   message.message_id, action, message.text)
        await observe_shadow(message, text=message.text, source_kind="text",
                             production_label=label, production_p_spam=production_p_spam,
                             production_action=action, production_skipped=False)
        return

    # Проверяем пороги и действуем
    if label == "spam" and confidence > SPAM_DELETE_THRESHOLD:
        production_action = "delete_attempted"
        logger.info(f"Spam detected with high confidence ({confidence:.3f} > {SPAM_DELETE_THRESHOLD}), attempting to delete message {message.message_id}")

        try:
            await message.delete()
            production_action = "deleted"
            logger.info(f"Successfully deleted spam message {message.message_id} from user {from_user_id}")

            log_action(
                DATASET_PATH,
                user_id_safe,
                from_user_username,
                message.chat.id,
                message.message_id,
                "deleted_spam",
                message.text
            )

            # Уведомляем админов об удалении
            monitor_succeeded = monitor_failed = False
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🗑️ УДАЛЕН СПАМ (confidence: {confidence:.3f}):\n\n{message.text}\n\nОт: @{from_user_username or from_user_id}\nЧат: {message.chat.id}"
                    )
                    monitor_succeeded = True
                except Exception as e:
                    monitor_failed = True
                    logger.warning(f"Failed to notify admin {admin_id} about deletion: {e}")
            monitor_record_batch(monitor_succeeded, monitor_failed)

        except TelegramBadRequest as e:
            logger.error(f"TelegramBadRequest when deleting message {message.message_id}: {e}")
            # Проверяем права бота
            perms = await check_bot_permissions(message.chat.id)
            if not perms['can_delete_messages']:
                logger.error(f"Bot does not have delete permissions in chat {message.chat.id}")
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"❌ НЕ МОГУ УДАЛИТЬ СПАМ - НЕТ ПРАВ!\n\nТекст: {message.text}\nОт: @{from_user_username or from_user_id}\nЧат: {message.chat.id}\n\nДайте боту права администратора!"
                        )
                    except Exception as notify_e:
                        logger.warning(f"Failed to notify admin {admin_id} about permission error: {notify_e}")
        except TelegramForbiddenError as e:
            logger.error(f"TelegramForbiddenError when deleting message {message.message_id}: {e}")
        except TelegramNotFound as e:
            logger.error(f"TelegramNotFound when deleting message {message.message_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error when deleting message {message.message_id}: {e}")

    elif label == "spam" and confidence > SPAM_MODERATE_THRESHOLD:
        production_action = "moderation_notification_attempted"
        logger.info(f"Spam detected for moderation ({confidence:.3f} > {SPAM_MODERATE_THRESHOLD}), notifying admins")

        # Пересылаем на модерацию
        monitor_succeeded = monitor_failed = False
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ ПОДОЗРЕНИЕ НА СПАМ (confidence: {confidence:.3f}):\n\n{message.text}\n\nОт: @{from_user_username or from_user_id}\nЧат: {message.chat.id}"
                )
                monitor_succeeded = True
            except Exception as e:
                monitor_failed = True
                logger.warning(f"Failed to notify admin {admin_id} about potential spam: {e}")
        monitor_record_batch(monitor_succeeded, monitor_failed)
    else:
        logger.debug(f"Message classified as '{label}' with confidence {confidence:.3f} - no action needed")

    await observe_shadow(
        message, text=message.text, source_kind="text", production_label=label,
        production_p_spam=production_p_spam, production_action=production_action, production_skipped=False,
    )

@dp.edited_message(F.chat.id.in_(CHAT_IDS))
async def shadow_edited_message(message: types.Message):
    text = message.text or message.caption
    if text and text.strip():
        await observe_shadow(message, text=text, source_kind="edited_message")

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
        logger.warning(f"Shadow enabled={shadow_runtime.enabled}, loaded={shadow_runtime.loaded}, version={shadow_runtime.model_version}")
        try:
            await dp.start_polling(bot)
        finally:
            await bot.session.close()

    asyncio.run(main())
