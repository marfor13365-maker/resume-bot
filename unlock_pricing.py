# unlock_pricing.py
# Логика: история покупок в Supabase Postgres + прогрессивная цена 2 -> 4 -> 8 -> 16 ...
# Подключить в основной файл бота (например resume_bot.py) через:
#   from unlock_pricing import get_next_price, record_purchase, mark_paid, has_pending

import psycopg2
import psycopg2.extras
import secrets
import string

DATABASE_URL = "postgresql://..."  # тот же DSN, что у resume-bot / Blizko backend
BASE_PRICE = 2  # базовая цена в рублях/у.е. — поменяй под реальную цену

# ---------- SQL миграция (выполнить один раз) ----------
"""
CREATE TABLE IF NOT EXISTS unlock_purchases (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    device_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('unlock','extra_account')),
    price INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid','expired')),
    code TEXT UNIQUE,
    email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_unlock_user_kind ON unlock_purchases(telegram_user_id, kind, status);
"""

def _conn():
    return psycopg2.connect(DATABASE_URL)


def get_next_price(telegram_user_id: int, kind: str) -> int:
    """Считает следующую цену: BASE_PRICE * 2^(число уже ОПЛАЧЕННЫХ покупок этого kind этим юзером)."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM unlock_purchases WHERE telegram_user_id=%s AND kind=%s AND status='paid'",
            (telegram_user_id, kind),
        )
        paid_count = cur.fetchone()[0]
    return BASE_PRICE * (2 ** paid_count)


def has_pending(telegram_user_id: int, kind: str) -> bool:
    """Не даём создать вторую заявку, пока первая не оплачена/не отменена — защита от дублей."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM unlock_purchases WHERE telegram_user_id=%s AND kind=%s AND status='pending' LIMIT 1",
            (telegram_user_id, kind),
        )
        return cur.fetchone() is not None


def record_purchase(telegram_user_id: int, device_id: str, kind: str, price: int, email: str = None) -> int:
    """Создаёт заявку в статусе pending, возвращает id заявки."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO unlock_purchases (telegram_user_id, device_id, kind, price, email)
               VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            (telegram_user_id, device_id, kind, price, email),
        )
        purchase_id = cur.fetchone()[0]
        conn.commit()
    return purchase_id


def _gen_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def mark_paid(purchase_id: int) -> str:
    """Вызывать из обработчика успешного платежа (successful_payment). Генерирует уникальный код."""
    code = _gen_code()
    with _conn() as conn, conn.cursor() as cur:
        # на случай коллизии — повторить генерацию при конфликте
        for _ in range(5):
            try:
                cur.execute(
                    """UPDATE unlock_purchases
                       SET status='paid', code=%s, paid_at=now()
                       WHERE id=%s RETURNING code""",
                    (code, purchase_id),
                )
                conn.commit()
                return code
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                code = _gen_code()
    raise RuntimeError("Не удалось сгенерировать уникальный код")
