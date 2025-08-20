import sqlite3

DB_NAME = "database/database.db"
def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # чтобы результаты были как словари
    return conn

def add_user(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
        (telegram_id,)
    )

    conn.commit()
    conn.close()


def get_user_id(telegram_id, cursor):
    cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()  # получаем первую строку результата

    if row:
        return row["id"]  # возвращаем id пользователя
    return None  # если пользователя нет


def add_link(original_url, short_link, telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    user_id = get_user_id(telegram_id, cursor)
    if user_id is None:
        conn.close()
        return None  # пользователь не найден

    cursor.execute(
        "INSERT INTO links (user_id, link, original_url) VALUES (?, ?, ?)",
        (user_id, short_link, original_url)
    )

    conn.commit()
    conn.close()
    return True

def get_links(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    user_id = get_user_id(telegram_id, cursor)

    # Получаем все ссылки пользователя
    cursor.execute("SELECT original_url, link, created_at, clicks FROM links WHERE user_id = ?", (user_id,))
    links = cursor.fetchall()  # список sqlite3.Row

    conn.close()

    # Преобразуем в список словарей для удобства
    return [{"link": l["link"], "created_at": l["created_at"], "clicks": l["clicks"]} for l in links]


def get_balance(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return row["balance"]  # возвращаем баланс
    return 0.0  # если пользователя нет
