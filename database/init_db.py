import sqlite3

# подключаемся к базе (если файла нет — он создастся)
conn = sqlite3.connect("database.db")
cursor = conn.cursor()

# создаём таблицу пользователей
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    balance REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# создаём таблицу ссылок
cursor.execute("""
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    link TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
)
""")

# сохраняем изменения и закрываем соединение
conn.commit()
conn.close()

print("✅ База и таблицы созданы")

cursor.execute("""
ALTER TABLE links
ADD COLUMN clicks INTEGER DEFAULT 0
""")

cursor.execute("""
ALTER TABLE links
ADD COLUMN max_clicks INTEGER DEFAULT 2;
""")