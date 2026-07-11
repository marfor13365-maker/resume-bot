import os
import re
import logging
import uuid
import json
import time
import threading
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request
import telebot
from groq import Groq
import psycopg2
from psycopg2.extras import RealDictCursor

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@rezumeizi")

MERCHANT_ID = os.getenv("MERCHANT_ID")
API_SECRET = os.getenv("API_SECRET")
PLATIGA_API_URL = "https://app.platega.io/transaction/process"
PLATIGA_LK_URL = "https://platega.io/"

# Blizko: интеграция с vector-chat-api (доп.аккаунты)
BLIZKO_API_URL = os.getenv("BLIZKO_API_URL", "https://vector-chat-api.onrender.com")
BLIZKO_API_KEY = os.getenv("BLIZKO_API_KEY", "")

# Схема Postgres для resume-bot (изолирована от таблиц Blizko в том же Supabase-проекте)
DB_SCHEMA = os.getenv("DB_SCHEMA", "resume_bot")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
groq_client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-08-15-17"
TERMS_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-08-15-10"
SUPPORT_EMAIL = "marfor13365@gmail.com"

user_states = {}
user_data = {}
user_menu_msg = {}

# Достаём id заявки из вставленной пользователем ссылки (или сырого id)
REQUEST_ID_RE = re.compile(r"e_([a-zA-Z0-9\-]+)")

def extract_request_id(pasted_text):
    m = REQUEST_ID_RE.search(pasted_text.strip())
    if m:
        return m.group(1)
    cleaned = pasted_text.strip()
    if re.fullmatch(r"[a-zA-Z0-9\-]{8,}", cleaned):
        return cleaned
    return None

# ========== ПЕРЕВОДЫ ==========
T = {
    "ru": {
        "choose_lang": "🌍 Выберите язык / Choose language:",
        "welcome": "👋 Привет!\n\nЯ адаптирую резюме под вакансию и оптимизирую под ATS-проверку.\n\nПримите условия использования:",
        "agreed": "✅ Условия приняты!",
        "main_menu": "🏠 Главное меню:",
        "sub_active": "✅ Подписка резюме-адаптер активна до: {date}",
        "sub_free": "✅ Доступ открыт (бесплатно)",
        "sub_none": "❌ Подписки резюме-адаптер нет\n\nЦена: {price}₽ / {days} дней",
        "need_sub": "🔒 Нужна подписка резюме-адаптер.\n\nЦена: {price}₽ / {days} дней\n\nДля оплаты: 📧 {email}",
        "btn_optimize": "🚀 Оптимизировать резюме",
        "btn_my_sub": "📄 Подписка резюме-адаптер",
        "btn_info": "ℹ️ Информация",
        "btn_support": "🆘 Поддержка",
        "btn_back": "◀️ Назад",
        "btn_back_menu": "◀️ Назад в меню",
        "btn_back_resume": "◀️ Ввести резюме заново",
        "btn_again": "🔄 Оптимизировать ещё раз",
        "btn_home": "🏠 Главное меню",
        "btn_policy": "📄 Политика",
        "btn_terms": "📋 Соглашение",
        "btn_agree": "✅ Принимаю условия",
        "btn_write_support": "✉️ Написать вопрос",
        "info_text": "ℹ️ Информация\n\n🤖 Бот оптимизации резюме\nАдаптирует резюме под вакансию с учётом ATS.\n\n📢 Наш канал: @rezumeizi",
        "support_text": "🆘 Поддержка\n\n📧 {email}\n\nНапишите вопрос прямо здесь:",
        "write_support": "✉️ Напишите ваш вопрос:",
        "support_sent": "✅ Вопрос отправлен! Ответим на {email}",
        "step1": "📄 Шаг 1 из 2 — Резюме\n\nОтправь резюме текстом или .txt файлом:",
        "step2": "✅ Резюме получено!\n\n📋 Шаг 2 из 2 — Вакансия\n\nТеперь вставь текст вакансии:",
        "processing": "⏳ Оптимизирую резюме...",
        "result_title": "✅ Готово!\n\n",
        "result_next": "💡 Что дальше?",
        "need_agree": "⚠️ Сначала примите условия.",
        "too_short_resume": "⚠️ Текст слишком короткий.",
        "too_short_vacancy": "⚠️ Текст вакансии слишком короткий.",
        "no_links": "🔗 Ссылки не поддерживаются. Скопируй текст вакансии.",
        "only_txt": "⚠️ Только .txt. Скопируй текст и отправь как сообщение.",
        "error": "❌ Ошибка. Попробуй ещё раз.",
        "lang_changed": "✅ Язык: Русский",
        "payment_success": "✅ Оплата прошла успешно!\nПодписка резюме-адаптер активна до {date}.",
        "btn_vpn": "🔐 VPN без ограничений",
        "vpn_menu": "🌐 *VPN без ограничений*\n\n{description}\n\n💰 Цена: {price}₽ / месяц\n\n{status}",
        "vpn_active": "✅ Ваш VPN активен до {date}\n🔑 Ключ:\n`{key}`",
        "vpn_inactive": "❌ У вас нет активного VPN.",
        "btn_pay_resume": "💳 Оплатить резюме адаптер",
        "btn_pay_vpn": "💳 Оплатить VPN",
        "btn_vpn_instruction": "📖 Инструкция",
        "vpn_paid_success": "✅ Оплата VPN получена!\n\n{instruction}",
        "vpn_no_keys": "⚠️ К сожалению, все ключи временно закончились. Обратитесь к администратору.",
        "btn_blizko_extra": "💳 Оплатить доп. аккаунт в Blizko",
        "btn_rules": "📜 Правила",
        "blizko_ask_link": "Вставь сюда ссылку, которую тебе показал сайт Blizko:",
        "blizko_link_invalid": "Не распознал ссылку. Проверь, что скопировал её полностью с сайта Blizko, и вставь ещё раз.",
        "blizko_request_gone": "⚠️ Заявка не найдена или уже обработана. Получи новую ссылку на сайте Blizko.",
        "blizko_terms": "📜 *Правила*\n\nАдаптация резюме под вакансию — сервис этого бота. Приложение Blizko (сайт: https://marfor13365-maker.github.io/Znakomstva/) — отдельный сервис, оплата дополнительного аккаунта на устройстве проходит здесь. Оплата не подлежит возврату после выдачи кода доступа. Код одноразовый и действителен только для устройства, с которого была создана заявка.",
    },
    "en": {
        "choose_lang": "🌍 Выберите язык / Choose language:",
        "welcome": "👋 Hello!\n\nI adapt resumes for vacancies and optimize for ATS.\n\nPlease accept the terms:",
        "agreed": "✅ Terms accepted!",
        "main_menu": "🏠 Main menu:",
        "sub_active": "✅ Resume adapter subscription active until: {date}",
        "sub_free": "✅ Access is free",
        "sub_none": "❌ No resume adapter subscription\n\nPrice: {price}₽ / {days} days",
        "need_sub": "🔒 Resume adapter subscription required.\n\nPrice: {price}₽ / {days} days\n\nTo pay: 📧 {email}",
        "btn_optimize": "🚀 Optimize resume",
        "btn_my_sub": "📄 Resume adapter subscription",
        "btn_info": "ℹ️ Information",
        "btn_support": "🆘 Support",
        "btn_back": "◀️ Back",
        "btn_back_menu": "◀️ Back to menu",
        "btn_back_resume": "◀️ Re-enter resume",
        "btn_again": "🔄 Optimize again",
        "btn_home": "🏠 Main menu",
        "btn_policy": "📄 Privacy Policy",
        "btn_terms": "📋 Terms",
        "btn_agree": "✅ I accept",
        "btn_write_support": "✉️ Write question",
        "info_text": "ℹ️ Information\n\n🤖 Resume Optimization Bot\nAdapts resumes for vacancies with ATS.\n\n📢 Our channel: @rezumeizi",
        "support_text": "🆘 Support\n\n📧 {email}\n\nWrite your question here:",
        "write_support": "✉️ Write your question:",
        "support_sent": "✅ Sent! We'll reply to {email}",
        "step1": "📄 Step 1 of 2 — Resume\n\nSend resume as text or .txt file:",
        "step2": "✅ Resume received!\n\n📋 Step 2 of 2 — Vacancy\n\nPaste vacancy text:",
        "processing": "⏳ Optimizing resume...",
        "result_title": "✅ Done!\n\n",
        "result_next": "💡 What next?",
        "need_agree": "⚠️ Accept terms first.",
        "too_short_resume": "⚠️ Text too short.",
        "too_short_vacancy": "⚠️ Vacancy text too short.",
        "no_links": "🔗 Links not supported. Copy vacancy text.",
        "only_txt": "⚠️ Only .txt. Copy text and send as message.",
        "error": "❌ Error. Try again.",
        "lang_changed": "✅ Language: English",
        "payment_success": "✅ Payment successful!\nResume adapter subscription active until {date}.",
        "btn_vpn": "🔐 Unlimited VPN",
        "vpn_menu": "🌐 *Unlimited VPN*\n\n{description}\n\n💰 Price: {price}₽ / month\n\n{status}",
        "vpn_active": "✅ Your VPN is active until {date}\n🔑 Key:\n`{key}`",
        "vpn_inactive": "❌ You don't have an active VPN.",
        "btn_pay_resume": "💳 Pay for resume adapter",
        "btn_pay_vpn": "💳 Pay for VPN",
        "btn_vpn_instruction": "📖 Instructions",
        "vpn_paid_success": "✅ VPN payment received!\n\n{instruction}",
        "vpn_no_keys": "⚠️ Sorry, all keys are temporarily sold out. Contact administrator.",
        "btn_blizko_extra": "💳 Pay for extra Blizko account",
        "btn_rules": "📜 Rules",
        "blizko_ask_link": "Paste the link the Blizko site showed you:",
        "blizko_link_invalid": "Couldn't recognize that link. Make sure you copied it fully from Blizko, then paste it again.",
        "blizko_request_gone": "⚠️ Request not found or already processed. Get a new link from the Blizko site.",
        "blizko_terms": "📜 *Rules*\n\nResume adaptation is this bot's own service. The Blizko app (site: https://marfor13365-maker.github.io/Znakomstva/) is a separate service; payment for an extra account on a device happens here. Payment is non-refundable after the access code is issued. The code is single-use and only valid for the device the request was created from.",
    }
}

SYSTEM_PROMPT = {
    "ru": "Ты эксперт по оптимизации резюме. Кратко и чётко адаптируй резюме под вакансию: добавь ключевые слова, оптимизируй под ATS. Сохрани реальные данные. В конце 2-3 строки: процент соответствия и главное что изменено.",
    "en": "You are a resume expert. Briefly adapt the resume for the vacancy: add keywords, optimize for ATS. Keep real data. End with 2-3 lines: match % and key changes."
}

def get_conn():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=10)
    with conn.cursor() as c:
        c.execute(f"SET search_path TO {DB_SCHEMA}, public")
    conn.commit()
    return conn

def get_conn_with_retry(retries=5, delay=3):
    for attempt in range(1, retries + 1):
        try:
            conn = get_conn()
            logger.info(f"Подключение к БД успешно (попытка {attempt})")
            return conn
        except Exception as e:
            logger.warning(f"Ошибка подключения (попытка {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(delay)
            else:
                raise

def init_database():
    conn = get_conn_with_retry(retries=5, delay=3)
    c = conn.cursor()

    try:
        c.execute(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}")
        conn.commit()
        logger.info(f"Схема {DB_SCHEMA} проверена/создана")
    except Exception as e:
        logger.error(f"Ошибка создания схемы {DB_SCHEMA}: {e}")
        conn.rollback()

    tables = {
        "users": """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                agreed BOOLEAN DEFAULT FALSE,
                lang TEXT DEFAULT 'ru',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """,
        "settings": "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
        "tickets": """
            CREATE TABLE IF NOT EXISTS tickets (
                user_id BIGINT PRIMARY KEY,
                message TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """,
        "payments": """
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                order_id TEXT,
                amount INTEGER,
                status TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """,
        "poster_state": "CREATE TABLE IF NOT EXISTS poster_state (key VARCHAR(50) PRIMARY KEY, value INTEGER)",
        "vpn_keys": """
            CREATE TABLE IF NOT EXISTS vpn_keys (
                id SERIAL PRIMARY KEY,
                key_text TEXT UNIQUE NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                used_by BIGINT,
                used_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """,
        "vpn_purchases": """
            CREATE TABLE IF NOT EXISTS vpn_purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                key_id INTEGER REFERENCES vpn_keys(id),
                purchased_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE
            )
        """
    }
    for name, sql in tables.items():
        try:
            c.execute(sql)
            logger.info(f"Таблица {name} проверена/создана")
        except Exception as e:
            logger.error(f"Ошибка создания таблицы {name}: {e}")
            conn.rollback()

    c.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='sub_start') THEN
                ALTER TABLE users ADD COLUMN sub_start TIMESTAMP WITH TIME ZONE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='sub_end') THEN
                ALTER TABLE users ADD COLUMN sub_end TIMESTAMP WITH TIME ZONE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='is_subscribed') THEN
                ALTER TABLE users ADD COLUMN is_subscribed BOOLEAN DEFAULT FALSE;
            END IF;
        END $$;
    """)
    logger.info("Колонки подписки проверены/добавлены")

    init_data = [
        ("INSERT INTO poster_state (key, value) VALUES ('topic_index', 0) ON CONFLICT (key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('price','100') ON CONFLICT(key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('subscription_days','30') ON CONFLICT(key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('ad_text','') ON CONFLICT(key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('ad_active','0') ON CONFLICT(key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('vpn_price','300') ON CONFLICT(key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('vpn_description','🔐 Анонимный и быстрый VPN без ограничений трафика и скорости. Подходит для любых устройств.') ON CONFLICT(key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('vpn_instruction','📱 Инструкция по подключению VPN через Happ:\n\n1️⃣ Скачайте приложение:\n• Android (Google Play): https://play.google.com/store/apps/details?id=com.happproxy\n• Android (RuStore): https://apps.rustore.ru/app/com.happproxy\n• iOS: https://apps.apple.com/ru/app/happ-proxy-utility/id6504287215\n\n2️⃣ Скопируйте ключ: {key}\n\n3️⃣ Откройте Happ → кнопка «+» → «Из буфера» → нажмите на сервер.\n\n✅ Готово!') ON CONFLICT(key) DO NOTHING", None),
        ("INSERT INTO settings(key,value) VALUES('blizko_extra_base_price','200') ON CONFLICT(key) DO NOTHING", None),
    ]
    for sql, params in init_data:
        try:
            c.execute(sql, params)
        except Exception as e:
            logger.error(f"Ошибка вставки начальных данных: {e}")
            conn.rollback()

    conn.commit()
    conn.close()
    logger.info("✅ Инициализация базы данных успешно завершена")

def get_user(uid):
    conn = get_conn_with_retry()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM users WHERE user_id = %s", (uid,))
    row = c.fetchone()
    conn.close()
    return row

def upsert_user(uid, agreed=None, lang=None, sub_end=None, sub_start=None, is_subscribed=None):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT(user_id) DO NOTHING", (uid,))
    updates = []
    params = []
    if agreed is not None:
        updates.append("agreed = %s")
        params.append(agreed)
    if lang is not None:
        updates.append("lang = %s")
        params.append(lang)
    if sub_end is not None:
        updates.append("sub_end = %s")
        params.append(sub_end)
    if sub_start is not None:
        updates.append("sub_start = %s")
        params.append(sub_start)
    if is_subscribed is not None:
        updates.append("is_subscribed = %s")
        params.append(is_subscribed)
    if updates:
        query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = %s"
        params.append(uid)
        c.execute(query, params)
    conn.commit()
    conn.close()

def get_setting(key):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=%s", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_setting(key, value):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=%s",
        (key, str(value), str(value))
    )
    conn.commit()
    conn.close()

def save_ticket(uid, message):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute(
        "INSERT INTO tickets(user_id,message) VALUES(%s,%s) ON CONFLICT(user_id) DO UPDATE SET message=%s,created_at=NOW()",
        (uid, message, message)
    )
    conn.commit()
    conn.close()

def get_tickets():
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT user_id, message FROM tickets ORDER BY created_at DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_ticket(uid):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("DELETE FROM tickets WHERE user_id=%s", (uid,))
    conn.commit()
    conn.close()

def count_users():
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    n = c.fetchone()[0]
    conn.close()
    return n

def count_tickets():
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tickets")
    n = c.fetchone()[0]
    conn.close()
    return n

def get_all_users():
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

def has_access(uid):
    try:
        if get_setting("price") == "0":
            return True
        user = get_user(uid)
        if not user:
            return False
        sub_end = user.get("sub_end")
        if not sub_end:
            return False
        return sub_end > datetime.now(timezone.utc)
    except Exception as e:
        logger.error(f"Ошибка has_access: {e}")
        return False

def sub_status_text(uid):
    price = get_setting("price")
    days = get_setting("subscription_days")
    if price == "0":
        return t(uid, "sub_free")
    user = get_user(uid)
    if user:
        sub_end = user.get("sub_end")
        if sub_end and sub_end > datetime.now(timezone.utc):
            date_str = sub_end.strftime("%d.%m.%Y")
            return t(uid, "sub_active", date=date_str)
    return t(uid, "sub_none", price=price, days=days)

def activate_subscription(user_id: int, days: int = None):
    if days is None:
        days = int(get_setting("subscription_days") or 30)
    now = datetime.now(timezone.utc)
    sub_end = now + timedelta(days=days)
    upsert_user(user_id, sub_start=now, sub_end=sub_end, is_subscribed=True)
    logger.info(f"✅ Подписка (резюме) активирована для {user_id} до {sub_end}")
    return sub_end

def get_vpn_price():
    return int(get_setting("vpn_price") or 300)

def get_vpn_description():
    return get_setting("vpn_description") or "Анонимный и быстрый VPN без ограничений."

def get_vpn_instruction():
    return get_setting("vpn_instruction") or "Инструкция по VPN не задана."

def get_active_vpn_purchase(user_id):
    conn = get_conn_with_retry()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("""
        SELECT vp.*, vk.key_text 
        FROM vpn_purchases vp
        JOIN vpn_keys vk ON vp.key_id = vk.id
        WHERE vp.user_id = %s AND vp.is_active = TRUE AND vp.expires_at > NOW()
        ORDER BY vp.purchased_at DESC LIMIT 1
    """, (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def has_active_vpn(user_id):
    purchase = get_active_vpn_purchase(user_id)
    return purchase is not None

def get_free_vpn_key():
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT id, key_text FROM vpn_keys WHERE used = FALSE ORDER BY id LIMIT 1 FOR UPDATE")
    row = c.fetchone()
    if row:
        key_id, key_text = row
        c.execute("UPDATE vpn_keys SET used = TRUE WHERE id = %s", (key_id,))
        conn.commit()
        conn.close()
        return key_id, key_text
    conn.close()
    return None, None

def activate_vpn(user_id, key_id):
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("""
        INSERT INTO vpn_purchases (user_id, key_id, expires_at)
        VALUES (%s, %s, %s)
    """, (user_id, key_id, expires_at))
    conn.commit()
    conn.close()
    logger.info(f"✅ VPN активирован для {user_id} до {expires_at}")

def deactivate_old_vpn(user_id):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("UPDATE vpn_purchases SET is_active = FALSE WHERE user_id = %s AND is_active = TRUE", (user_id,))
    conn.commit()
    conn.close()

def add_vpn_key(key_text):
    conn = get_conn_with_retry()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO vpn_keys (key_text) VALUES (%s) ON CONFLICT (key_text) DO NOTHING", (key_text,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления ключа: {e}")
        return False
    finally:
        conn.close()

def get_all_vpn_keys():
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT id, key_text, used, used_by FROM vpn_keys ORDER BY id")
    rows = c.fetchall()
    conn.close()
    return rows

def get_vpn_stats():
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM vpn_keys WHERE used = FALSE")
    free = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM vpn_keys WHERE used = TRUE")
    used = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM vpn_purchases WHERE is_active = TRUE AND expires_at > NOW()")
    active_subs = c.fetchone()[0]
    conn.close()
    return free, used, active_subs

def get_stats():
    conn = get_conn_with_retry()
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_subscribed = TRUE AND sub_end > NOW()")
        active_subs = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE sub_start >= DATE_TRUNC('day', NOW())")
        today_subs = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_subscribed = TRUE")
        total_subs = c.fetchone()[0]
    except Exception as e:
        logger.error(f"Ошибка в get_stats: {e}")
        total_users = active_subs = today_subs = total_subs = 0
    finally:
        conn.close()
    return total_users, active_subs, today_subs, total_subs

def get_users_list(offset=0, limit=20):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("""
        SELECT user_id, sub_end
        FROM users
        ORDER BY user_id
        LIMIT %s OFFSET %s
    """, (limit, offset))
    rows = c.fetchall()
    conn.close()
    return rows

def load_topic_index():
    try:
        conn = get_conn_with_retry()
        c = conn.cursor()
        c.execute("SELECT value FROM poster_state WHERE key = 'topic_index'")
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        logger.error(f"Ошибка load_topic_index: {e}")
        return 0

def save_topic_index(index):
    conn = get_conn_with_retry()
    c = conn.cursor()
    c.execute("UPDATE poster_state SET value = %s WHERE key = 'topic_index'", (index,))
    conn.commit()
    conn.close()

TOPICS_RU = [
    "5 ошибок в резюме которые отсеивают ATS-системы",
    "Как правильно описать опыт работы чтобы пройти ATS",
    "Ключевые слова в резюме: как их найти и вставить",
    "Почему HR не видит твоё резюме и как это исправить",
    "Формат резюме который принимают все ATS-системы",
    "Как адаптировать одно резюме под разные вакансии",
    "Раздел навыков в резюме: что писать и как оформить",
    "Сопроводительное письмо: нужно ли и как писать",
    "Как описать достижения в резюме с цифрами",
    "Топ-10 слов которые убивают твоё резюме",
    "Как пройти ATS если у тебя нет опыта работы",
    "Резюме для смены профессии: как составить",
    "Linkedin профиль vs резюме: в чём разница",
    "Как указать образование в резюме правильно",
    "Пробелы в карьере: как объяснить в резюме",
]

def generate_post(topic):
    prompt = f"""Напиши полезный пост для Telegram канала о резюме и карьере.
Тема: {topic}

Требования:
- 150-200 слов
- Живой разговорный стиль
- 3-5 конкретных советов
- В конце призыв подписаться на канал @rezumeizi (если уместно)
- Используй эмодзи
- Без хэштегов"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0.7
    )
    return response.choices[0].message.content

def send_post_to_telegram(text):
    bot.send_message(CHANNEL_ID, text)
    return True

def post_with_retry(topic, retries=3):
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"Генерация поста для темы: {topic}")
            post_text = generate_post(topic)
            send_post_to_telegram(post_text)
            logger.info("✅ Пост отправлен")
            return True
        except Exception as e:
            logger.error(f"Попытка {attempt} не удалась: {e}")
            if attempt < retries:
                time.sleep(10 * attempt)
    return False

def scheduled_job():
    topic_index = load_topic_index()
    topic = TOPICS_RU[topic_index % len(TOPICS_RU)]
    success = post_with_retry(topic)
    if success:
        save_topic_index(topic_index + 1)

def create_platiga_payment(user_id, amount, description, payment_method=11, order_id=None, service_type="subscription", extra_payload=None):
    if not order_id:
        order_id = f"{user_id}_{uuid.uuid4().hex[:8]}_{int(datetime.now().timestamp())}"
    bot_url = f"https://t.me/{(bot.get_me()).username}"
    payload_dict = {"user_id": user_id, "order_id": order_id, "type": service_type}
    if extra_payload:
        payload_dict.update(extra_payload)
    payload_data = json.dumps(payload_dict, ensure_ascii=False)
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook/platiga"
    payload = {
        "paymentMethod": payment_method,
        "paymentDetails": {"amount": amount, "currency": "RUB"},
        "description": description,
        "return": f"{bot_url}?start=payment_success_{order_id}",
        "failedUrl": f"{bot_url}?start=payment_fail_{order_id}",
        "payload": payload_data,
        "webhook_url": webhook_url
    }
    headers = {
        "X-MerchantId": MERCHANT_ID,
        "X-Secret": API_SECRET,
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(PLATIGA_API_URL, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("redirect")
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        return None

def blizko_mark_paid(request_id):
    try:
        r = requests.post(
            f"{BLIZKO_API_URL}/api/unlock/mark-paid",
            headers={"X-Api-Key": BLIZKO_API_KEY, "Content-Type": "application/json"},
            json={"request_id": request_id},
            timeout=15
        )
        if r.status_code == 200:
            return r.json().get("code")
        logger.error(f"blizko mark-paid error: {r.status_code} {r.text}")
        return None
    except Exception as e:
        logger.error(f"blizko_mark_paid exception: {e}")
        return None

def get_blizko_extra_base_price():
    return int(get_setting("blizko_extra_base_price") or 200)

def blizko_get_request_info(request_id):
    """Спрашивает у Render точную цену заявки (там уже посчитана прогрессия)."""
    try:
        r = requests.get(f"{BLIZKO_API_URL}/api/unlock/status/{request_id}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"blizko_get_request_info exception: {e}")
    return None

def blizko_offer_kb():
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton("💳 Оплатить", callback_data="blizko_pay"))
    return kb

def show_blizko_offer(cid, request_id):
    info = blizko_get_request_info(request_id)
    if not info:
        bot.send_message(cid, t(cid, "blizko_request_gone"))
        return

    price = info["price"]
    user_data.setdefault(cid, {})["blizko_request_id"] = request_id
    user_data[cid]["blizko_price"] = price

    text = (
        "➕ *Дополнительный аккаунт Blizko*\n\n"
        f"Стоимость: {price}₽\n\n"
        "После оплаты пришлю код — скопируй его и вставь на сайте Blizko, чтобы создать второй аккаунт на этом устройстве."
    )

    delete_prev_menu(cid)
    text = text + "\n\n" + t(cid, "blizko_terms")
    msg = bot.send_message(cid, text, reply_markup=blizko_offer_kb(), parse_mode="Markdown")
    user_menu_msg[cid] = msg.message_id

def t(uid, key, **kwargs):
    user = get_user(uid)
    lang = user["lang"] if user else "ru"
    text = T.get(lang, T["ru"]).get(key, key)
    return text.format(**kwargs) if kwargs else text

def get_lang(uid):
    user = get_user(uid)
    return user["lang"] if user else "ru"

def get_ad_footer():
    if get_setting("ad_active") == "1":
        ad = get_setting("ad_text")
        if ad:
            return f"\n\n━━━━━━━━━━━━━━━\n📢 {ad}"
    return ""

def delete_prev_menu(cid):
    if cid in user_menu_msg:
        try:
            bot.delete_message(cid, user_menu_msg[cid])
        except:
            pass
        del user_menu_msg[cid]

def send_menu(cid, text, kb):
    delete_prev_menu(cid)
    ad = get_ad_footer()
    msg = bot.send_message(cid, text + ad, reply_markup=kb, parse_mode="Markdown")
    user_menu_msg[cid] = msg.message_id
    return msg

def lang_kb():
    kb = telebot.types.InlineKeyboardMarkup()
    kb.row(
        telebot.types.InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
        telebot.types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    return kb

def agree_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_agree"), callback_data="agree"))
    kb.row(
        telebot.types.InlineKeyboardButton(t(uid, "btn_policy"), url=PRIVACY_URL),
        telebot.types.InlineKeyboardButton(t(uid, "btn_terms"), url=TERMS_URL)
    )
    return kb

def main_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    if has_access(uid):
        kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_optimize"), callback_data="start_flow"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_vpn"), callback_data="vpn_menu"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_my_sub"), callback_data="my_sub"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_blizko_extra"), callback_data="blizko_extra_start"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_info"), callback_data="info"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_rules"), callback_data="rules"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_support"), callback_data="support"))
    return kb

def info_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.row(
        telebot.types.InlineKeyboardButton(t(uid, "btn_policy"), url=PRIVACY_URL),
        telebot.types.InlineKeyboardButton(t(uid, "btn_terms"), url=TERMS_URL)
    )
    kb.add(telebot.types.InlineKeyboardButton("📢 Наш канал", url="https://t.me/rezumeizi"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_back"), callback_data="back_main"))
    return kb

def support_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_write_support"), callback_data="write_support"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_back"), callback_data="back_main"))
    return kb

def back_main_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_back_menu"), callback_data="back_main"))
    return kb

def back_resume_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_back_resume"), callback_data="start_flow"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_home"), callback_data="back_main"))
    return kb

def result_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_again"), callback_data="start_flow"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_home"), callback_data="back_main"))
    return kb

def vpn_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_pay_vpn"), callback_data="vpn_subscribe"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_vpn_instruction"), callback_data="vpn_show_instruction"))
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_back"), callback_data="back_main"))
    return kb

def admin_kb():
    price = get_setting("price")
    days = get_setting("subscription_days")
    ad_active = get_setting("ad_active") == "1"
    price_text = f"{price}₽" if price != "0" else "Бесплатно"
    vpn_price = get_vpn_price()
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton(f"💰 Цена резюме: {price_text}", callback_data="admin_price"))
    kb.add(telebot.types.InlineKeyboardButton(f"📅 Дней подписки: {days}", callback_data="admin_days"))
    kb.add(telebot.types.InlineKeyboardButton(f"🔐 Цена VPN: {vpn_price}₽/мес", callback_data="admin_vpn_price"))
    kb.add(telebot.types.InlineKeyboardButton(f"➕ Базовая цена доп.аккаунта Blizko: {get_blizko_extra_base_price()}₽", callback_data="admin_blizko_extra_price"))
    kb.add(telebot.types.InlineKeyboardButton(f"📝 Описание VPN", callback_data="admin_vpn_desc"))
    kb.add(telebot.types.InlineKeyboardButton(f"📖 Инструкция VPN", callback_data="admin_vpn_instruction"))
    kb.add(telebot.types.InlineKeyboardButton(f"🔑 Управление ключами VPN", callback_data="admin_vpn_keys"))
    kb.add(telebot.types.InlineKeyboardButton(f"📢 Реклама: {'✅ Вкл' if ad_active else '❌ Выкл'}", callback_data="admin_ad_toggle"))
    kb.add(telebot.types.InlineKeyboardButton("✏️ Текст рекламы", callback_data="admin_ad_text"))
    kb.add(telebot.types.InlineKeyboardButton("➕ Выдать подписку (резюме)", callback_data="admin_give_sub"))
    kb.add(telebot.types.InlineKeyboardButton("📢 Рассылка всем", callback_data="admin_broadcast"))
    kb.add(telebot.types.InlineKeyboardButton("📮 Опубликовать пост", callback_data="admin_post_now"))
    kb.add(telebot.types.InlineKeyboardButton("🎫 Обращения", callback_data="admin_tickets"))
    kb.add(telebot.types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"))
    kb.add(telebot.types.InlineKeyboardButton("🔗 ЛК Platiga", url=PLATIGA_LK_URL))
    kb.add(telebot.types.InlineKeyboardButton("🏠 Выйти из админки", callback_data="admin_exit"))
    return kb

def payment_methods_kb(uid):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("💳 Карты РФ", callback_data="pay_method_11"),
        telebot.types.InlineKeyboardButton("📱 СБП", callback_data="pay_method_2"),
        telebot.types.InlineKeyboardButton("🌍 Международные карты", callback_data="pay_method_12"),
        telebot.types.InlineKeyboardButton("🇧🇾 ЕРИП", callback_data="pay_method_3"),
        telebot.types.InlineKeyboardButton("₿ Криптовалюта", callback_data="pay_method_13")
    )
    kb.add(telebot.types.InlineKeyboardButton(t(uid, "btn_back"), callback_data="back_main"))
    return kb

@bot.message_handler(commands=["start"])
def start(message):
    cid = message.chat.id
    parts = message.text.split(maxsplit=1)
    param = parts[1].strip() if len(parts) > 1 else ""

    if param.startswith("e_"):
        request_id = param[2:]
        user_states[cid] = None
        upsert_user(cid)
        delete_prev_menu(cid)
        show_blizko_offer(cid, request_id)
        return

    user_states[cid] = None
    upsert_user(cid)
    delete_prev_menu(cid)
    msg = bot.send_message(cid, T["ru"]["choose_lang"], reply_markup=lang_kb())
    user_menu_msg[cid] = msg.message_id

@bot.message_handler(commands=["admin"])
def admin_cmd(message):
    if message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Нет доступа.")
        return
    _show_admin(message.chat.id)

def _show_admin(cid):
    delete_prev_menu(cid)
    price = get_setting("price")
    days = get_setting("subscription_days")
    ad_text = get_setting("ad_text") or "не задан"
    ad_active = get_setting("ad_active") == "1"
    topic_index = load_topic_index()
    next_topic = TOPICS_RU[topic_index % len(TOPICS_RU)]
    vpn_price = get_vpn_price()
    vpn_free, vpn_used, vpn_active_subs = get_vpn_stats()
    msg = bot.send_message(cid,
        f"⚙️ Админ панель\n\n"
        f"💰 Цена резюме: {price}₽\n"
        f"📅 Дней подписки: {days}\n"
        f"🔐 VPN цена: {vpn_price}₽/мес\n"
        f"🔑 VPN ключи: {vpn_free} свободно / {vpn_used} использовано\n"
        f"📡 Активных VPN: {vpn_active_subs}\n"
        f"➕ Базовая цена доп.аккаунта Blizko: {get_blizko_extra_base_price()}₽\n"
        f"📢 Реклама: {'✅ Вкл' if ad_active else '❌ Выкл'}\n"
        f"📝 Текст рекламы: {ad_text}\n\n"
        f"🎫 Обращений: {count_tickets()}\n"
        f"👥 Пользователей: {count_users()}\n\n"
        f"📌 Следующий пост ({topic_index + 1}/{len(TOPICS_RU)}):\n{next_topic}",
        reply_markup=admin_kb()
    )
    user_menu_msg[cid] = msg.message_id

@bot.callback_query_handler(func=lambda call: True)
def cb(call):
    cid = call.message.chat.id
    data = call.data

    if data in ("lang_ru", "lang_en"):
        lang = data.split("_")[1]
        upsert_user(cid, lang=lang)
        bot.answer_callback_query(call.id, T[lang]["lang_changed"])
        user = get_user(cid)
        if user and user["agreed"]:
            try:
                bot.edit_message_text(t(cid, "main_menu") + get_ad_footer(), cid, call.message.message_id, reply_markup=main_kb(cid))
            except:
                send_menu(cid, t(cid, "main_menu"), main_kb(cid))
        else:
            try:
                bot.edit_message_text(t(cid, "welcome") + get_ad_footer(), cid, call.message.message_id, reply_markup=agree_kb(cid))
            except:
                send_menu(cid, t(cid, "welcome"), agree_kb(cid))
    elif data == "agree":
        upsert_user(cid, agreed=True)
        try:
            bot.edit_message_text(t(cid, "main_menu") + get_ad_footer(), cid, call.message.message_id, reply_markup=main_kb(cid))
            user_menu_msg[cid] = call.message.message_id
        except:
            send_menu(cid, t(cid, "main_menu"), main_kb(cid))
    elif data == "back_main":
        user_states[cid] = None
        try:
            bot.edit_message_text(t(cid, "main_menu") + get_ad_footer(), cid, call.message.message_id, reply_markup=main_kb(cid))
            user_menu_msg[cid] = call.message.message_id
        except:
            send_menu(cid, t(cid, "main_menu"), main_kb(cid))
    elif data == "my_sub":
        status = sub_status_text(cid)
        kb = telebot.types.InlineKeyboardMarkup()
        if not has_access(cid) and get_setting("price") != "0":
            kb.add(telebot.types.InlineKeyboardButton(t(cid, "btn_pay_resume"), callback_data="pay_subscription"))
        kb.add(telebot.types.InlineKeyboardButton(t(cid, "btn_back"), callback_data="back_main"))
        try:
            bot.edit_message_text(status + get_ad_footer(), cid, call.message.message_id, reply_markup=kb)
        except:
            pass
    elif data == "pay_subscription":
        user = get_user(cid)
        if not user or not user["agreed"]:
            bot.answer_callback_query(call.id, t(cid, "need_agree"))
            return
        if has_access(cid):
            bot.answer_callback_query(call.id, "У вас уже есть активная подписка!")
            return
        if not MERCHANT_ID or not API_SECRET:
            bot.answer_callback_query(call.id, "Платёжная система временно недоступна.")
            return
        try:
            bot.edit_message_text("Выберите способ оплаты:", cid, call.message.message_id, reply_markup=payment_methods_kb(cid))
            user_states[cid] = "choosing_payment_method"
            user_data[cid] = {"service_type": "subscription", "amount": int(get_setting("price")), "description": f"Подписка на {get_setting('subscription_days')} дней"}
        except:
            pass
    elif data == "rules":
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton(t(cid, "btn_back"), callback_data="back_main"))
        try:
            bot.edit_message_text(t(cid, "blizko_terms"), cid, call.message.message_id, reply_markup=kb, parse_mode="Markdown")
        except:
            send_menu(cid, t(cid, "blizko_terms"), kb)
    elif data == "blizko_extra_start":
        user_states[cid] = "awaiting_blizko_link"
        try:
            bot.edit_message_text(t(cid, "blizko_ask_link"), cid, call.message.message_id, reply_markup=back_main_kb(cid))
            user_menu_msg[cid] = call.message.message_id
        except:
            send_menu(cid, t(cid, "blizko_ask_link"), back_main_kb(cid))
    elif data == "blizko_pay":
        if not MERCHANT_ID or not API_SECRET:
            bot.answer_callback_query(call.id, "Платёжная система временно недоступна.")
            return
        request_id = user_data.get(cid, {}).get("blizko_request_id")
        price = user_data.get(cid, {}).get("blizko_price")
        if not request_id or not price:
            bot.answer_callback_query(call.id, "Заявка не найдена, вставь ссылку заново.")
            return
        try:
            bot.edit_message_text("Выберите способ оплаты:", cid, call.message.message_id, reply_markup=payment_methods_kb(cid))
            user_states[cid] = "choosing_payment_method"
            user_data[cid].update({
                "service_type": "blizko_extra_account",
                "amount": price,
                "description": "Blizko: дополнительный аккаунт",
                "blizko_request_id": request_id
            })
        except:
            pass
    elif data.startswith("pay_method_"):
        method = int(data.split("_")[2])
        if user_states.get(cid) == "choosing_payment_method" and user_data.get(cid, {}).get("service_type"):
            service_type = user_data[cid]["service_type"]
            amount = user_data[cid]["amount"]
            description = user_data[cid]["description"]
            blizko_request_id = user_data[cid].get("blizko_request_id")
        else:
            service_type = "subscription"
            amount = int(get_setting("price"))
            description = f"Подписка на {get_setting('subscription_days')} дней"
            blizko_request_id = None

        extra_payload = {"request_id": blizko_request_id} if blizko_request_id else None
        payment_url = create_platiga_payment(cid, float(amount), description, payment_method=method, service_type=service_type, extra_payload=extra_payload)
        if payment_url:
            try:
                bot.edit_message_text(
                    f"💳 Для оплаты перейдите по ссылке:\n{payment_url}\n\nПосле оплаты услуга активируется автоматически.",
                    cid, call.message.message_id,
                    reply_markup=telebot.types.InlineKeyboardMarkup().add(
                        telebot.types.InlineKeyboardButton(t(cid, "btn_back"), callback_data="back_main")
                    )
                )
                user_states[cid] = None
                user_data[cid] = {}
            except:
                pass
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка создания платежа, попробуйте позже.")
    elif data == "info":
        try:
            bot.edit_message_text(t(cid, "info_text") + get_ad_footer(), cid, call.message.message_id, reply_markup=info_kb(cid))
        except:
            pass
    elif data == "support":
        try:
            bot.edit_message_text(t(cid, "support_text", email=SUPPORT_EMAIL) + get_ad_footer(), cid, call.message.message_id, reply_markup=support_kb(cid))
        except:
            pass
    elif data == "write_support":
        user_states[cid] = "writing_support"
        try:
            bot.edit_message_text(t(cid, "write_support") + get_ad_footer(), cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "vpn_menu":
        user = get_user(cid)
        if not user or not user["agreed"]:
            bot.answer_callback_query(call.id, t(cid, "need_agree"))
            return
        description = get_vpn_description()
        price = get_vpn_price()
        active = has_active_vpn(cid)
        if active:
            purchase = get_active_vpn_purchase(cid)
            expires = purchase["expires_at"].strftime("%d.%m.%Y")
            key = purchase["key_text"]
            status_text = t(cid, "vpn_active", date=expires, key=key)
        else:
            status_text = t(cid, "vpn_inactive")
        text = t(cid, "vpn_menu", description=description, price=price, status=status_text)
        try:
            bot.edit_message_text(text, cid, call.message.message_id, reply_markup=vpn_kb(cid), parse_mode="Markdown")
        except:
            send_menu(cid, text, vpn_kb(cid))
    elif data == "vpn_subscribe":
        user = get_user(cid)
        if not user or not user["agreed"]:
            bot.answer_callback_query(call.id, t(cid, "need_agree"))
            return
        if not MERCHANT_ID or not API_SECRET:
            bot.answer_callback_query(call.id, "Платёжная система временно недоступна.")
            return
        price = get_vpn_price()
        description = "VPN без ограничений на 30 дней"
        try:
            bot.edit_message_text("Выберите способ оплаты:", cid, call.message.message_id, reply_markup=payment_methods_kb(cid))
            user_states[cid] = "choosing_payment_method"
            user_data[cid] = {"service_type": "vpn", "amount": price, "description": description}
        except:
            pass
    elif data == "vpn_show_instruction":
        instruction = get_vpn_instruction()
        if "{key}" in instruction:
            instruction = instruction.replace("{key}", "после оплаты")
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton(t(cid, "btn_back"), callback_data="vpn_menu"))
        try:
            bot.edit_message_text(f"📖 *Инструкция по подключению VPN*\n\n{instruction}", cid, call.message.message_id, reply_markup=kb, parse_mode="Markdown")
        except:
            pass
    elif data == "admin_exit" and cid == ADMIN_ID:
        user_states[cid] = None
        try:
            bot.edit_message_text(t(cid, "main_menu") + get_ad_footer(), cid, call.message.message_id, reply_markup=main_kb(cid))
        except:
            send_menu(cid, t(cid, "main_menu"), main_kb(cid))
    elif data == "admin_price" and cid == ADMIN_ID:
        user_states[cid] = "admin_set_price"
        try:
            bot.edit_message_text(f"💰 Текущая цена (резюме): {get_setting('price')}₽\n\nВведите новую цену (0 = бесплатно):", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_days" and cid == ADMIN_ID:
        user_states[cid] = "admin_set_days"
        try:
            bot.edit_message_text(f"📅 Текущее кол-во дней: {get_setting('subscription_days')}\n\nВведите новое количество:", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_blizko_extra_price" and cid == ADMIN_ID:
        user_states[cid] = "admin_set_blizko_extra_price"
        try:
            bot.edit_message_text(
                f"➕ Текущая базовая цена доп.аккаунта: {get_blizko_extra_base_price()}₽\n\n"
                f"Это цена за ПЕРВЫЙ доп.аккаунт. Каждый следующий на том же устройстве — вдвое дороже "
                f"(2-й = ×2, 3-й = ×4 и т.д. автоматически).\n\nВведите новую базовую цену (число):",
                cid, call.message.message_id, reply_markup=back_main_kb(cid)
            )
        except:
            pass
    elif data == "admin_vpn_price" and cid == ADMIN_ID:
        user_states[cid] = "admin_set_vpn_price"
        try:
            bot.edit_message_text(f"🔐 Текущая цена VPN: {get_vpn_price()}₽\n\nВведите новую цену (только число):", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_vpn_desc" and cid == ADMIN_ID:
        user_states[cid] = "admin_set_vpn_desc"
        current = get_vpn_description()
        try:
            bot.edit_message_text(f"📝 Текущее описание VPN:\n{current}\n\nВведите новое описание:", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_vpn_instruction" and cid == ADMIN_ID:
        user_states[cid] = "admin_edit_vpn_instruction"
        current = get_vpn_instruction()
        try:
            bot.edit_message_text(
                f"📖 Текущая инструкция VPN:\n\n{current}\n\nВведите новый текст (можно использовать {{key}} для подстановки ключа):",
                cid, call.message.message_id,
                reply_markup=back_main_kb(cid)
            )
        except:
            pass
    elif data == "admin_vpn_keys" and cid == ADMIN_ID:
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("➕ Добавить ключ", callback_data="admin_add_vpn_key"))
        kb.add(telebot.types.InlineKeyboardButton("📋 Список ключей", callback_data="admin_list_vpn_keys"))
        kb.add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="back_admin"))
        try:
            bot.edit_message_text("Управление ключами VPN:", cid, call.message.message_id, reply_markup=kb)
        except:
            pass
    elif data == "admin_add_vpn_key" and cid == ADMIN_ID:
        user_states[cid] = "admin_add_vpn_key"
        try:
            bot.edit_message_text("Введите новый ключ VPN (одной строкой):", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_list_vpn_keys" and cid == ADMIN_ID:
        keys = get_all_vpn_keys()
        if not keys:
            text = "Список ключей пуст."
        else:
            text = "🔑 *Ключи VPN:*\n\n"
            for kid, ktext, used, used_by in keys:
                status = "✅ свободен" if not used else f"❌ использован (user {used_by})"
                text += f"`{ktext}`\n{status}\n\n"
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="admin_vpn_keys"))
        try:
            bot.edit_message_text(text, cid, call.message.message_id, reply_markup=kb, parse_mode="Markdown")
        except:
            pass
    elif data == "admin_ad_toggle" and cid == ADMIN_ID:
        current = get_setting("ad_active") == "1"
        set_setting("ad_active", "0" if current else "1")
        bot.answer_callback_query(call.id, f"Реклама {'выключена' if current else 'включена'}")
        try:
            bot.edit_message_reply_markup(cid, call.message.message_id, reply_markup=admin_kb())
        except:
            pass
    elif data == "admin_ad_text" and cid == ADMIN_ID:
        user_states[cid] = "admin_set_ad"
        current = get_setting("ad_text") or "не задан"
        try:
            bot.edit_message_text(f"✏️ Текущий текст рекламы:\n{current}\n\nВведите новый текст:", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_give_sub" and cid == ADMIN_ID:
        user_states[cid] = "admin_give_sub"
        try:
            bot.edit_message_text(f"➕ Введите Telegram ID пользователя\n(подписка на {get_setting('subscription_days')} дней):", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_broadcast" and cid == ADMIN_ID:
        user_states[cid] = "admin_broadcast"
        try:
            bot.edit_message_text("📢 Введите текст рассылки:", cid, call.message.message_id, reply_markup=back_main_kb(cid))
        except:
            pass
    elif data == "admin_post_now" and cid == ADMIN_ID:
        bot.answer_callback_query(call.id, "⏳ Публикую пост...")
        threading.Thread(target=_admin_post_now, args=(cid,)).start()
    elif data == "admin_tickets" and cid == ADMIN_ID:
        tickets = get_tickets()
        if not tickets:
            bot.answer_callback_query(call.id, "🎫 Обращений нет")
        else:
            for uid, msg in tickets:
                kb = telebot.types.InlineKeyboardMarkup()
                kb.add(telebot.types.InlineKeyboardButton("✉️ Ответить", callback_data=f"reply_{uid}"))
                bot.send_message(cid, f"🎫 От {uid}:\n\n{msg}", reply_markup=kb)
    elif data == "admin_stats" and cid == ADMIN_ID:
        total_users, active_subs, today_subs, total_subs = get_stats()
        users = get_users_list(offset=0, limit=20)
        vpn_free, vpn_used, vpn_active_subs = get_vpn_stats()
        stats_text = (
            f"📊 Статистика\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Активных подписок (резюме): {active_subs}\n"
            f"📅 Подписок за сегодня: {today_subs}\n"
            f"📈 Всего подписок (за всё время): {total_subs}\n"
            f"🔐 Активных VPN: {vpn_active_subs}\n"
            f"🔑 VPN ключей: свободно {vpn_free}, использовано {vpn_used}\n\n"
            f"Список пользователей (первые 20):\n"
        )
        if users:
            for uid, sub_end in users:
                if sub_end:
                    stats_text += f"{uid} — до {sub_end.strftime('%d.%m.%Y')}\n"
                else:
                    stats_text += f"{uid} — без подписки\n"
        else:
            stats_text += "Нет пользователей.\n"
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="back_admin"))
        try:
            bot.edit_message_text(stats_text, cid, call.message.message_id, reply_markup=kb)
        except Exception as e:
            logger.error(f"Ошибка редактирования: {e}")
            bot.send_message(cid, stats_text, reply_markup=kb)
    elif data == "back_admin" and cid == ADMIN_ID:
        _show_admin(cid)
    elif data.startswith("reply_") and cid == ADMIN_ID:
        target_id = int(data.split("_")[1])
        user_states[cid] = f"replying_{target_id}"
        bot.send_message(cid, f"✉️ Введите ответ пользователю {target_id}:")
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

def _admin_post_now(admin_cid):
    try:
        topic_index = load_topic_index()
        topic = TOPICS_RU[topic_index % len(TOPICS_RU)]
        if post_with_retry(topic, retries=2):
            save_topic_index(topic_index + 1)
            bot.send_message(admin_cid, f"✅ Пост опубликован!\nТема: {topic}")
        else:
            bot.send_message(admin_cid, "❌ Не удалось опубликовать пост.")
    except Exception as e:
        logger.error(f"Ошибка _admin_post_now: {e}")
        bot.send_message(admin_cid, f"❌ Ошибка: {e}")

@bot.message_handler(content_types=["document"])
def doc_handler(message):
    cid = message.chat.id
    if user_states.get(cid) != "waiting_resume":
        return
    doc = message.document
    if not doc.file_name.endswith(".txt"):
        bot.send_message(cid, t(cid, "only_txt"))
        return
    file_info = bot.get_file(doc.file_id)
    downloaded = bot.download_file(file_info.file_path)
    user_data.setdefault(cid, {})["resume"] = downloaded.decode("utf-8")
    user_states[cid] = "waiting_vacancy"
    send_menu(cid, t(cid, "step2"), back_resume_kb(cid))

@bot.message_handler(content_types=["text"])
def text_handler(message):
    cid = message.chat.id
    text = message.text
    state = user_states.get(cid)

    if text.startswith("/"):
        return

    if state == "awaiting_blizko_link":
        request_id = extract_request_id(text)
        if not request_id:
            bot.send_message(cid, t(cid, "blizko_link_invalid"))
            return
        user_states[cid] = None
        show_blizko_offer(cid, request_id)
        return

    if state == "writing_support":
        save_ticket(cid, text)
        user_states[cid] = None
        try:
            bot.delete_message(cid, message.message_id)
        except:
            pass
        send_menu(cid, t(cid, "support_sent", email=SUPPORT_EMAIL) + "\n\n" + t(cid, "main_menu"), main_kb(cid))
        try:
            bot.send_message(ADMIN_ID, f"🎫 Новое обращение от {cid}:\n\n{text}")
        except:
            pass
        return

    if state and state.startswith("replying_") and cid == ADMIN_ID:
        target_id = int(state.split("_")[1])
        try:
            bot.send_message(target_id, f"📨 Ответ от поддержки:\n\n{text}")
            delete_ticket(target_id)
            bot.send_message(cid, f"✅ Ответ отправлен пользователю {target_id}")
        except Exception as e:
            bot.send_message(cid, f"❌ Ошибка: {e}")
        user_states[cid] = None
        _show_admin(cid)
        return

    if state == "admin_set_price" and cid == ADMIN_ID:
        try:
            set_setting("price", int(text))
            user_states[cid] = None
            _show_admin(cid)
        except:
            bot.send_message(cid, "❌ Введите число.")
        return

    if state == "admin_set_days" and cid == ADMIN_ID:
        try:
            set_setting("subscription_days", int(text))
            user_states[cid] = None
            _show_admin(cid)
        except:
            bot.send_message(cid, "❌ Введите число.")
        return

    if state == "admin_set_blizko_extra_price" and cid == ADMIN_ID:
        try:
            set_setting("blizko_extra_base_price", int(text))
            user_states[cid] = None
            _show_admin(cid)
        except:
            bot.send_message(cid, "❌ Введите число.")
        return

    if state == "admin_set_vpn_price" and cid == ADMIN_ID:
        try:
            new_price = int(text)
            set_setting("vpn_price", new_price)
            user_states[cid] = None
            _show_admin(cid)
        except:
            bot.send_message(cid, "❌ Введите число (рубли).")
        return

    if state == "admin_set_vpn_desc" and cid == ADMIN_ID:
        set_setting("vpn_description", text)
        user_states[cid] = None
        _show_admin(cid)
        return

    if state == "admin_edit_vpn_instruction" and cid == ADMIN_ID:
        set_setting("vpn_instruction", text)
        bot.send_message(cid, "✅ Инструкция VPN обновлена!")
        user_states[cid] = None
        _show_admin(cid)
        return

    if state == "admin_add_vpn_key" and cid == ADMIN_ID:
        key_text = text.strip()
        if add_vpn_key(key_text):
            bot.send_message(cid, f"✅ Ключ `{key_text}` добавлен в пул.", parse_mode="Markdown")
        else:
            bot.send_message(cid, "❌ Не удалось добавить ключ (возможно, уже существует).")
        user_states[cid] = None
        _show_admin(cid)
        return

    if state == "admin_set_ad" and cid == ADMIN_ID:
        set_setting("ad_text", text)
        set_setting("ad_active", "1")
        user_states[cid] = None
        _show_admin(cid)
        return

    if state == "admin_give_sub" and cid == ADMIN_ID:
        try:
            target_id = int(text.strip())
            user = get_user(target_id)
            if not user:
                upsert_user(target_id)
            days = int(get_setting("subscription_days") or 30)
            sub_end = activate_subscription(target_id, days)
            date_str = sub_end.strftime("%d.%m.%Y")
            try:
                bot.send_message(target_id, f"🎉 Подписка выдана до {date_str}!", reply_markup=main_kb(target_id))
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение {target_id}: {e}")
            bot.send_message(cid, f"✅ Подписка выдана пользователю {target_id} до {date_str}")
        except ValueError:
            bot.send_message(cid, "❌ Неверный ID. Введите число.")
        except Exception as e:
            bot.send_message(cid, f"❌ Ошибка: {e}")
        user_states[cid] = None
        _show_admin(cid)
        return

    if state == "admin_broadcast" and cid == ADMIN_ID:
        users = get_all_users()
        sent = 0
        for uid in users:
            try:
                bot.send_message(uid, f"📢 {text}")
                sent += 1
            except Exception as e:
                logger.error(f"Не удалось отправить {uid}: {e}")
        bot.send_message(cid, f"✅ Отправлено {sent}/{len(users)}")
        user_states[cid] = None
        _show_admin(cid)
        return

    user = get_user(cid)
    if not user or not user["agreed"]:
        send_menu(cid, t(cid, "need_agree"), agree_kb(cid))
        return

    if state == "waiting_resume":
        if len(text) < 50:
            bot.send_message(cid, t(cid, "too_short_resume"))
            return
        user_data.setdefault(cid, {})["resume"] = text
        user_states[cid] = "waiting_vacancy"
        send_menu(cid, t(cid, "step2"), back_resume_kb(cid))
    elif state == "waiting_vacancy":
        if re.match(r'https?://\S+', text.strip()):
            bot.send_message(cid, t(cid, "no_links"))
            return
        if len(text) < 30:
            bot.send_message(cid, t(cid, "too_short_vacancy"))
            return
        resume = user_data.get(cid, {}).get("resume", "")
        user_states[cid] = None
        delete_prev_menu(cid)
        proc_msg = bot.send_message(cid, t(cid, "processing"))
        lang = get_lang(cid)
        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT[lang]},
                    {"role": "user", "content": f"RESUME:\n{resume}\n\n===\n\nVACANCY:\n{text}"}
                ],
                max_tokens=2000,
                temperature=0.1
            )
            result = response.choices[0].message.content
            try:
                bot.delete_message(cid, proc_msg.message_id)
            except:
                pass
            full_text = t(cid, "result_title") + result
            if len(full_text) > 4000:
                bot.send_message(cid, t(cid, "result_title"))
                for i in range(0, len(result), 4000):
                    bot.send_message(cid, result[i:i+4000])
            else:
                bot.send_message(cid, full_text)
            send_menu(cid, t(cid, "result_next"), result_kb(cid))
        except Exception as e:
            logger.error(f"Groq error: {e}")
            try:
                bot.delete_message(cid, proc_msg.message_id)
            except:
                pass
            send_menu(cid, t(cid, "error"), main_kb(cid))
    else:
        send_menu(cid, t(cid, "main_menu"), main_kb(cid))

@app.route("/" + BOT_TOKEN, methods=["POST"])
def webhook():
    try:
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return "OK", 200

@app.route("/webhook/platiga", methods=["POST"])
def platiga_webhook():
    data = request.get_json(silent=True) or {}
    logger.info(f"Platiga webhook: {data}")
    status = data.get("status")
    payload_str = data.get("payload") or "{}"
    try:
        payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
    except Exception as e:
        logger.error(f"Ошибка парсинга payload: {e}")
        payload = {}
    user_id = payload.get("user_id")
    service_type = payload.get("type", "subscription")
    if status == "CONFIRMED" and user_id:
        try:
            user_id = int(user_id)
            if service_type == "subscription":
                sub_end = activate_subscription(user_id)
                date_str = sub_end.strftime("%d.%m.%Y %H:%M")
                bot.send_message(user_id, t(user_id, "payment_success", date=date_str), reply_markup=main_kb(user_id))
            elif service_type == "vpn":
                key_id, key_text = get_free_vpn_key()
                if key_id:
                    deactivate_old_vpn(user_id)
                    activate_vpn(user_id, key_id)
                    instruction_template = get_vpn_instruction()
                    instruction = instruction_template.replace("{key}", key_text) if "{key}" in instruction_template else instruction_template + f"\n\n🔑 Ваш ключ: `{key_text}`"
                    bot.send_message(user_id, t(user_id, "vpn_paid_success", instruction=instruction), parse_mode="Markdown", reply_markup=main_kb(user_id))
                else:
                    bot.send_message(user_id, t(user_id, "vpn_no_keys"), reply_markup=main_kb(user_id))
                    bot.send_message(ADMIN_ID, f"⚠️ У пользователя {user_id} прошла оплата VPN, но нет свободных ключей!")
            elif service_type == "blizko_extra_account":
                request_id = payload.get("request_id")
                if request_id:
                    code = blizko_mark_paid(request_id)
                    if code:
                        bot.send_message(
                            user_id,
                            f"✅ Оплата получена!\n\n➕ Дополнительный аккаунт\n\nТвой код доступа:\n`{code}`\n\nВставь его на сайте Blizko, чтобы продолжить.",
                            parse_mode="Markdown",
                            reply_markup=main_kb(user_id)
                        )
                    else:
                        bot.send_message(user_id, "⚠️ Оплата получена, но возникла ошибка генерации кода. Напишите в поддержку.", reply_markup=main_kb(user_id))
                        bot.send_message(ADMIN_ID, f"⚠️ Blizko: оплата прошла, но mark-paid не сработал. user={user_id} request_id={request_id}")
                else:
                    logger.error("blizko payload без request_id")
        except Exception as e:
            logger.error(f"Ошибка обработки платежа: {e}")
    return "OK", 200

@app.route("/cron/post", methods=["GET"])
def cron_post():
    threading.Thread(target=scheduled_job).start()
    return "OK", 200

@app.route("/api/blizko-prices", methods=["GET"])
def blizko_prices():
    return {
        "extra_base_price": get_blizko_extra_base_price()
    }, 200

@app.route("/")
def index():
    return "Bot is running!", 200

def startup():
    logger.info("🔧 Запуск инициализации базы данных и вебхука...")
    try:
        init_database()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
    try:
        bot.remove_webhook()
        webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}"
        bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Вебхук установлен: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}")

startup()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
