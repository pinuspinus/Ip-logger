import sqlite3

# подключаемся к базе (если файла нет — он создастся)
conn = sqlite3.connect("database.db")
cursor = conn.cursor()

# # создаём таблицу пользователей
# cursor.execute("""
# CREATE TABLE IF NOT EXISTS users (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     telegram_id INTEGER UNIQUE NOT NULL,
#     balance REAL DEFAULT 0,
#     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
# )
# """)
#
# # создаём таблицу ссылок
# cursor.execute("""
# CREATE TABLE IF NOT EXISTS links (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     user_id INTEGER NOT NULL,
#     link TEXT NOT NULL,
#     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#     FOREIGN KEY (user_id) REFERENCES users (id)
# )
# """)
#
# # сохраняем изменения и закрываем соединение
# conn.commit()
# conn.close()
#
# print("✅ База и таблицы созданы")
#
# cursor.execute("""
# ALTER TABLE links
# ADD COLUMN clicks INTEGER DEFAULT 0
# """)
#
# cursor.execute("""
# ALTER TABLE links
# ADD COLUMN max_clicks INTEGER DEFAULT 2;
# """)
#
#
# cursor.execute("""
# ALTER TABLE links ADD COLUMN original_url TEXT;
# """)
#
# cursor.execute("""
# DROP TABLE links;
# """)
# # создаём таблицу ссылок с нужными полями
# cursor.execute("""
# CREATE TABLE IF NOT EXISTS links (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     user_id INTEGER NOT NULL,
#     link TEXT NOT NULL UNIQUE,
#     original_url TEXT NOT NULL,
#     clicks INTEGER DEFAULT 0,
#     max_clicks INTEGER DEFAULT 1,
#     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#     FOREIGN KEY (user_id) REFERENCES users (id)
# )
# """)



# --- invoices ---
# cursor.execute("""
# CREATE TABLE IF NOT EXISTS invoices (
#     invoice_id  TEXT PRIMARY KEY,
#     user_id     INTEGER NOT NULL,
#     amount      REAL    NOT NULL,
#     asset       TEXT    NOT NULL,
#     status      TEXT    NOT NULL CHECK (status IN ('pending','paid','expired')),
#     payload     TEXT,
#     created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#     FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
# );
# """)
#
# cursor.execute("""
# CREATE INDEX IF NOT EXISTS idx_invoices_user_status
# ON invoices (user_id, status);
# """)
#
# conn.commit()
# conn.close()
# print("✅ База и таблицы готовы:")



# cursor.execute("""
# SELECT name FROM sqlite_master
# WHERE type='table'
# ORDER BY name;
# """)
# rows = cursor.fetchall()
#
# if not rows:
#     print("Таблиц нет.")
# else:
#     print("📋 Таблицы в БД:")
#     for (name,) in rows:   # каждая строка = кортеж из одного поля name
#         print(" •", name)

#
# cursor.execute("""
# Alter table users ADD COLUMN banned BOOLEAN DEFAULT FALSE;
# """)
#
# conn.commit()
# conn.close()
# print("✅ База и таблицы готовы:")


cursor.execute("""
DELETE  FROM links;
""")

# сохраняем изменения и закрываем соединение
conn.commit()
conn.close()

