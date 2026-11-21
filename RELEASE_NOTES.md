# Антиспам 1.0.4

## Что нового в 1.0.5

1. **Межгрупповые спам-волны.** Бот связывает от двух подозрительных сообщений в разных подключённых группах при сходстве текста или подписи от 90% за 60 секунд. Отправители могут быть разными.
2. **Немедленное автоудаление.** При `DRY_RUN=false` бот сразу запрашивает удаление совпадений с собственным сигналом спама и продолжает наблюдать до 30 секунд тишины.
3. **Повторные нарушители.** После двух разных сообщений одного автора, вручную подтверждённых как спам в одной супергруппе, бот запрещает ему писать на 72 часа. Боту нужны права ограничивать участников.
4. **Единый отчёт.** Каждый настроенный администратор получает одну обновляемую карточку с обнаружениями, удалениями, группами, ограничениями, длительностью, типом сигнала и долей подтверждённых удалений.
5. **Исправление ошибок.** `/wave_ham ID_волны ID_группы ID_сообщения` отмечает ложное срабатывание, а `/wave_release ID_группы ID_участника` снимает действующее ограничение этого бота. Исходные решения остаются в истории.
6. **История волн для AST Console.** AST Console — будущий Android-инструмент для администраторов. Есть ранний прототип, полноценное приложение остаётся долгосрочным планом без объявленных сроков. v1.0.5 сохраняет историю волн и готовит статистику для будущей интеграции. Локальная JSON-выгрузка уже доступна. Серверный API для волн и экран статистики пока только запланированы.

**Также в v1.0.5 вошли два исправления, ранее установленные на рабочем боте**

- **Защита сообщений администраторов и владельцев.** Перед автоудалением бот проверяет роль автора. Публикации от имени чата, включая анонимные, и сообщения с неподтверждённой ролью пропускаются.
- **Расширенный фильтр прямых сделок с USDT.** Бот распознаёт больше формулировок покупки, продажи и обмена, включая варианты `ustd` и «юсдт». Автоудаление по этому правилу включается отдельно через `AUTO_DELETE_DIRECT_USDT=true`.

Пробный режим включён по умолчанию. Локальная статистика по указанным группам:

```shell
python wave_statistics.py --db dataset.sqlite.admin-review.sqlite --group=-1001234567890
```

## New in 1.0.5

1. **Cross-group spam waves.** The bot links at least two independently suspicious posts in different connected groups when their text or captions match by 90% or more within 60 seconds. The senders may differ.
2. **Immediate automatic deletion.** With `DRY_RUN=false`, the bot requests deletion as soon as a match is confirmed and keeps tracking until 30 seconds of silence.
3. **Repeat offenders.** Two distinct posts from the same user manually confirmed as spam in one supergroup trigger a 72-hour posting restriction. The bot needs permission to restrict members.
4. **One editable wave report.** Each configured administrator receives a single report showing detections, deletions, affected groups, restrictions, duration, signal type, and the confirmed deletion rate.
5. **Moderation corrections.** In a private chat, `/wave_ham WAVE_ID GROUP_ID MESSAGE_ID` marks a false positive and `/wave_release GROUP_ID USER_ID` lifts an active restriction imposed by the bot. Original decisions remain in the audit trail.
6. **Wave history for AST Console.** AST Console is a planned Android tool for administrators. An early prototype exists, and the full app remains a long-term goal with no announced timeline. The bot now stores wave history and provides a local JSON export. The server endpoint and the wave screen are planned.

**v1.0.5 also includes two fixes previously deployed to the production bot**

- **Administrator and owner protection.** Automatic deletion checks the sender’s group role. Posts sent as a chat, including anonymous posts, and posts from unverified roles are exempt.
- **Expanded direct USDT trade filter.** The bot recognizes more buy, sell, and exchange offers, including ticker variants such as `ustd` and «юсдт». Enable automatic actions for this rule with `AUTO_DELETE_DIRECT_USDT=true`.

Dry-run mode is enabled by default. Export wave statistics for an authorized group:

```shell
python wave_statistics.py --db dataset.sqlite.admin-review.sqlite --group=-1001234567890
```

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
