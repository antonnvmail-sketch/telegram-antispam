"""Offline review signals for direct offers in group messages."""

import re
import unicodedata


COIN = re.compile(r"\b(?:usdt|юсдт|юстд)\b")
TRADE = re.compile(r"\b(?:купить|куплю|продать|продам|продаю|обменять|обменяю)\b")
DIRECT_TRADE = re.compile(
    r"^(?:(?:привет|всем привет)[!,. ]+)?"
    r"(?:есть кто\b|кто\s+(?:может|хочет|готов)\b|"
    r"(?:куплю|продам|продаю|обменяю)\b)"
)
WORK_OFFER = re.compile(
    r"\b(?:предложение по работе|предлагаю работу|есть работа|есть подработка|"
    r"предлагаю подработку)\b"
)
REMOTE = re.compile(r"\b(?:из дома|удаленно|удаленная работа|на удаленке)\b")
PRIVATE_CONTACT = re.compile(
    r"\b(?:расскажу|подробности|пишите|напишите)\b[^.!?]{0,50}"
    r"\b(?:в личке|в личку|в личные сообщения|в лс)\b"
)
WORK_DISCUSSION = re.compile(
    r"\b(?:осторожно|мошенники|предупреждение|обсуждали|цитата)\b"
)


def review_reason(text):
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    normalized = normalized.replace("ё", "е")
    if COIN.search(normalized) and TRADE.search(normalized) and DIRECT_TRADE.search(normalized):
        return "direct_usdt_trade"
    if (
        WORK_OFFER.search(normalized)
        and REMOTE.search(normalized)
        and PRIVATE_CONTACT.search(normalized)
        and not WORK_DISCUSSION.search(normalized)
    ):
        return "remote_work_private_contact"
    return None
