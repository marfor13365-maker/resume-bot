# unlock_handlers.py
# Хендлеры для @Rezumeizi_bot: разблокировка аккаунта / покупка доп.аккаунта Blizko.
# Требует unlock_pricing.py (положить рядом) и настроенный bot = telebot.TeleBot(TOKEN)
#
# Подключение в основном файле бота:
#   from unlock_handlers import register_unlock_handlers
#   register_unlock_handlers(bot)

import telebot
from telebot import types
from unlock_pricing import get_next_price, has_pending, record_purchase, mark_paid

PROVIDER_TOKEN = "PASTE_PAYMENT_PROVIDER_TOKEN"  # ЮKassa/CloudPayments/Stars и т.д.
CURRENCY = "RUB"

# device_id -> telegram_user_id держим только в rам памяти на время диалога,
# сама заявка (с device_id) уже сохраняется в БД через record_purchase()
_pending_context = {}  # telegram_user_id -> {"kind": ..., "device_id": ...}


def register_unlock_handlers(bot: telebot.TeleBot):

    @bot.message_handler(commands=['start'])
    def handle_start(message):
        args = message.text.split(maxsplit=1)
        param = args[1].strip() if len(args) > 1 else ""

        if param.startswith("unlock_"):
            device_id = param[len("unlock_"):]
            _start_flow(bot, message, kind="unlock", device_id=device_id)
        elif param.startswith("extra_"):
            device_id = param[len("extra_"):]
            _start_flow(bot, message, kind="extra_account", device_id=device_id)
        else:
            bot.send_message(message.chat.id, "Привет! Чем помочь?")

    @bot.message_handler(func=lambda m: m.from_user.id in _pending_context
                          and _pending_context[m.from_user.id].get("awaiting_email"))
    def handle_email_input(message):
        ctx = _pending_context[message.from_user.id]
        email = message.text.strip()
        ctx["email"] = email
        ctx["awaiting_email"] = False
        _show_price_and_pay_button(bot, message, ctx)

    @bot.pre_checkout_query_handler(func=lambda q: True)
    def handle_pre_checkout(pre_checkout_query):
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

    @bot.message_handler(content_types=['successful_payment'])
    def handle_successful_payment(message):
        user_id = message.from_user.id
        ctx = _pending_context.get(user_id)
        if not ctx or "purchase_id" not in ctx:
            bot.send_message(message.chat.id, "Не нашёл заявку на оплату. Напиши в поддержку.")
            return

        code = mark_paid(ctx["purchase_id"])
        bot.send_message(
            message.chat.id,
            f"Оплата получена ✅\n\nТвой код доступа:\n`{code}`\n\n"
            f"Введи его на сайте Blizko в поле «Код доступа» — на том же устройстве, "
            f"с которого начинал.",
            parse_mode="Markdown"
        )
        _pending_context.pop(user_id, None)


def _start_flow(bot, message, kind, device_id):
    user_id = message.from_user.id

    if has_pending(user_id, kind):
        bot.send_message(
            message.chat.id,
            "У тебя уже есть неоплаченная заявка этого типа. "
            "Оплати её или подожди — новую пока создать нельзя."
        )
        return

    ctx = {"kind": kind, "device_id": device_id}
    _pending_context[user_id] = ctx

    if kind == "unlock":
        ctx["awaiting_email"] = True
        bot.send_message(message.chat.id, "Введи email, привязанный к аккаунту на Blizko:")
    else:
        _show_price_and_pay_button(bot, message, ctx)


def _show_price_and_pay_button(bot, message, ctx):
    user_id = message.from_user.id
    kind = ctx["kind"]
    price = get_next_price(user_id, kind)  # прогрессия 2 -> 4 -> 8 -> 16...

    purchase_id = record_purchase(
        telegram_user_id=user_id,
        device_id=ctx["device_id"],
        kind=kind,
        price=price,
        email=ctx.get("email"),
    )
    ctx["purchase_id"] = purchase_id

    title = "Разблокировка аккаунта Blizko" if kind == "unlock" else "Дополнительный аккаунт Blizko"
    prices = [types.LabeledPrice(label=title, amount=price * 100)]  # amount в копейках

    bot.send_invoice(
        chat_id=message.chat.id,
        title=title,
        description=f"Стоимость: {price} ₽",
        invoice_payload=f"unlock_purchase_{purchase_id}",
        provider_token=PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=prices,
        start_parameter="blizko_pay",
    )
