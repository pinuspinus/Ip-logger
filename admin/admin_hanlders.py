import asyncio
from urllib.parse import urlsplit, urlparse
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton
from config import SERVER_URL, PREFERRED_SCHEME
from database.db_api import get_all_users, ban_user, unban_user, minus_balance, add_balance, \
    get_links, change_count_clicks  # <-- тут импортируешь свою функцию
from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from database.db_api import get_user   # твоя функция
from .admin_keyboards import admin_kb, admin_home
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup
from contextlib import closing, suppress
from datetime import datetime, timedelta, timezone
from .admin_creds import ADMIN_IDS
from database.db_api import get_connection  # твоя функция подключения
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError
from decimal import Decimal, ROUND_HALF_UP


admin_router = Router()

@admin_router.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌️ У вас нет прав для этой команды ❌")
        print(message.from_user.id)
        return
    else:
        await message.answer("⚙️ Добро пожаловать в админ-панель!", reply_markup=admin_kb)


PAGE_SIZE = 10  # сколько пользователей показывать на странице
COST_PER_LINK = 1.0


# Вспомогательная функция для построения клавиатуры пагинации
def build_users_pager_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_users_list:{page-1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"admin_users_list:{page+1}"))
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🏠 В админ-меню", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Обработка кнопки "Список пользователей"
@admin_router.callback_query(F.data.startswith("admin_users_list"))
async def admin_users_list_cb(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    # получаем номер страницы из callback_data
    parts = callback.data.split(":")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    users = get_all_users()
    total = len(users)
    if total == 0:
        await callback.message.edit_text("— Пользователей нет —")
        await callback.answer()
        return

    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(0, min(page, total_pages - 1))  # защита от выхода за пределы
    start, end = page * PAGE_SIZE, (page + 1) * PAGE_SIZE
    page_items = users[start:end]

    # формируем текст ответа
    lines = [f"👥 Список пользователей (стр. {page+1}/{total_pages}, всего: {total})\n"]
    for u in page_items:
        uid = u.get("id", "-")
        tid = u.get("telegram_id", "-")
        bal = u.get("balance", 0)
        bal_str = f"{float(bal):.3f}".rstrip("0").rstrip(".")
        banned = u.get("banned", 0)
        created_raw = u.get("created_at", "-")
        created = created_raw
        if created_raw and created_raw != "-":
            try:
                # если в БД хранится как текст в ISO-формате
                dt = datetime.fromisoformat(str(created_raw))
                created = dt.strftime("%Y-%m-%d")  # только дата
            except Exception:
                created = str(created_raw).split()[0]  # запасной вариант: взять всё до пробела

        lines.append(
            f"👤 <code>{uid}</code> | 📨 <code>{tid}</code> | 💰 {bal_str} | 🕒 {created} | ⛔ {banned}"
        )

    text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=build_users_pager_kb(page, total_pages),
        parse_mode="HTML"
    )
    await callback.answer()

@admin_router.callback_query(F.data == "admin_home")
async def admin_home_cb(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    # Закрываем "часики" у кнопки сразу
    with suppress(Exception):
        await callback.answer()

    text = "⚙️ Админ-панель"

    try:
        if isinstance(admin_kb, InlineKeyboardMarkup):
            # Редактировать можно только с inline-клавиатурой
            await callback.message.edit_text(
                text,
                reply_markup=admin_kb,
                disable_web_page_preview=True
            )
        else:
            # Если admin_kb — ReplyKeyboardMarkup, редактировать нельзя: шлём новое сообщение
            await callback.message.answer(text, reply_markup=admin_kb)
            # По желанию удалим старое сообщение с инлайн-клавой (если было)
            with suppress(Exception):
                await callback.message.delete()

    except TelegramBadRequest:
        # Например: "message is not modified" / "can't be edited"
        await callback.message.answer(text, reply_markup=admin_kb)
        with suppress(Exception):
            await callback.message.delete()

    except TelegramNetworkError:
        # Сетевой сбой — пробуем отправить новое сообщение
        await callback.message.answer(text, reply_markup=admin_kb)

    finally:
        # Сброс состояния безопасен даже если состояния не было
        with suppress(Exception):
            await state.clear()


# Состояние для FSM
class WaitForSMTHUser(StatesGroup):
    waiting_id = State()
    waiting_id_for_ban = State()
    waiting_id_for_unban = State()
    waiting_for_amount_to_change_balance = State()
    waiting_text = State()
    waiting_count_clicks = State()





@admin_router.callback_query(F.data.startswith("admin_user_search"))
async def admin_user_search(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    await callback.message.edit_text(
        text="🔎 Отправь мне <b>telegram ID</b> пользователя",
        parse_mode="HTML",
        reply_markup=admin_home   # или admin_home, если отдельная кнопка возврата
    )
    await state.set_state(WaitForSMTHUser.waiting_id)


ADMIN_PAGE_SIZE = 10  # сколько показывать на странице

def _fmt_money(x) -> str:
    try:
        return f"{Decimal(str(x)):.3f}".rstrip("0").rstrip(".")
    except Exception:
        return str(x)

def _fetch_user_invoices(user_id: int, offset: int, limit: int):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM invoices WHERE user_id=?", (user_id,))
    total = (cur.fetchone() or {"cnt": 0})["cnt"]
    cur.execute(
        "SELECT invoice_id, amount, asset, status, payload, created_at "
        "FROM invoices WHERE user_id=? "
        "ORDER BY created_at DESC, rowid DESC "
        "LIMIT ? OFFSET ?",
        (user_id, limit, offset)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return total, rows

def _fetch_user_links(user_id: int, offset: int, limit: int):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM links WHERE user_id=?", (user_id,))
    total = (cur.fetchone() or {"cnt": 0})["cnt"]
    cur.execute(
        "SELECT original_url, link, short_host, clicks, max_clicks, created_at "
        "FROM links WHERE user_id=? "
        "ORDER BY created_at DESC, rowid DESC "
        "LIMIT ? OFFSET ?",
        (user_id, limit, offset)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return total, rows

def _admin_user_kb(telegram_id: int, user_id: int, inv_page: int = 0, lnk_page: int = 0,
                   inv_pages: int = 1, lnk_pages: int = 1) -> InlineKeyboardMarkup:
    # делаем кнопки пагинации для инвойсов и ссылок
    inv_controls = []
    if inv_page > 0:
        inv_controls.append(InlineKeyboardButton(text="⬅️ Инвойсы", callback_data=f"admin:uid:{user_id}:inv:{inv_page-1}:{lnk_page}"))
    if inv_page < inv_pages - 1:
        inv_controls.append(InlineKeyboardButton(text="Инвойсы ➡️", callback_data=f"admin:uid:{user_id}:inv:{inv_page+1}:{lnk_page}"))

    lnk_controls = []
    if lnk_page > 0:
        lnk_controls.append(InlineKeyboardButton(text="⬅️ Ссылки", callback_data=f"admin:uid:{user_id}:lnk:{inv_page}:{lnk_page-1}"))
    if lnk_page < lnk_pages - 1:
        lnk_controls.append(InlineKeyboardButton(text="Ссылки ➡️", callback_data=f"admin:uid:{user_id}:lnk:{inv_page}:{lnk_page+1}"))

    rows = []
    if inv_controls:
        rows.append(inv_controls)
    if lnk_controls:
        rows.append(lnk_controls)
    rows.append([InlineKeyboardButton(text="🏠 Админ-меню", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _render_user_card_and_lists(user: dict, inv_page: int, lnk_page: int) -> tuple[str, InlineKeyboardMarkup]:
    # карточка
    bal_str = _fmt_money(user.get('balance', 0))
    header = (
        f"👤 Пользователь\n"
        f"• Внутренний ID: <code>{user['id']}</code>\n"
        f"• Telegram ID: <code>{user['telegram_id']}</code>\n"
        f"• 💰 Баланс: {bal_str} USDT\n"
        f"• 🕒 Создан: {user.get('created_at', 'N/A')} UTC\n"
        f"• 🚫 Бан: {bool(user.get('banned'))}\n\n"
    )

    # инвойсы
    inv_total, inv_rows = _fetch_user_invoices(user['id'], inv_page * ADMIN_PAGE_SIZE, ADMIN_PAGE_SIZE)
    inv_pages = max(1, (inv_total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    inv_block = [f"📑 Инвойсы (страница {inv_page+1}/{inv_pages}; всего {inv_total})\n"]
    if inv_rows:
        for r in inv_rows:
            inv_block.append(
                f"💳 <b>{r['status']}</b> — <code>{r['invoice_id']}</code>\n"
                f"💰 Сумма: {_fmt_money(r['amount'])} {r.get('asset', '')}\n"
                f"⏰ {r.get('created_at', 'N/A')} UTC\n"
            )
    else:
        inv_block.append("— нет записей")
    inv_block.append("")  # пустая строка

    # ссылки
    lnk_total, lnk_rows = _fetch_user_links(user['id'], lnk_page * ADMIN_PAGE_SIZE, ADMIN_PAGE_SIZE)
    lnk_pages = max(1, (lnk_total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    lnk_block = [f"🔗 Ссылки (страница {lnk_page+1}/{lnk_pages}; всего {lnk_total})"]
    if lnk_rows:
        for r in lnk_rows:
            slug = r.get("link", "N/A")
            host = (r.get("short_host") or "").strip()

            short = f"{PREFERRED_SCHEME}://{host}/link/{slug}"


            lnk_block.append(
                f"— <code>{r.get('original_url', 'N/A')}</code>\n"
                f"  ➡️ <code>{short}</code>\n"
                f"  👀 {int(r.get('clicks', 0))}/{int(r.get('max_clicks', 1))}\n"
                f"  🕒 {r.get('created_at', 'N/A')} UTC"
            )
    else:
        lnk_block.append("— нет ссылок")

    text = header + "\n".join(inv_block + [""] + lnk_block)
    kb = _admin_user_kb(user['telegram_id'], user['id'], inv_page, lnk_page, inv_pages, lnk_pages)
    return text, kb

# === Ввод Telegram ID админом ===
@admin_router.message(WaitForSMTHUser.waiting_id, F.text)
async def process_user_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        tid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи корректный <b>Telegram ID</b> (число)", parse_mode="HTML")
        return

    user = get_user(tid)  # должен вернуть dict с полями id, telegram_id, balance, created_at, banned
    if not user:
        await message.answer("⚠️ Пользователь не найден")
    else:
        text, kb = _render_user_card_and_lists(user, inv_page=0, lnk_page=0)
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)

    await state.clear()

# === Пагинация инвойсов ===
@admin_router.callback_query(F.data.regexp(r"^admin:uid:(\d+):inv:(\d+):(\d+)$"))
async def admin_user_invoices_page(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True); return

    m = callback.data.split(":")  # ["admin","uid","<user_id>","inv","<inv_page>","<lnk_page>"]
    user_id = int(m[2]); inv_page = int(m[4]); lnk_page = int(m[5])

    # достаём пользователя по внутреннему id
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await callback.message.edit_text("⚠️ Пользователь не найден")
        await callback.answer(); return
    user = dict(row)

    text, kb = _render_user_card_and_lists(user, inv_page=inv_page, lnk_page=lnk_page)
    await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
    await callback.answer()

# === Пагинация ссылок ===
@admin_router.callback_query(F.data.regexp(r"^admin:uid:(\d+):lnk:(\d+):(\d+)$"))
async def admin_user_links_page(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True); return

    m = callback.data.split(":")  # ["admin","uid","<user_id>","lnk","<inv_page>","<lnk_page>"]
    user_id = int(m[2]); inv_page = int(m[4]); lnk_page = int(m[5])

    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await callback.message.edit_text("⚠️ Пользователь не найден")
        await callback.answer(); return
    user = dict(row)

    text, kb = _render_user_card_and_lists(user, inv_page=inv_page, lnk_page=lnk_page)
    await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
    await callback.answer()




@admin_router.callback_query(F.data.startswith("admin_ban"))
async def admin_ban(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    await callback.message.edit_text(
        text="🔎 Отправь мне <b>telegram ID</b> пользователя для бана",
        parse_mode="HTML",
        reply_markup=admin_home   # или admin_home, если отдельная кнопка возврата
    )
    await state.set_state(WaitForSMTHUser.waiting_id_for_ban)

# Принимаем ID от админа
@admin_router.message(WaitForSMTHUser.waiting_id_for_ban, F.text)
async def process_ban_user(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        tid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи корректный <b>ID</b> (число)", parse_mode="HTML")
        return
    user, error = ban_user(tid)

    if error:
        await message.answer(f"⚠️ {error}")
        return

    bal_str = f"{float(user['balance']):.3f}".rstrip("0").rstrip(".")
    text = (
        f"👤 ID: <code>{user['id']}</code>\n"
        f"📨 Telegram ID: <code>{tid}</code>\n"
        f"💰 Баланс: {bal_str}\n"
        f"🕒 Создан: {user['created_at']}\n"
        f"⛔ Бан: {user['banned']}"
    )
    await message.answer(text, parse_mode="HTML")
    # сбрасываем состояние
    await state.clear()


@admin_router.callback_query(F.data.startswith("admin_unban"))
async def admin_unban(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    await callback.message.edit_text(
        text="🔎 Отправь мне <b>telegram ID</b> пользователя для разбана",
        parse_mode="HTML",
        reply_markup=admin_home   # или admin_home, если отдельная кнопка возврата
    )
    await state.set_state(WaitForSMTHUser.waiting_id_for_unban)

# Принимаем ID от админа
@admin_router.message(WaitForSMTHUser.waiting_id_for_unban, F.text)
async def process_unban_user(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        tid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи корректный <b>ID</b> (число)", parse_mode="HTML")
        return
    user, error = unban_user(tid)

    if error:
        await message.answer(f"⚠️ {error}")
        return

    bal_str = f"{float(user['balance']):.3f}".rstrip("0").rstrip(".")
    text = (
        f"👤 ID: <code>{user['id']}</code>\n"
        f"📨 Telegram ID: <code>{tid}</code>\n"
        f"💰 Баланс: {bal_str}\n"
        f"🕒 Создан: {user['created_at']}\n"
        f"⛔ Бан: {user['banned']}"
    )
    await message.answer(text, parse_mode="HTML")
    # сбрасываем состояние
    await state.clear()

@admin_router.callback_query(F.data.startswith("admin_edit_balance"))
async def admin_edit_balance(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    await callback.message.edit_text(
        text=(
            "✏️ Введите данные в формате:\n"
            "<b>telegram_id:сумма</b>\n\n"
            "Примеры:\n"
            "<code>123456789:10.5</code> (прибавить)\n"
            "<code>123456789:-3</code> (вычесть)"
        ),
        parse_mode="HTML",
        reply_markup=admin_home  # или admin_home, смотря что у тебя
    )
    await state.set_state(WaitForSMTHUser.waiting_for_amount_to_change_balance)


@admin_router.message(WaitForSMTHUser.waiting_for_amount_to_change_balance, F.text)
async def process_change_balance(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    raw = message.text.strip()
    # ожидаем формат: telegram_id:amount
    try:
        if ":" not in raw:
            raise ValueError("format")
        left, right = [p.strip() for p in raw.split(":", 1)]
        tid = int(left)
        amount = Decimal(right.replace(",", "."))  # поддержка запятой
    except Exception:
        await message.answer("❌ Неверный формат. Пример: <code>123456789:10.5</code>", parse_mode="HTML")
        return

    # Проверим, что пользователь существует и возьмём старый баланс
    user = get_user(tid)
    if not user:
        await message.answer("⚠️ Пользователь не найден")
        await state.clear()
        return

    old_bal = Decimal(str(user["balance"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    old_bal_str = f"{old_bal.normalize():f}"

    # Изменяем баланс
    res = False
    new_balance = None
    error = None

    try:
        if amount < 0:
            # minus_balance ожидает положительную сумму
            res, new_balance, error = minus_balance(tid, abs(amount))
        else:
            ok = add_balance(tid, float(amount))   # add_balance работает с float
            if not ok:
                res, error = False, "Не удалось изменить баланс"
            else:
                after = get_user(tid)
                if not after:
                    res, error = False, "Пользователь не найден после операции"
                else:
                    new_balance = Decimal(str(after["balance"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    res, error = True, None
    except Exception as e:
        res, error = False, f"Ошибка БД: {e}"

    if error:
        await message.answer(f"⚠️ {error}")
        await state.clear()
        return

    if not res:
        await message.answer("⚠️ Операция не выполнена")
        await state.clear()
        return

    new_bal_str = f"{new_balance.normalize():f}"

    text = (
        f"👤 ID: <code>{user['id']}</code>\n"
        f"📨 Telegram ID: <code>{tid}</code>\n"
        f"💰 Старый баланс: {old_bal_str}\n"
        f"💰 Новый баланс: <b>{new_bal_str}</b>\n"
        f"🕒 Создан: {user['created_at']}\n"
        f"⛔ Бан: {user.get('banned', 0)}"
    )
    await message.answer(text, parse_mode="HTML")
    await state.clear()


def _utc_iso(dt: datetime) -> str:
    """Строка в формате 'YYYY-MM-DD HH:MM:SS' (UTC) для SQLite."""
    return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_stats_simple():
    """
    Возвращает:
    users_total / users_1d / users_7d / users_banned
    links_total / links_1d / links_7d
    clicks_total / clicks_1d=0 / clicks_7d=0
    active_users_1d: список {id, telegram_id, last_activity}
    """
    conn = get_connection()
    with closing(conn):
        cur = conn.cursor()

        now = datetime.utcnow()
        day_ago = _utc_iso(now - timedelta(days=1))
        week_ago = _utc_iso(now - timedelta(days=7))

        # ---- USERS ----
        cur.execute("SELECT COUNT(*) AS c FROM users")
        users_total = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at >= ?", (day_ago,))
        users_1d = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at >= ?", (week_ago,))
        users_7d = cur.fetchone()["c"]

        users_banned = 0
        with suppress(Exception):
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE banned = 1")
            users_banned = cur.fetchone()["c"]

        # ---- LINKS ----
        cur.execute("SELECT COUNT(*) AS c FROM links")
        links_total = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM links WHERE created_at >= ?", (day_ago,))
        links_1d = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM links WHERE created_at >= ?", (week_ago,))
        links_7d = cur.fetchone()["c"]

        # ---- CLICKS ---- (агрегат из links)
        cur.execute("SELECT COALESCE(SUM(clicks), 0) AS s FROM links")
        clicks_total = cur.fetchone()["s"] or 0
        clicks_1d = 0   # так как событий кликов нет, за периоды считать нечем
        clicks_7d = 0

        # ---- ACTIVE USERS (24h): создали ссылки ----
        cur.execute("""
            SELECT u.id, u.telegram_id, MAX(l.created_at) AS last_activity
            FROM users u
            JOIN links l ON l.user_id = u.id
            WHERE l.created_at >= ?
            GROUP BY u.id, u.telegram_id
            ORDER BY last_activity DESC
        """, (day_ago,))
        rows = cur.fetchall()
        active_users_1d = [
            {
                "id": r["id"] if hasattr(r, "keys") else r[0],
                "telegram_id": r["telegram_id"] if hasattr(r, "keys") else r[1],
                "last_activity": r["last_activity"] if hasattr(r, "keys") else r[2],
            }
            for r in rows
        ]

        return {
            "users_total": users_total,
            "users_1d": users_1d,
            "users_7d": users_7d,
            "users_banned": users_banned,

            "links_total": links_total,
            "links_1d": links_1d,
            "links_7d": links_7d,

            "clicks_total": int(clicks_total),
            "clicks_1d": int(clicks_1d),
            "clicks_7d": int(clicks_7d),

            "active_users_1d": active_users_1d,
        }


@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats_cb(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    stats = get_stats_simple()

    def fmt_int(n):
        try:
            return f"{int(n)}"
        except Exception:
            return str(n)

    # список активных
    active_lines = []
    for u in stats["active_users_1d"][:50]:
        uid = u.get("id", "-")
        tid = u.get("telegram_id", "-")
        last = u.get("last_activity", "-")
        if isinstance(last, str) and " " in last:
            last = last[:16]  # YYYY-MM-DD HH:MM
        active_lines.append(
            f"🆔 DB:<code>{uid}</code> | 💬 TG:<code>{tid}</code> | 🕒 {last}"
        )

    text = "\n".join([
        "📊 <b>Общая статистика</b>\n",

        "👥 <b>Пользователи</b> 👥",
        f"👤 Всего: <b>{fmt_int(stats['users_total'])}</b>",
        f"⏰ За 24 ч: {fmt_int(stats['users_1d'])}",
        f"📅 За 7 д:  {fmt_int(stats['users_7d'])}",
        f"⛔ Забанены: {fmt_int(stats['users_banned'])}",

        "",
        "🔗 <b>Ссылки</b> 🔗",
        f"📎 Всего: <b>{fmt_int(stats['links_total'])}</b>",
        f"⏰ За 24 ч: {fmt_int(stats['links_1d'])}",
        f"📅 За 7 д:  {fmt_int(stats['links_7d'])}",

        "",
        "👀 <b>Клики</b> 👀",
        f"🔢 Всего: <b>{fmt_int(stats['clicks_total'])}</b>",
        f"⏰ За 24 ч: {fmt_int(stats['clicks_1d'])}",
        f"📅 За 7 д:  {fmt_int(stats['clicks_7d'])}",

        "",
        "⚡ <b>Активные пользователи за 24 ч</b> (создавали ссылки):",
        *(active_lines or ["— нет активности —"]),
    ])

    with suppress(Exception):
        await callback.answer()

    try:
        if isinstance(admin_home, InlineKeyboardMarkup):
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
        else:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
            with suppress(Exception):
                await callback.message.delete()
    except (TelegramBadRequest, TelegramNetworkError):
        await callback.message.answer(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=admin_home
        )


def _period_bounds(days: int) -> tuple[str, str]:
    now = datetime.utcnow()
    frm = _utc_iso(now - timedelta(days=days))
    to  = _utc_iso(now)
    return frm, to


def _sum_invoices_by_asset(cur, frm_iso: str | None, to_iso: str | None) -> dict[str, dict]:
    """
    Возвращает словарь по активам:
      { "USDT": {"amount": 23.0, "count": 7}, "TRX": {"amount": 20.0, "count": 3}, ... }
    Только статус 'paid'. Если frm/to None — за всё время.
    """
    params = []
    where = "WHERE status = 'paid'"
    if frm_iso and to_iso:
        where += " AND created_at BETWEEN ? AND ?"
        params += [frm_iso, to_iso]

    cur.execute(f"""
        SELECT asset, COALESCE(SUM(amount),0) AS total, COUNT(*) AS cnt
        FROM invoices
        {where}
        GROUP BY asset
        ORDER BY asset
    """, tuple(params))

    res = {}
    for r in cur.fetchall():
        asset = (r["asset"] if hasattr(r, "keys") else r[0]) or ""
        total = float((r["total"] if hasattr(r, "keys") else r[1]) or 0)
        cnt   = int((r["cnt"]   if hasattr(r, "keys") else r[2]) or 0)
        res[asset] = {"amount": total, "count": cnt}
    return res

def _links_count(cur, frm_iso: str | None, to_iso: str | None) -> int:
    if frm_iso and to_iso:
        cur.execute("SELECT COUNT(*) AS c FROM links WHERE created_at BETWEEN ? AND ?", (frm_iso, to_iso))
    else:
        cur.execute("SELECT COUNT(*) AS c FROM links")
    return int(cur.fetchone()["c"] or 0)

@admin_router.callback_query(F.data == "admin_income")
async def admin_income_cb(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    day_from, day_to     = _period_bounds(1)
    week_from, week_to   = _period_bounds(7)
    month_from, month_to = _period_bounds(30)

    conn = get_connection()
    with closing(conn):
        cur = conn.cursor()
        have_invoices = _exists(cur, "invoices")

        if have_invoices:
            day_assets    = _sum_invoices_by_asset(cur, day_from,   day_to)
            week_assets   = _sum_invoices_by_asset(cur, week_from,  week_to)
            month_assets  = _sum_invoices_by_asset(cur, month_from, month_to)
            all_assets    = _sum_invoices_by_asset(cur, None, None)
        else:
            # без invoices: считаем по ссылкам
            day_links    = _links_count(cur, day_from,   day_to)
            week_links   = _links_count(cur, week_from,  week_to)
            month_links  = _links_count(cur, month_from, month_to)
            all_links    = _links_count(cur, None, None)

    title = "💵 <b>Доход за период</b>\n"
    hint  = "🧾 Источник: <i>invoices (paid)</i>\n" if have_invoices else \
            f"🔗 Источник: <i>созданные ссылки × цену</i> (COST_PER_LINK={_fmt_num(COST_PER_LINK)})\n"

    def block_from_assets(header: str, assets: dict[str, dict]) -> list[str]:
        lines = [header]
        if not assets:
            lines.append("— нет данных —")
            return lines
        # суммарное количество платежей (по всем активам)
        total_cnt = sum(v["count"] for v in assets.values())
        lines.append(f"📦 Кол-во платежей: <b>{total_cnt}</b>")
        lines.append("💱 По валютам:")
        for asset, info in assets.items():
            lines.append(f"🪙 {asset}: <b>{_fmt_num(info['amount'])}</b> (🧾 {info['count']})")
        return lines

    def block_from_links(header: str, count_links: int) -> list[str]:
        amount = count_links * float(COST_PER_LINK)
        return [
            header,
            f"🔗 Создано ссылок: <b>{count_links}</b>",
            f"💰 Доход: <b>{_fmt_num(amount)}</b>",
        ]

    text_lines = [title, hint]

    if have_invoices:
        text_lines += [
            "⏰ <b>За сутки</b>",
            *block_from_assets("", day_assets),
            "━━━━━━━━━━━━━━━",
            "📅 <b>За 7 дней</b>",
            *block_from_assets("", week_assets),
            "━━━━━━━━━━━━━━━",
            "🗓 <b>За 30 дней</b>",
            *block_from_assets("", month_assets),
            "━━━━━━━━━━━━━━━",
            "♾ <b>За всё время</b>",
            *block_from_assets("", all_assets),
        ]
    else:
        text_lines += [
            "⏰ <b>За сутки</b>",
            *block_from_links("", day_links),
            "━━━━━━━━━━━━━━━",
            "📅 <b>За 7 дней</b>",
            *block_from_links("", week_links),
            "━━━━━━━━━━━━━━━",
            "🗓 <b>За 30 дней</b>",
            *block_from_links("", month_links),
            "━━━━━━━━━━━━━━━",
            "♾ <b>За всё время</b>",
            *block_from_links("", all_links),
        ]

    text = "\n".join(text_lines)

    with suppress(Exception):
        await callback.answer()

    try:
        if isinstance(admin_home, InlineKeyboardMarkup):
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
        else:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
            with suppress(Exception):
                await callback.message.delete()
    except (TelegramBadRequest, TelegramNetworkError):
        await callback.message.answer(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=admin_home
        )


def _exists(cur, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None

def _fmt_num(x) -> str:
    try:
        return f"{float(x):.3f}".rstrip("0").rstrip(".")
    except Exception:
        return str(x)

def _fmt_dt(s: str) -> str:
    # "YYYY-MM-DD HH:MM:SS" -> "YYYY-MM-DD HH:MM"
    if isinstance(s, str) and " " in s:
        return s[:16]
    return str(s)

def _short_host(u: str) -> str:
    if not u:
        return "-"
    try:
        p = urlsplit(u)
        host = p.netloc or (p.path.split("/")[0] if p.path else "")
        return host or u
    except Exception:
        return u

def _mask_link_id(s: str) -> str:
    if not s:
        return "-"
    s = str(s)
    if len(s) <= 14:
        return s
    return f"{s[:6]}…{s[-4:]}"

# ========== core queries ==========

def _get_activity_24h():
    """
    Возвращает:
      {
        "new_users": int,
        "new_links": int,
        "income_assets": {ASSET: {"amount": float, "count": int}},
        "active_users": [ {id, telegram_id, links_count, last_activity}, ... ],
        "recent_links": [ {link, original_url, created_at, telegram_id}, ... ],
      }
    """
    now = datetime.utcnow()
    since = _utc_iso(now - timedelta(days=1))

    conn = get_connection()
    with closing(conn):
        cur = conn.cursor()

        # Новые пользователи за 24ч
        cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at >= ?", (since,))
        new_users = int(cur.fetchone()["c"] or 0)

        # Создано ссылок за 24ч
        cur.execute("SELECT COUNT(*) AS c FROM links WHERE created_at >= ?", (since,))
        new_links = int(cur.fetchone()["c"] or 0)

        # Доход за 24ч по валютам из invoices (если таблица есть)
        income_assets = {}
        if _exists(cur, "invoices"):
            cur.execute("""
                SELECT UPPER(asset) AS asset, COALESCE(SUM(amount),0) AS total, COUNT(*) AS cnt
                FROM invoices
                WHERE status='paid' AND created_at >= ?
                GROUP BY UPPER(asset)
                ORDER BY asset
            """, (since,))
            for r in cur.fetchall():
                asset = r["asset"]
                total = float(r["total"] or 0)
                cnt   = int(r["cnt"] or 0)
                income_assets[asset] = {"amount": total, "count": cnt}

        # Активные пользователи (создавали ссылки за 24ч)
        cur.execute("""
            SELECT u.id, u.telegram_id, COUNT(l.id) AS links_count, MAX(l.created_at) AS last_activity
            FROM users u
            JOIN links l ON l.user_id = u.id
            WHERE l.created_at >= ?
            GROUP BY u.id, u.telegram_id
            ORDER BY last_activity DESC
        """, (since,))
        active_users = [{
            "id": r["id"],
            "telegram_id": r["telegram_id"],
            "links_count": int(r["links_count"] or 0),
            "last_activity": r["last_activity"],
        } for r in cur.fetchall()]

        # Последние N ссылок (созданные за 24ч)
        N = 10
        cur.execute("""
                    SELECT l.link, l.short_host, l.original_url, l.created_at, u.telegram_id
                    FROM links l
                             JOIN users u ON u.id = l.user_id
                    WHERE l.created_at >= ?
                    ORDER BY l.created_at DESC LIMIT ?
                    """, (since, N))
        recent_links = [{
            "link": r["link"],
            "short_host": r["short_host"],
            "original_url": r["original_url"],
            "created_at": r["created_at"],
            "telegram_id": r["telegram_id"],
        } for r in cur.fetchall()]

        return {
            "new_users": new_users,
            "new_links": new_links,
            "income_assets": income_assets,   # пустой, если нет invoices
            "active_users": active_users,
            "recent_links": recent_links,
        }

# ========== handler ==========

@admin_router.callback_query(F.data == "admin_activity_day")
async def admin_activity_day_cb(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    data = _get_activity_24h()

    # --- сводка ---
    lines = [
        "📅 <b>Активность за 24 часа</b>\n",
        f"👥 Новые пользователи: <b>{data['new_users']}</b>",
        f"🔗 Создано ссылок: <b>{data['new_links']}</b>",
    ]

    # --- доход по валютам (если есть invoices) ---
    if data["income_assets"]:
        lines += [
            "",
            "💵 <b>Доход за 24ч (по валютам)</b>",
            f"🧾 Платежей: <b>{sum(v['count'] for v in data['income_assets'].values())}</b>",
            "💱 Разбивка:",
        ]
        for asset, info in data["income_assets"].items():
            lines.append(f"🪙 {asset}: <b>{_fmt_num(info['amount'])}</b> (🧾 {info['count']})")

    # --- активные пользователи ---
    lines += ["", "⚡ <b>Активные пользователи (создавали ссылки)</b>"]
    if data["active_users"]:
        for u in data["active_users"][:50]:
            lines.append(
                f"🆔 DB:<code>{u['id']}</code> | "
                f"💬 TG:<code>{u['telegram_id']}</code> | "
                f"🔗 {u['links_count']} | "
                f"🕒 {_fmt_dt(u['last_activity'])}"
            )
    else:
        lines.append("— нет активности —")

    # --- последние ссылки (красиво и компактно) ---
    lines += ["", "🧷 <b>Последние ссылки</b>"]
    if data["recent_links"]:
        for i, r in enumerate(data["recent_links"], 1):
            created = _fmt_dt(r["created_at"])
            masked  = _mask_link_id(r["link"])
            host = (r.get("short_host") or "").strip()

            short_url = f"{PREFERRED_SCHEME}://{host}/link/{r['link']}"

            lines.append(
                f"{i}. 🔗 <a href=\"{short_url}\">{masked}</a> | "
                f"💬 TG:<code>{r['telegram_id']}</code> | "
                f"🕒 {created} | 🌐 {host}"
            )
    else:
        lines.append("— пока ничего —")

    text = "\n".join(lines)

    # закрыть «часики» у кнопки
    with suppress(Exception):
        await callback.answer()

    # безопасно показать (edit_text для inline-клавы, иначе answer)
    try:
        if isinstance(admin_home, InlineKeyboardMarkup):
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
        else:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
            with suppress(Exception):
                await callback.message.delete()
    except (TelegramBadRequest, TelegramNetworkError):
        await callback.message.answer(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=admin_home
        )

def _get_activity_7d():
    """
    Возвращает активность за последние 7 дней:
      {
        "new_users": int,
        "new_links": int,
        "income_assets": {ASSET: {"amount": float, "count": int}},
        "active_users": [ {id, telegram_id, links_count, last_activity}, ... ],
        "recent_links": [ {link, original_url, created_at, telegram_id}, ... ],
      }
    """
    now = datetime.utcnow()
    since = _utc_iso(now - timedelta(days=7))

    conn = get_connection()
    with closing(conn):
        cur = conn.cursor()

        # Новые пользователи за 7д
        cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at >= ?", (since,))
        new_users = int(cur.fetchone()["c"] or 0)

        # Создано ссылок за 7д
        cur.execute("SELECT COUNT(*) AS c FROM links WHERE created_at >= ?", (since,))
        new_links = int(cur.fetchone()["c"] or 0)

        # Доход за 7д по валютам (если есть invoices)
        income_assets = {}
        if _exists(cur, "invoices"):
            cur.execute("""
                SELECT UPPER(asset) AS asset, COALESCE(SUM(amount),0) AS total, COUNT(*) AS cnt
                FROM invoices
                WHERE status='paid' AND created_at >= ?
                GROUP BY UPPER(asset)
                ORDER BY asset
            """, (since,))
            for r in cur.fetchall():
                asset = r["asset"]
                total = float(r["total"] or 0)
                cnt   = int(r["cnt"] or 0)
                income_assets[asset] = {"amount": total, "count": cnt}

        # Активные пользователи (создавали ссылки за 7д)
        cur.execute("""
            SELECT u.id, u.telegram_id, COUNT(l.id) AS links_count, MAX(l.created_at) AS last_activity
            FROM users u
            JOIN links l ON l.user_id = u.id
            WHERE l.created_at >= ?
            GROUP BY u.id, u.telegram_id
            ORDER BY last_activity DESC
        """, (since,))
        active_users = [{
            "id": r["id"],
            "telegram_id": r["telegram_id"],
            "links_count": int(r["links_count"] or 0),
            "last_activity": r["last_activity"],
        } for r in cur.fetchall()]

        # Последние ссылки за 7д (возьмём N побольше, например 20)
        N = 20
        cur.execute("""
                    SELECT l.link, l.short_host, l.original_url, l.created_at, u.telegram_id
                    FROM links l
                             JOIN users u ON u.id = l.user_id
                    WHERE l.created_at >= ?
                    ORDER BY l.created_at DESC LIMIT ?
                    """, (since, N))
        recent_links = [{
            "link": r["link"],
            "short_host": r["short_host"],
            "original_url": r["original_url"],
            "created_at": r["created_at"],
            "telegram_id": r["telegram_id"],
        } for r in cur.fetchall()]

        return {
            "new_users": new_users,
            "new_links": new_links,
            "income_assets": income_assets,
            "active_users": active_users,
            "recent_links": recent_links,
        }


# ===== handler (7 days) =====

@admin_router.callback_query(F.data == "admin_activity_week")
async def admin_activity_week_cb(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    data = _get_activity_7d()

    # Сводка
    lines = [
        "📅 <b>Активность за 7 дней</b>\n",
        f"👥 Новые пользователи: <b>{data['new_users']}</b>",
        f"🔗 Создано ссылок: <b>{data['new_links']}</b>",
    ]

    # Доход по валютам (если есть invoices)
    if data["income_assets"]:
        lines += [
            "",
            "💵 <b>Доход за 7 дней (по валютам)</b>",
            f"🧾 Платежей: <b>{sum(v['count'] for v in data['income_assets'].values())}</b>",
            "💱 Разбивка:",
        ]
        for asset, info in data["income_assets"].items():
            lines.append(f"🪙 {asset}: <b>{_fmt_num(info['amount'])}</b> (🧾 {info['count']})")

    # Активные пользователи
    lines += ["", "⚡ <b>Активные пользователи (создавали ссылки)</b>"]
    if data["active_users"]:
        for u in data["active_users"][:50]:
            lines.append(
                f"🆔 DB:<code>{u['id']}</code> | "
                f"💬 TG:<code>{u['telegram_id']}</code> | "
                f"🔗 {u['links_count']} | "
                f"🕒 {_fmt_dt(u['last_activity'])}"
            )
    else:
        lines.append("— нет активности —")

    # Последние ссылки (маска, домен, кликабельный шорт-URL)
    lines += ["", "🧷 <b>Последние ссылки</b>"]
    if data["recent_links"]:
        for i, r in enumerate(data["recent_links"], 1):
            created = _fmt_dt(r["created_at"])
            masked  = _mask_link_id(r["link"])
            host = (r.get("short_host") or "").strip()
            short_url = f"{PREFERRED_SCHEME}://{host}/link/{r['link']}"

            lines.append(
                f"{i}. 🔗 <a href=\"{short_url}\">{masked}</a> | "
                f"💬 TG:<code>{r['telegram_id']}</code> | "
                f"🕒 {created} | 🌐 {host}"
            )
    else:
        lines.append("— пока ничего —")

    text = "\n".join(lines)

    # закрыть «часики» у колбэка
    with suppress(Exception):
        await callback.answer()

    # безопасный вывод (edit_text для inline-клавы, иначе answer)
    try:
        if isinstance(admin_home, InlineKeyboardMarkup):
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
        else:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_home
            )
            with suppress(Exception):
                await callback.message.delete()
    except (TelegramBadRequest, TelegramNetworkError):
        await callback.message.answer(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=admin_home
        )

# --- DB helper: получить все TG ID активных пользователей ---
def get_all_active_telegram_ids() -> list[int]:
    conn = get_connection()
    with closing(conn):
        cur = conn.cursor()
        # если колонки banned может не быть — оставим fallback
        try:
            cur.execute("SELECT telegram_id FROM users WHERE COALESCE(banned,0) = 0")
        except Exception:
            cur.execute("SELECT telegram_id FROM users")
        rows = cur.fetchall()
        return [int(r[0] if not hasattr(r, "keys") else r["telegram_id"]) for r in rows]

# --- клавиатуры ---
def kb_broadcast_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Отправить", callback_data="broadcast_send")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")],
        [InlineKeyboardButton(text="🏠 В админ-меню", callback_data="admin_home")],
    ])

# ====== start flow ======
@admin_router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    with suppress(Exception):
        await callback.answer()

    text = (
        "📣 <b>Рассылка</b>\n\n"
        "Отправь текст сообщения для рассылки (HTML разрешён).\n\n"
        "🔒 Примечание: превью ссылок будет <b>выключено</b> для всех сообщений."
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_home)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=admin_home)

    await state.set_state(WaitForSMTHUser.waiting_text)

# ловим текст рассылки
@admin_router.message(WaitForSMTHUser.waiting_text, F.text)
async def admin_broadcast_preview(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    content = message.text.strip()
    if not content:
        await message.answer("❌ Пустое сообщение. Введи текст рассылки.")
        return

    # сохраним текст в FSM
    await state.update_data(broadcast_text=content)

    # покажем предпросмотр + кнопки
    preview = "🖼 <b>Предпросмотр рассылки</b>:\n\n" + content
    await message.answer(preview, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb_broadcast_confirm())

# отмена
@admin_router.callback_query(F.data == "broadcast_cancel")
async def admin_broadcast_cancel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.clear()
    with suppress(Exception):
        await callback.answer("Отменено")
    # вернёмся в меню
    try:
        await callback.message.edit_text("⚙️ Админ-панель", reply_markup=admin_home)
    except Exception:
        await callback.message.answer("⚙️ Админ-панель", reply_markup=admin_home)

# подтверждение → отправка
@admin_router.callback_query(F.data == "broadcast_send")
async def admin_broadcast_send(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    data = await state.get_data()
    content = data.get("broadcast_text")
    if not content:
        await callback.answer("Нет текста", show_alert=True)
        return

    with suppress(Exception):
        await callback.answer("Отправляю…")

    # заберём tg ids
    tg_ids = get_all_active_telegram_ids()
    total = len(tg_ids)
    delivered = 0
    failed = 0

    # покажем стартовый прогресс
    try:
        progress_msg = await callback.message.edit_text(
            f"📣 Рассылка начата\n"
            f"Всего получателей: <b>{total}</b>\n\n"
            f"Отправлено: 0\nОшибок: 0",
            parse_mode="HTML",
            reply_markup=admin_home
        )
    except Exception:
        progress_msg = await callback.message.answer(
            f"📣 Рассылка начата\nВсего получателей: <b>{total}</b>\n\nОтправлено: 0\nОшибок: 0",
            parse_mode="HTML",
            reply_markup=admin_home
        )

    # отправка последовательно с «бережной» скоростью
    # (простая и безопасная — без сложной конкуренции)
    for i, chat_id in enumerate(tg_ids, 1):
        try:
            await callback.bot.send_message(
                chat_id,
                content,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            delivered += 1
        except TelegramRetryAfter as e:
            # если сервер просит подождать
            await asyncio.sleep(e.retry_after + 1)
            # повторим одну попытку
            try:
                await callback.bot.send_message(
                    chat_id,
                    content,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                delivered += 1
            except Exception:
                failed += 1
        except (TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError, Exception):
            # заблокировал бота / некорректный чат / и т.п.
            failed += 1

        # иногда обновляем прогресс (не каждый раз, чтобы не спамить)
        if i % 20 == 0 or i == total:
            with suppress(Exception):
                await progress_msg.edit_text(
                    f"📣 Рассылка идёт…\n"
                    f"Всего получателей: <b>{total}</b>\n\n"
                    f"Отправлено: <b>{delivered}</b>\n"
                    f"Ошибок: <b>{failed}</b>",
                    parse_mode="HTML",
                    reply_markup=admin_home
                )

        # базовая пауза против флуд-лимита
        await asyncio.sleep(0.03)

    # финальный отчёт
    with suppress(Exception):
        await state.clear()

    summary = (
        "✅ <b>Рассылка завершена</b>\n\n"
        f"Всего получателей: <b>{total}</b>\n"
        f"Доставлено: <b>{delivered}</b>\n"
        f"Ошибок: <b>{failed}</b>"
    )
    try:
        await progress_msg.edit_text(summary, parse_mode="HTML", reply_markup=admin_home)
    except Exception:
        await callback.message.answer(summary, parse_mode="HTML", reply_markup=admin_home)


def change_count_clicks_safe(*, link_or_url: str, delta: int) -> tuple[bool, str, int | None]:
    """
    Меняет max_clicks на delta (может быть отрицательным).
    Результат не опустится ниже 0.
    Принимает либо slug, либо полный короткий URL вида https://your.host/link/<slug>.

    Возвращает: (ok, message, new_max_clicks | None)
    """
    slug = _extract_slug(link_or_url)
    if not slug:
        return False, "Не удалось распознать ссылку. Пришлите короткую ссылку или её slug.", None

    conn = get_connection()
    cur = conn.cursor()
    try:
        # найдём запись
        cur.execute("SELECT id, link, max_clicks FROM links WHERE link = ?", (slug,))
        row = cur.fetchone()
        if not row:
            return False, "Ссылка не найдена.", None

        # обновляем с защитой от отрицательного результата
        cur.execute(
            "UPDATE links SET max_clicks = MAX(max_clicks + ?, 0) WHERE link = ?",
            (int(delta), slug)
        )
        conn.commit()

        # читаем новое значение
        cur.execute("SELECT max_clicks FROM links WHERE link = ?", (slug,))
        new_row = cur.fetchone()
        new_val = int(new_row["max_clicks"]) if new_row else None
        return True, "Готово.", new_val
    finally:
        conn.close()


def _extract_slug(s: str) -> str | None:
    """
    Извлекает slug из:
      - 'abcdef123'
      - 'https://host/link/abcdef123'
      - 'http://host/link/abcdef123?x=1'
    Если не удалось — None.
    """
    s = (s or "").strip()
    if not s:
        return None

    # если это полный URL
    if "://" in s:
        try:
            from urllib.parse import urlparse
            p = urlparse(s)
            # ищем последний сегмент пути
            path = (p.path or "").rstrip("/")
            if not path:
                return None
            slug = path.split("/")[-1]
            return slug or None
        except Exception:
            return None

    # иначе считаем, что прислали сразу slug
    return s


def _extract_slug_strict(s: str) -> str | None:
    """
    Пытаемся вытащить slug:
      - 'AbC123' (чистый slug)
      - 'https://host.tld/link/AbC123'
      - 'http://host.tld/link/AbC123?foo=1#bar'
    Возвращает slug или None.
    """
    s = (s or "").strip()
    if not s:
        return None

    if "://" in s:
        try:
            p = urlparse(s)
            path = (p.path or "").rstrip("/")
            if not path:
                return None
            # ожидаем формат '/link/<slug>'
            parts = path.split("/")
            if len(parts) >= 3 and parts[-2] == "link":
                return parts[-1] or None
            # если формат иной — не считаем валидным
            return None
        except Exception:
            return None

    # если нет протокола — считаем, что это slug (без пробелов и '/')
    if "/" in s or " " in s or ":" in s:
        return None
    return s


def _build_short_by_slug(slug: str) -> str:
    """
    Достаём short_host из БД по slug и собираем короткую ссылку.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT short_host FROM links WHERE link = ?", (slug,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Slug '{slug}' не найден в базе")

        host = (row["short_host"] if hasattr(row, "keys") else row[0]).strip()
        return f"{PREFERRED_SCHEME}://{host}/link/{slug}"
    finally:
        conn.close()



@admin_router.callback_query(F.data == "clicks_up")
async def admin_clicks_up(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав", show_alert=True)
        return

    text = (
        "🔧 <b>Изменение лимита кликов</b>\n\n"
        "Отправьте <b>короткую ссылку или slug</b> и величину изменения.\n"
        "Можно указывать отрицательное число (уменьшение).\n\n"
        "Формат: <code>Короткая_ссылка_ИЛИ_slug:Число</code>\n"
        "Примеры:\n"
        f"• <code>{PREFERRED_SCHEME}://your.host/link/AbC123:10</code> — прибавить 10\n"
        "• <code>AbC123:-2</code> — отнять 2\n\n"
        "❗️Если укажете слишком большое отрицательное число — результат станет 0 (не уйдёт в минус)."
    )
    with suppress(Exception):
        await callback.answer()

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_home)
    await state.set_state(WaitForSMTHUser.waiting_count_clicks)


@admin_router.message(WaitForSMTHUser.waiting_count_clicks, F.text)
async def process_change_clicks(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет прав")
        return

    raw = (message.text or "").strip()

    # парсим по последнему двоеточию, чтобы не ломалось на "https://"
    if ":" not in raw:
        await message.answer(
            "❌ Формат: <code>ссылка_или_slug:число</code>\n"
            f"Пример: <code>{PREFERRED_SCHEME}://your.host/link/AbC123:5</code> или <code>AbC123:5</code>",
            parse_mode="HTML"
        )
        return

    url_part, delta_part = raw.rsplit(":", 1)
    url_part = url_part.strip()
    delta_part = delta_part.strip()

    # валидация числа
    try:
        delta = int(delta_part)
    except ValueError:
        await message.answer("❌ Введите целое число изменения (например, 3 или -2).")
        return

    # ограничим диапазон
    if not (-100000 <= delta <= 100000):
        await message.answer("⚠️ Слишком большое изменение. Разрешено от -100000 до 100000.")
        return

    # извлекаем slug (поддерживаем и полную короткую ссылку, и чистый slug)
    slug = _extract_slug_strict(url_part)
    if not slug:
        await message.answer(
            "❌ Не удалось распознать короткую ссылку/slug.\n"
            f"Пример: <code>{PREFERRED_SCHEME}://your.host/link/AbC123:5</code> или <code>AbC123:5</code>",
            parse_mode="HTML"
        )
        return

    # меняем лимит
    ok, msg, new_val = change_count_clicks_safe(link_or_url=slug, delta=delta)
    if not ok:
        await message.answer(f"❌ {msg}")
        return

    final_short = _build_short_by_slug(slug)

    await message.answer(
        f"✅ {msg}\n"
        f"🔗 Ссылка: <code>{final_short}</code>\n"
        f"Δ изменение: <b>{delta}</b>\n"
        f"📌 Новый лимит: <b>{new_val}</b>",
        parse_mode="HTML",
        reply_markup=admin_home
    )
    await state.clear()







