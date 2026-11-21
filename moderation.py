from config import ADMIN_IDS

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def can_moderate_sender(bot, message, logger):
    """Require a known non-admin sender before automatic moderation."""
    # Telegram supplies a placeholder user for messages sent as a chat.
    if message.sender_chat is not None or message.from_user is None:
        return False

    try:
        member = await bot.get_chat_member(
            message.chat.id, message.from_user.id, request_timeout=3,
        )
    except Exception as exc:
        logger.warning("Sender role lookup failed in chat %s: %s",
                       message.chat.id, type(exc).__name__)
        return False

    return member.status in {"member", "restricted", "left", "kicked"}
