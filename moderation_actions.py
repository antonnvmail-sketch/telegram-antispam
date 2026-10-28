"""Moderation lifecycle with injected Telegram and journal adapters."""
import asyncio
from dataclasses import dataclass, field
import json


@dataclass
class ActionResult:
    decision: str
    deletion: str = 'not_attempted'
    notification: str = 'not_attempted'
    journal_failures: list = field(default_factory=list)
    dry_run: bool = False

    @property
    def outcome_action(self):
        if self.dry_run:
            return {'delete':'would_delete_spam','moderate':'would_notify_moderators',
                    'allow':'would_allow'}[self.decision]
        if self.decision == 'delete':
            return {'deleted': 'deleted', 'rejected': 'delete_rejected',
                    'unknown': 'delete_unknown'}.get(self.deletion, 'delete_attempted')
        if self.decision == 'moderate':
            return 'moderation_notification_' + self.notification
        return 'allow'


async def execute_actions(*, message, decision_record, journal_decision,
                          admins, send_message, write_action,
                          check_permissions, logger,
                          rejected_errors, bad_request_error):
    """Preserve filtering thresholds; never retry an uncertain Telegram action."""
    decision = decision_record.action
    label, confidence = decision_record.label, decision_record.confidence
    result = ActionResult(decision, dry_run=decision_record.dry_run)
    context = {**decision_record.to_record(), 'chat_id': message.chat.id, 'message_id': message.message_id,
               'decision': decision}
    journal_unavailable = not journal_decision(decision_record)
    if journal_unavailable:
        result.journal_failures.append('decision_record')

    def event(stage, status, action, **details):
        nonlocal journal_unavailable
        logger.info(json.dumps({**context, 'stage': stage, 'status': status, 'event_action': action,
                                **details}, sort_keys=True))
        if journal_unavailable:
            return  # Keep file events; avoid repeating DB timeouts for this message.
        try:
            write_action(action)
        except Exception as exc:
            journal_unavailable = True
            result.journal_failures.append(action)
            # Do not attribute a failed journal write to the Telegram operation.
            logger.error(json.dumps({**context, 'stage': 'journal', 'status': 'failed',
                'action': action, 'error_type': type(exc).__name__}, sort_keys=True))

    async def notify(text):
        sent = failed = unknown = 0
        for admin_id in admins:
            event('notification', 'attempted', 'notification_attempted')
            try:
                await send_message(admin_id, text)
            except asyncio.CancelledError:
                event('notification', 'unknown', 'notification_unknown',
                      error_type='CancelledError')
                raise
            except rejected_errors as exc:
                failed += 1
                event('notification', 'rejected', 'notification_rejected',
                      error_type=type(exc).__name__)
            except Exception as exc:
                # A timeout does not prove the message was not delivered.
                unknown += 1
                event('notification', 'unknown', 'notification_unknown',
                      error_type=type(exc).__name__)
            else:
                sent += 1
                event('notification', 'sent', 'notification_sent')
        result.notification = ('skipped' if not admins else
            'partial' if sent and (failed or unknown) else
            'sent' if sent else 'unknown' if unknown else 'failed')
        event('notification_batch', result.notification,
              'notification_batch_' + result.notification,
              sent=sent, rejected=failed, unknown=unknown)
    event('decision', 'selected', 'decision_' + decision)
    event('classification', 'completed', 'classified_' + label)
    if result.dry_run:
        event('execution', 'dry_run', result.outcome_action)
        return result
    user = message.from_user
    sender = user.username if user and user.username else user.id if user else None
    suffix = f'\n\n{message.text}\n\nОт: @{sender}\nЧат: {message.chat.id}'
    if decision == 'delete':
        event('deletion', 'attempted', 'delete_attempted')
        bad_request = False
        try:
            response = await message.delete()
        except asyncio.CancelledError:
            result.deletion = 'unknown'
            event('deletion', 'unknown', 'delete_unknown', error_type='CancelledError')
            raise
        except rejected_errors as exc:
            result.deletion = 'rejected'
            bad_request = isinstance(exc, bad_request_error)
            event('deletion', 'rejected', 'delete_rejected', error_type=type(exc).__name__)
        except Exception as exc:
            result.deletion = 'unknown'
            event('deletion', 'unknown', 'delete_unknown', error_type=type(exc).__name__)
        else:
            result.deletion = 'deleted' if response is True else 'unknown'
            event('deletion', result.deletion,
                  'deleted_spam' if result.deletion == 'deleted' else 'delete_unknown')

        # Notifications are outside the deletion try: errors cannot undo its result.
        if result.deletion == 'deleted':
            await notify(f'🗑️ УДАЛЕН СПАМ (confidence: {confidence:.3f}):' + suffix)
        elif bad_request:
            try:
                perms = await check_permissions(message.chat.id)
            except Exception as exc:
                perms = {'error': type(exc).__name__}
            if 'error' in perms:
                event('permissions', 'unknown', 'permissions_unknown')
            elif perms.get('can_delete_messages') is False:
                await notify('❌ НЕ МОГУ УДАЛИТЬ СПАМ - НЕТ ПРАВ!' + suffix
                             + '\n\nДайте боту права администратора!')
    elif decision == 'moderate':
        await notify(f'⚠️ ПОДОЗРЕНИЕ НА СПАМ (confidence: {confidence:.3f}):' + suffix)
    return result
