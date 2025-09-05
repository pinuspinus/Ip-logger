from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


admin_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users_list"),
        InlineKeyboardButton(text="🔎 Поиск пользователя", callback_data="admin_user_search"),
    ],
    [
        InlineKeyboardButton(text="🚫 Бан", callback_data="admin_ban"),
        InlineKeyboardButton(text="✅ Разбан", callback_data="admin_unban"),
    ],
    [
        InlineKeyboardButton(text="💰 Изменить баланс", callback_data="admin_edit_balance"),
    ],
    [
        InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="💵 Доход за период", callback_data="admin_income"),
    ],
    [
        InlineKeyboardButton(text="⚡ Активность за сутки", callback_data="admin_activity_day"),
        InlineKeyboardButton(text="📅 Активность за неделю", callback_data="admin_activity_week"),
    ],
    [
        InlineKeyboardButton(text="📢 Рассылка пользователям", callback_data="admin_broadcast"),
        InlineKeyboardButton(text="🔗 Изменить кол-во переходов у ссылки", callback_data="clicks_up"),
    ],
])

admin_home = InlineKeyboardMarkup(inline_keyboard = [[InlineKeyboardButton(text="🏠 В админ-меню", callback_data="admin_home")]])