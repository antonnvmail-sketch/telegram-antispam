# Telegram Antispam · JUST MADE

[Русский](README.md) · **English**

A self-hosted Telegram bot that helps group administrators moderate text messages. It uses TF-IDF and logistic regression to classify messages as spam or legitimate conversation (`ham`). In active mode, it deletes messages classified as spam above the deletion threshold and sends suspicious messages to administrators.

**Version: 1.0.3 · License: MIT.**

[Download a release](https://github.com/antonnvmail-sketch/telegram-antispam/releases) · [Project website (Russian)](https://justmade.su/antispam/) · [Changelog (Russian)](CHANGELOG.md) · [Report an issue](https://github.com/antonnvmail-sketch/telegram-antispam/issues)

## Before you start

The public repository includes synthetic demonstration messages, not the author's production model or private conversations. Train your own model on examples relevant to your community and review its decisions before enabling automatic deletion. False positives and missed spam are possible.

**English moderation quality has not been evaluated.** The bot can accept English text, but it does not translate messages or understand unfamiliar words through a language model. Its predictions depend on the vocabulary learned from training examples. The supplied demonstration data is in Russian; it is not a ready-to-use English spam filter. For an English-speaking or multilingual group, provide representative spam and legitimate messages in the languages used there, then evaluate on separate examples.

This document explains installation in English. The bot's current replies and help messages are primarily in Russian.

## New in 1.0.3

- Private administrator cards with Delete spam, Keep, and Report mistaken deletion buttons. Use `/review` as a reply in a configured group to request a card.
- Group title, a source-message link where supported, and clear action outcomes and errors. Buttons expire after 24 hours and are bound to their recipient, notification and source message. Repeated actions are blocked.
- Feedback and original evidence are stored separately from training data. No automatic retraining is performed; reporting an error does not restore a deleted message.
- `/stat` covers the current and two previous calendar months in Moscow time. It counts automatic deletions, confirmed button deletions, mistaken-deletion reports and dismissed suspicions. Partial periods and unavailable history are explicit; these are action counters, not model accuracy.
- `/stat_export` exports aggregate CSV without message text or identifiers; `/review_stats` counts administrator answers. Help is updated.
- Single-instance protection and optional systemd restart/watchdog and health checks. Administrators receive state-change alerts; a quiet group is not treated as an outage.

`DRY_RUN=true` disables action cards and all review-button actions, including old cards. Classification policy and thresholds are unchanged. Store the separate `<DATASET_PATH>.admin-review.sqlite` evidence database and `.operations/` state privately.

## Optional service setup

On a host with user systemd, run `python setup_service.py` to preview the units and `python setup_service.py --write` to create them. Existing units are never overwritten. Stop any manually started instance before `systemctl --user daemon-reload` and `systemctl --user enable --now antispam.service antispam-health.timer`. An administrator must enable linger for the selected user if startup without login is required. See the [Russian service instructions](README.md#автозапуск-и-контроль-состояния-необязательно) for operation and service limits. Neither a server restart nor a Telegram outage guarantees that an alert can be delivered through the same bot.

## Features and limits

- Text filtering in configured Telegram groups; slash commands are skipped.
- Administrator commands to add labeled examples, retrain the model, inspect decisions, and view statistics.
- Local import of labeled training data and evaluation on a separate dataset.
- `DRY_RUN=true` by default: proposed moderation actions are recorded locally, without automatic deletion or moderator notifications. Bot commands can still reply and change training data.

Automatic moderation processes `message.text` only. Images, video, voice messages, media captions, and edited messages are outside this filter. The classifier does not use the parent post's context. Uploading a training dataset as a Telegram document is not implemented.

## Install and prepare a demo

You need Python 3.12. Run these commands from the project directory. Use `python3.12` instead of `python` when needed to select that version.

```shell
git clone https://github.com/antonnvmail-sketch/telegram-antispam.git
cd telegram-antispam
python -m venv .venv
```

Activate the environment in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or in a POSIX shell:

```shell
source .venv/bin/activate
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of `python` without changing the system execution policy.

```shell
python -m pip install -r requirements.txt
python -m pip check
python prepare_demo.py
```

`requirements.txt` uses `requirements.lock.txt` as installation constraints. Demo preparation reads `data.example.txt`, creates `dataset.sqlite` and `model.joblib`, and trains a model. It needs no Telegram token and does not contact Telegram. Existing output files are not overwritten. To repeat preparation, choose new paths:

```shell
python prepare_demo.py --db another-demo.sqlite --model another-demo.joblib
```

This demonstrates training, not moderation accuracy. Load only trusted joblib files: deserializing a model can execute Python code.

## Connect your own Telegram bot

1. Create a bot through BotFather and a separate test group. Do not reuse the token of an already running bot.
2. Copy `.env.example` to `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

   In a POSIX shell, use `cp .env.example .env`.

3. Set `TELEGRAM_TOKEN`, `ADMIN_IDS`, and `CHAT_IDS` to your own values. IDs are comma-separated numbers, not `@usernames`. Group IDs may be negative. `/here` shows the current chat ID once the bot is running.
4. Add the bot to the test group and configure its group-message access and permissions in BotFather and the group settings. Automatic deletion requires administrator permission to delete messages. Administrators should start a private conversation with the bot so it can send them notifications.
5. Keep `DRY_RUN=true` and leave other optional settings at their defaults.
6. Start a single instance:

   ```shell
   python bot_new.py
   ```

The bot validates configuration and checks for a local model before creating working files. An unfilled `.env.example` is not a working configuration.

Use `/status`, `/check_permissions`, and `/test_spam your message` to inspect setup and predictions. Check representative messages and mistakes in your test group. Only after reviewing the results should you consider setting `DRY_RUN=false` and restarting. Stop the bot with Ctrl+C.

## Main settings

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_TOKEN` | Your secret bot token; store it only in your local `.env` |
| `ADMIN_IDS` | Users allowed to run training and diagnostic commands |
| `CHAT_IDS` | Chats monitored by the text filter |
| `DATASET_PATH` / `MODEL_PATH` | Local SQLite database and trusted model file |
| `DRY_RUN` | Defaults to `true`; records proposed moderation actions |
| `SPAM_DELETE_THRESHOLD` | Deletion threshold; defaults to `0.7` |
| `SPAM_MODERATE_THRESHOLD` | Moderator notification threshold; defaults to `0.4` |
| `LOG_FILE`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` | Local log rotation settings |

An action requires the predicted label to be `spam` and its confidence to be **strictly greater** than the corresponding threshold. Deletion takes priority over notification. A moderation threshold of `0.4` does not mean that every message with P(spam) above 0.4 triggers a notification: `spam` must first be the winning label. Scores are not guaranteed to be calibrated probabilities.

## New in 1.0.3

After `/add_spam` and `/add_ham`, and in `/status`, the bot reports how many added examples are not included in the loaded model. Imports and duplicate additions count too. The count survives a restart and returns to zero after successful training on the current dataset. Older models show an unknown count until successfully retrained with this version. This reminder is not a measure of model quality; manual edits or deletions can require retraining even when the count is zero.

A newly trained model replaces the previous file only after saving completes. A failed write preserves the old file. This release also adds this English guide and simplifies the standalone bot.

## Train for your community

Administrators can add examples with `/add_spam message` and `/add_ham message`. Run `/train` to train on the database's current `messages` table and reload the model. Training needs both classes and enough recurring features. It runs synchronously and may delay bot responses on large datasets.

For local import, stop the bot and prepare a UTF-8 file with one labeled message per line:

```text
spam Your labeled spam example
ham Your labeled legitimate message
```

```shell
python import_dataset.py --data my-data.txt --db dataset.sqlite
```

Import appends rows; importing the same file again duplicates examples. Retrain after changing the database. The `/import_data` command reads a local `data.txt` file, which is not supplied.

Use messages you have permission to process. Human moderators should assign labels according to the group's rules; the bot's own predictions are not ground truth.

## Commands

| Command | Purpose |
| --- | --- |
| `/start`, `/help` | Show help |
| `/here` | Show the current chat ID |
| `/status`, `/check_permissions` | Inspect configuration, versions, and permissions |
| `/test_spam message` | Show a prediction and recommended action without deleting or notifying |
| `/add_spam message`, `/add_ham message` | Add a labeled training example |
| `/train` | Train and reload the model |
| `/stats` | Show statistics |
| `/import_data` | Import the local `data.txt` file |
| `/evaluate` | Evaluate against the training database; not an independent quality measurement |

Training, statistics, and diagnostic commands require a configured administrator ID.

## Evaluate on separate messages

Keep evaluation messages separate from training data, including closely related templates. Prepare a labeled UTF-8 file containing both classes, then run:

```shell
python evaluate_holdout.py --model trusted-model.joblib --training-data training.txt --data holdout.txt
```

Supply the actual training examples for that model in `training.txt`. This command does not train or contact Telegram. It rejects normalized exact overlap with the supplied training file and duplicates in the evaluation file, but cannot verify the model's true training history or exclude every similar template.

The JSON report includes classification errors and proposed moderation outcomes. Deletion precision is `null` when no deletions are proposed. Results describe only the supplied dataset; synthetic examples do not establish accuracy in real groups.

## Updates and rollback

Release 1.0.1 separates classification decisions, deletion results, database recording, and notifications. The filter and `/test_spam` share decision logic. `/status` reports bot and model versions. A network timeout is recorded as an unknown outcome, without automatic retries.

Before updating, stop your instance and privately back up the source files, configuration, model, and a consistent SQLite database. Use SQLite Online Backup for an active database.

Install a consistent 1.0.1 source bundle while preserving your own `.env`, model, and data. Migration 002 creates the decision table and index without rewriting historical tables. Start in dry-run mode and inspect status, predictions, and records.

To roll back, stop the bot and restore a consistent previous set of source files. **Do not overwrite fresh message history with an old database merely to roll back code.** The new decision table can remain. Verify configuration and run only one instance. Compatible Python and dependencies are also required.

## Data and verification

The training database and legacy logs can contain message text and identifiers. Decision records contain metadata and text hashes. Error logs may also contain message data. Do not publish tokens, conversations, training datasets, databases, models, logs, or backups. Check staged changes before committing; `.gitignore` cannot identify every secret or custom filename.

Testing was conducted before release. Automated results are available in [GitHub Actions](https://github.com/antonnvmail-sketch/telegram-antispam/actions). This does not establish independent moderation accuracy or English-language performance.

## Contributing

Report bugs through Issues or suggest changes through Pull Requests. Do not attach private messages, tokens, databases, or models trained on other people's conversations. Further accuracy improvements need separate data and evaluation; no release schedule is promised.

Author: [Anton · JUST MADE](https://github.com/antonnvmail-sketch). Licensed under [MIT](LICENSE).
