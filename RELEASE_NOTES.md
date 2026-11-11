# Антиспам 1.0.4

Спам-волна — серия сообщений, которая почти одновременно появляется в нескольких группах. Например, в разных чатах рассылают ложное объявление о закрытии и переезде. Когда бот видит один и тот же известный маркер в двух группах за 15 секунд, он собирает совпадения в одну карточку.

1. **Карточка спам-волны.** До 20 сообщений с известным маркером из разных групп и ссылки на оригиналы. Новые совпадения дополняют карточку.
2. **Удаление волны.** Одним подтверждением можно удалить сообщения из показанного списка.
3. **Ручная проверка после удаления.** Итог показывает причины отказов и ссылки, если группа поддерживает прямые ссылки.
4. **Модераторы по Telegram ID.** Обычная модерация ограничена назначенными группами. Если волна задела одну из них, модератор может подтвердить удаление всей серии. Администратор получит отчёт по группам.
5. **Карточки по новым сигналам.** Прямые запросы на сделки с USDT и предложения удалённой работы с переходом в личку могут вызвать карточку с кнопками.
6. **Скрытые Telegram-ссылки.** Некоторые объявления о переносе группы обнаруживаются и после правки.
7. **Команда `/spam`.** Ответом на сообщение сохраняет отметку и сразу запрашивает удаление оригинала.
8. **Аналитика.** `/review_stats` показывает итоги по людям и сигналам, а `/moderation_log [ID]` — последние решения. Отметки и подтверждённые удаления учитываются отдельно.

Пробный режим включён по умолчанию. Дополнительные сигналы и волны включаются настройкой `EXPERIMENTAL_REVIEW_ALERTS=true` в активном режиме. Тестирование проводилось.

[Как добавить модератора](https://github.com/antonnvmail-sketch/telegram-antispam/blob/main/README.md#как-добавить-модератора) · [Команды модерации и отчётов](https://github.com/antonnvmail-sketch/telegram-antispam/blob/main/README.md#команды-модерации-и-отчётов)

## Antispam 1.0.4

A spam wave is a series of messages that appear almost simultaneously in several groups. For example, several chats may receive a false notice about closing and moving. When the bot sees the same known marker in two groups within 15 seconds, it collects the matches in one card.

1. **Wave card.** One card links to up to 20 messages with a known marker across chats. Later matches update the same card.
2. **Wave deletion.** Confirm the displayed list to request deletion of its messages.
3. **Manual follow-up.** The result explains failures and includes source links where supported.
4. **Moderators by Telegram ID.** Routine moderation is limited to assigned chats. If a wave reaches one of those chats, the moderator can confirm deletion of the whole wave. Administrators receive per-chat results.
5. **New review signals.** Direct USDT trades and remote work offers that ask readers to move to private messages can trigger a review card with action buttons.
6. **Hidden Telegram links.** Selected group migration notices can be detected after an edit.
7. **`/spam` command.** Reply to a message to record a spam label and immediately request deletion.
8. **Analytics.** `/review_stats` shows counts by person and signal. `/moderation_log [ID]` shows recent decisions. Labels and confirmed deletions are counted separately.

Dry-run mode is enabled by default. To enable supplemental alerts and wave actions in active mode, set `EXPERIMENTAL_REVIEW_ALERTS=true`. Testing was conducted.

[Add a moderator](https://github.com/antonnvmail-sketch/telegram-antispam/blob/main/README.en.md#add-a-moderator) · [Commands](https://github.com/antonnvmail-sketch/telegram-antispam/blob/main/README.en.md#commands)

## Антиспам 1.0.3

Управлять модерацией теперь можно прямо из личных уведомлений бота.

- **Кнопки администратора:** «Спам — удалить», «Не спам — оставить» и «Не спам — ошибка удаления» после удаления. Ответы сохраняются отдельно; автоматического обучения по нажатиям нет.
- **Удобные карточки:** название группы, переход к исходному сообщению, компактный текст, результат действия и понятные объяснения ошибок.
- **Защита действий:** привязка кнопок к получателю и исходному сообщению, срок 24 часа, блокировка повторных действий и сокращение последовательных повторных уведомлений.
- **Новые команды:** `/review`, `/stat`, `/stat_export`, `/review_stats` и обновлённая `/help`.
- **Статистика:** текущий и два предыдущих календарных месяца по МСК; счётчики действий и ответов, без заявления общей точности модели. CSV содержит только агрегаты без текстов и идентификаторов.
- **Надёжность:** защита от второго экземпляра, необязательный автозапуск и контроль зависания, проверка связи с Telegram и прав удаления с уведомлениями об изменениях состояния.

Пробный режим включён по умолчанию; действия по кнопкам в нём отключены. Отметка ошибки не восстанавливает удалённое сообщение. Классификация и пороги сохранены. Рабочая модель и данные автора не распространяются. Тестирование проводилось.
