"""Review signals and the opt-in direct USDT trade deletion rule."""

from dataclasses import replace
import re
import unicodedata


# Restrict confusables and separators to the currency token.
COIN_SEPARATOR = r"[ ._·‐‑–—-]{0,2}"
COIN_FIRST = r"[uυю]"
COIN_SECOND = r"[sѕсcз]"
COIN_THIRD = r"[dдԁlл]"
COIN_LAST = r"[tтτ]"
COIN_STANDARD = COIN_SEPARATOR.join((COIN_FIRST, COIN_SECOND, COIN_THIRD, COIN_LAST))
COIN_TRANSPOSED = COIN_SEPARATOR.join((COIN_FIRST, COIN_SECOND, COIN_LAST, COIN_THIRD))
COIN_WORD = rf"(?:{COIN_STANDARD}|{COIN_TRANSPOSED}|tether|тезер)"
COIN = re.compile(rf"\b{COIN_WORD}\b")
GREETING = (
    r"(?:(?:привет|всем привет|здравствуйте|добрый день|доброе утро|добрый вечер)"
    r"(?:,?\s+чат)?[!,. ]+)?"
)
TRADE_MODAL = r"(?:может|сможет|хочет|готов)"
TRADE_REQUEST = (
    rf"(?:есть кто(?:-то)?(?:\s+{TRADE_MODAL})?|"
    rf"кто(?:-то|-нибудь|\s+здесь|\s+тут|\s+в чате)?\s+{TRADE_MODAL}|"
    r"может,?\s+кто(?:-то|-нибудь)?|"
    r"(?:есть кто в чате )?у кого можно)"
)
TRADE_VERB = r"(?:купить|продать|обменять|приобрести)"
TRADE_OFFER = r"(?:куплю|продам|продаю|обменяю|покупаю|меняю)"
AMOUNT = r"\d+(?: \d{3})*(?:[.,]\d+)?(?:[kк]| тыс\.?)?"
TRADE_OBJECT = rf"(?:{AMOUNT}\s*)?{COIN_WORD}(?:\s+{AMOUNT})?"
PAYMENT = r"(?:к[эе]ш|нал(?:ичные|ичку|ичными)?|рубли|руб(?:лей|ля)?\.?|₽)"
TRADE_TERMS = (
    rf"(?:за\s+{PAYMENT}|{PAYMENT}|по\s+курсу\s+{AMOUNT}(?:\s*₽)?|"
    r"курс\s+(?:обсудим|договорной)|(?:пишите|напишите|стучите)\s+в\s+(?:лс|личку|личные сообщения)|"
    r"в\s+(?:лс|личку)|срочно|сегодня)"
)
DIRECT_USDT_TRADE = re.compile(
    rf"{GREETING}(?:срочно[ ,:]+)?(?:я\s+)?(?:{TRADE_REQUEST}\s+(?:{TRADE_VERB}\s+{TRADE_OBJECT}|"
    rf"{TRADE_OBJECT}\s+{TRADE_VERB})|(?:{TRADE_OFFER}|(?:могу|готова?)\s+{TRADE_VERB})\s+{TRADE_OBJECT}|"
    rf"{TRADE_OBJECT}\s+{TRADE_OFFER}|кто\s+меняет\s+{TRADE_OBJECT}|"
    rf"ищу\s+человека\s*,?\s+который\s+(?:продаст|купит|обменяет)\s+{TRADE_OBJECT}|"
    rf"(?:кто(?:-нибудь|\s+(?:здесь|тут|в чате))?\s+(?:купит|продаст|продает|обменяет)|"
    rf"у\s+кого\s+есть|ищу\s+(?:покупателя|продавца)(?:\s+на)?|нуж(?:ен|ны|но))\s+{TRADE_OBJECT}|"
    rf"{TRADE_OBJECT}\s+(?:кто\s+(?:купит|продаст|меняет)|{TRADE_REQUEST}\s+{TRADE_VERB}))"
    rf"(?:(?:\s+|\s*[.!?,:;–—-]\s*){TRADE_TERMS})*\s*[.!?]*"
)
TRADE = re.compile(rf"\b(?:{TRADE_VERB}|{TRADE_OFFER})\b")
DIRECT_TRADE = re.compile(rf"^{GREETING}(?:{TRADE_REQUEST}|{TRADE_OFFER})\b")
INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))

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


def normalize_offer(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).replace("ё", "е")


def is_direct_trade(text):
    return bool(DIRECT_USDT_TRADE.fullmatch(normalize_offer(text.translate(INVISIBLE))))


def apply_direct_trade_rule(decision, text):
    if not is_direct_trade(text):
        return decision
    return direct_trade_decision(decision)


def direct_trade_decision(decision):
    return replace(
        decision, action="delete", reason="direct_usdt_trade_rule",
        policy_version=f"{decision.policy_version}+direct-usdt-v4",
    )


def review_reason(text):
    normalized = normalize_offer(text)
    if is_direct_trade(text) or (
        COIN.search(normalized) and TRADE.search(normalized) and DIRECT_TRADE.search(normalized)
    ):
        return "direct_usdt_trade"
    if (
        WORK_OFFER.search(normalized)
        and REMOTE.search(normalized)
        and PRIVATE_CONTACT.search(normalized)
        and not WORK_DISCUSSION.search(normalized)
    ):
        return "remote_work_private_contact"
    return None
