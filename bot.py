import random
import re
import secrets
import sqlite3
import string
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
from typing import Optional, Tuple
from glaz_boga.handlers import dyx_router
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import MessageEntityType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from cryptography.fernet import Fernet
import time
from payments.nowpayments.service import create_invoice as np_create_invoice, get_payment_status as np_get_payment_status
from admin.admin_hanlders import admin_router
from config import BOT_TOKEN, SECRET_KEY, SERVER_URL, DOMAIN, PREFERRED_SCHEME, CHANNEL_USERNAME, CHANNEL_INVITE_LINK, CHANNEL_ID
from database.db_api import (
    get_connection,
    add_user,
    get_user,
    get_balance,
    add_balance,
    minus_balance,
    get_links,            # ВАЖНО: ожидается, что принимает users.id (внутренний)
)
from payments.cryptopay.rates import get_rate_usdt
from payments.cryptopay.service import check_and_credit, create_topup_invoice
# Импорты (убедись, что они есть сверху файла)
import json
from payments.nowpayments.service import (
    list_payments_by_invoice as np_list_by_iid,
    get_payment_status as np_get_status,
)
from payments.nowpayments.repository import (
    get_nowp_invoice,
    update_nowp_payment_details, mark_nowp_paid)
from aiogram.enums import ChatMemberStatus
import contextlib
import ipaddress
import os
from uuid import uuid4
from aiogram.utils.markdown import code
from html import escape
  # ---- Инициализация ----

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
cipher = Fernet(SECRET_KEY)

dp.include_router(admin_router)
dp.include_router(dyx_router)

async def _get_username(user_id: int) -> str:
    try:
        user = await bot.get_chat(user_id)
        if user.username:
            return f"@{user.username}"
        elif user.full_name:
            return user.full_name
        else:
            return "—"
    except Exception:
        return "—"

# ---- Утилиты форматирования ----


def _fmt_dec(d: Decimal) -> str:
    """
    Красивый вывод Decimal:
    - всегда 2 знака после запятой
    - вторая цифра округляется вверх
    """
    d_q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d_q:.2f}"

# ---- Клавиатуры ----

main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Личный кабинет", callback_data="user_panel"),
            InlineKeyboardButton(text="🔗 Мои ссылки", callback_data="my_links"),
        ],
        [
            InlineKeyboardButton(text="🛠 Сгенерировать ссылку", callback_data="generate_link"),
            InlineKeyboardButton(text="👁️ Пробив личности", callback_data="check_person_data")
        ],
        [InlineKeyboardButton(text="✉️ Проверить утечки паролей по почте", callback_data="check_email_leak")],
        [InlineKeyboardButton(text="\U0001F4F9️ Проверить IP на уязвимые камеры", callback_data="scan_cam")],
    ]
)

back_to_menu = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]]
)

user_panel_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")],
    ]
)

payment_method_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🤖 CryptoBot", callback_data="paymethod:cryptobot")],
        [InlineKeyboardButton(text="🌐 NowPayment (150 видов крипты)",
                              callback_data="paymethod:NowPayments")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="user_panel")],
    ]
)

def topup_or_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")],
        ]
    )

def amounts_kb(asset: str, per_row: int = 2) -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton(text=f"💳 {a} {asset}", callback_data=f"amount:{asset}:{a}")
        for a in TOPUP_AMOUNTS
        if Decimal(a) >= MIN_AMOUNT.get(asset, Decimal("0"))
    ]
    rows = [btns[i : i + per_row] for i in range(0, len(btns), per_row)]
    rows.append(
        [
            InlineKeyboardButton(text="✍️ Другая сумма", callback_data=f"amount_custom:{asset}"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="paymethod:cryptobot"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def crypto_assets_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, a in enumerate(SUPPORTED_ASSETS, start=1):
        row.append(InlineKeyboardButton(text=a, callback_data=f"asset:{a}"))
        if i % 3 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="paymethod:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---- Константы пополнений ----

SUPPORTED_ASSETS = ("USDT", "USDC", "TON", "BTC", "ETH", "LTC", "BNB", "TRX")

MIN_AMOUNT = {
    "USDT": Decimal("1"),
    "USDC": Decimal("1"),
    "TON":  Decimal("0.3"),
    "BTC":  Decimal("0.00005"),
    "ETH":  Decimal("0.0007"),
    "LTC":  Decimal("0.01"),
    "BNB":  Decimal("0.005"),
    "TRX":  Decimal("5"),
}

TOPUP_AMOUNTS = (1, 5, 10, 25, 50)

# ---- /start ----



SUB_REQUIRED_TEXT = (
    "👋 Добро пожаловать!\n\n"
    "Чтобы пользоваться ботом, подпишись на наш официальный канал.\n"
)

def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📣 Подписаться", url=CHANNEL_INVITE_LINK)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]
    ])

async def _is_subscribed(bot, user_id: int) -> bool:
    chat_ref = CHANNEL_ID if CHANNEL_ID else CHANNEL_USERNAME
    try:
        member = await bot.get_chat_member(chat_id=chat_ref, user_id=user_id)
    except Exception:
        return False

    return member.status in (
        ChatMemberStatus.CREATOR,  # владелец канала
        ChatMemberStatus.ADMINISTRATOR,  # админ
        ChatMemberStatus.MEMBER  # обычный подписчик
    )


# === /start ===
@dp.message(Command("start"))
async def start(msg: types.Message):
    add_user(msg.from_user.id)  # создаст, если нет
    user = get_user(msg.from_user.id)
    if user and user.get("banned"):
        await msg.reply("⛔ ВЫ ЗАБАНЕНЫ ⛔")
        return

    # проверка подписки
    if not await _is_subscribed(msg.bot, msg.from_user.id):
        await msg.answer(SUB_REQUIRED_TEXT, reply_markup=sub_keyboard(), disable_web_page_preview=True)
        return

    # если подписан — показываем меню
    await msg.reply(
        f"👋 {msg.from_user.first_name} 👋\n\nГлавное меню ⬇️",
        reply_markup=main_menu,
    )


# === проверка кнопкой ===
@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    ok = await _is_subscribed(callback.bot, callback.from_user.id)
    if ok:
        with contextlib.suppress(Exception):
            await callback.answer("Подписка подтверждена ✅", show_alert=False)
        try:
            await callback.message.edit_text("✅ Спасибо! Подписка подтверждена. Открываю меню…",
                                             reply_markup=main_menu)
        except Exception:
            await callback.message.answer("✅ Спасибо! Подписка подтверждена. Открываю меню…",
                                          reply_markup=main_menu)
    else:
        await callback.answer("Похоже, вы ещё не подписались 🤔", show_alert=True)



# --- Состояния генерации ссылки ---
class LinkStates(StatesGroup):
    choosing_plan = State()       # новый шаг: выбор кол-ва переходов
    waiting_for_url = State()

# --- Тарифы за количество переходов ---
from decimal import Decimal

LINK_PLANS: dict[int, Decimal] = {
    1: Decimal("1.0"),
    2: Decimal("1.5"),
    3: Decimal("2.0"),
    5: Decimal("3.0"),
}

def _plan_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="1 переход — 1 USDT",   callback_data="linkplan:1"),
            InlineKeyboardButton(text="2 перехода — 1.5 USDT", callback_data="linkplan:2"),
        ],
        [
            InlineKeyboardButton(text="3 перехода — 2 USDT",  callback_data="linkplan:3"),
            InlineKeyboardButton(text="5 переходов — 3 USDT", callback_data="linkplan:5"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.callback_query(lambda c: c.data == "generate_link")
async def generate_link_callback(callback: types.CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    if user and user.get("banned"):
        await callback.message.answer("⛔ ВЫ ЗАБАНЕНЫ ⛔")
        return

    bal = Decimal(str(get_balance(callback.from_user.id) or 0))
    text = (
        "🔗 <b>Сгенерировать короткую ссылку</b>\n\n"
        "Выбери количество переходов для этой ссылки:\n\n"
        "👉 1 переход — 1 USDT\n"
        "👉 2 перехода — 1.5 USDT\n"
        "👉 3 перехода — 2 USDT\n"
        "👉 5 переходов — 3 USDT\n\n"
        f"💰 Текущий баланс: {_fmt_dec(bal)} USDT"
    )
    await callback.message.edit_text(text, reply_markup=_plan_keyboard(), parse_mode="HTML")
    await state.clear()
    await state.set_state(LinkStates.choosing_plan)
    await callback.answer()

MAX_URL_LEN = 2048
ALLOWED_SCHEMES = {"http", "https"}

def _extract_url_from_message(msg: types.Message) -> Optional[str]:
    if msg.entities:
        for e in msg.entities:
            if e.type == MessageEntityType.TEXT_LINK:
                return e.url
    if msg.entities:
        for e in msg.entities:
            if e.type == MessageEntityType.URL:
                return msg.text[e.offset : e.offset + e.length]
    txt = (msg.text or "").strip()
    return txt or None

from urllib.parse import urlsplit, urlunsplit

def _validate_and_normalize_url(url: str) -> tuple[bool, Optional[str], Optional[str]]:
    if not url:
        return False, None, (
            "❌ Пустая строка.\n"
            "✍️ Пришли ссылку вида: https://example.com\n\n"
            "🔄 Нажми снова «🛠 Сгенерировать ссылку»"
        )
    url = url.strip()
    if len(url) > MAX_URL_LEN:
        return False, None, (
            f"⚠️ Ссылка слишком длинная (>{MAX_URL_LEN}).\n\n"
            "🔄 Нажми снова «🛠 Сгенерировать ссылку»"
        )

    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return False, None, (
            "🚫 Поддерживаются только http/https.\n"
            "Пример: https://example.com\n\n"
            "🔄 Нажми снова «🛠 Сгенерировать ссылку»"
        )
    if not parts.netloc:
        return False, None, (
            "🌐 У ссылки отсутствует домен.\n"
            "Пример: https://example.com\n\n"
            "🔄 Нажми снова «🛠 Сгенерировать ссылку»"
        )

    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "", parts.query or "", "")
    )
    return True, normalized, None

# ---- Генерация slug и сохранение ----

ALPHABET62 = string.ascii_letters + string.digits

def _encode_base62(n: int) -> str:
    if n == 0:
        return ALPHABET62[0]
    s, base = [], len(ALPHABET62)
    while n:
        n, r = divmod(n, base)
        s.append(ALPHABET62[r])
    return "".join(reversed(s))

def _make_realistic_slug(link_id: int, noise_len: int = 8) -> str:
    core = _encode_base62(link_id)
    noise = "".join(secrets.choice(ALPHABET62) for _ in range(noise_len))
    return core + noise

import re, secrets, string
from urllib.parse import urlsplit

DOMAIN = "vrf.lat"  # твой основной домен

def _make_short_host(original_url: str, noise_len: int = 6) -> str:
    netloc = urlsplit(original_url).netloc.lower()

    # превращаем все точки и недопустимые символы в дефисы
    label = netloc.replace(".", "-")
    label = re.sub(r"[^a-z0-9-]", "-", label)
    label = re.sub(r"-+", "-", label).strip("-")

    # ограничение 63 символа на DNS-метку — режем основу
    base_max = 63 - 1 - noise_len  # дефис + шум
    base = label[:max(1, base_max)]

    noise = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(noise_len))
    one_level_label = f"{base}-{noise}".strip("-")[:63]

    return f"{one_level_label}.{DOMAIN}"

def _save_link_with_slug(
    original_url: str,
    user_id: int,
    max_clicks: int = 1,
    short_host: str | None = None,
) -> Optional[str]:
    """
    Сохраняет ссылку; если short_host не передан — сгенерирует.
    Возвращает slug или None.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cur.fetchone():
            return None

        # создаём черновую запись без slug/short_host
        cur.execute(
            "INSERT INTO links (user_id, original_url, max_clicks, link, short_host) VALUES (?, ?, ?, ?, ?)",
            (user_id, original_url, max_clicks, "", ""),
        )
        link_id = cur.lastrowid

        for _ in range(10):
            slug = _make_realistic_slug(link_id, noise_len=random.randint(8, 15))
            host = short_host or _make_short_host(original_url, noise_len=random.randint(5, 8))
            try:
                cur.execute(
                    "UPDATE links SET link = ?, short_host = ? WHERE id = ?",
                    (slug, host, link_id),
                )
                conn.commit()
                return slug
            except sqlite3.IntegrityError:
                # коллизия slug или short_host — пробуем ещё раз
                continue

        # если не удалось — откатываем
        cur.execute("DELETE FROM links WHERE id = ?", (link_id,))
        conn.commit()
        return None
    except Exception:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()

import re
from urllib.parse import urlsplit

_dns_allowed = re.compile(r"[a-z0-9-]")

def _safe_label(s: str, *, max_len: int = 30) -> str:
    """
    Делает строку безопасной для DNS-лейбла:
    - нижний регистр
    - заменяем всё не [a-z0-9-] на '-'
    - сжимаем подряд идущие '-' до одного
    - обрезаем до max_len
    - убираем ведущие/конечные '-'
    - если пусто — 'x'
    """
    s = (s or "").lower()
    s = "".join(ch if _dns_allowed.match(ch) else "-" for ch in s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "x"
    if len(s) > max_len:
        s = s[:max_len].rstrip("-") or "x"
    return s

_ALNUM = string.ascii_lowercase + string.digits

def _noise_label(groups: int = 3, group_len: int = 3) -> str:
    """
    Делает лейбл вида 'a1f-b9k-2xq' (только [a-z0-9-]),
    чтобы он выглядел естественно и проходил DNS.
    """
    parts = [
        "".join(secrets.choice(_ALNUM) for _ in range(group_len))
        for _ in range(groups)
    ]
    return "-".join(parts)

def _build_host_from_url(url: str, base_domain: str = DOMAIN) -> str:
    parts = urlsplit(url)

    host_labels = [_safe_label(x, max_len=32) for x in parts.netloc.split(".") if x]

    path_segments = [seg for seg in parts.path.split("/") if seg]
    extra_labels = []
    if path_segments:
        extra_labels.append(_safe_label(path_segments[0], max_len=32))
    if len(path_segments) > 1:
        extra_labels.append(_safe_label(path_segments[1], max_len=48))

    # 👇 новый «шумовой» лейбл с дефисами
    noise = _noise_label(groups=3, group_len=3)  # например: 'v4m-k1a-9qz'

    labels = host_labels + extra_labels + [noise, base_domain]
    host = ".".join(labels)

    if len(host) > 253:
        # урезаем extra_labels, затем при необходимости подрезаем лейблы
        while len(host) > 253 and extra_labels:
            extra_labels.pop()
            labels = host_labels + extra_labels + [noise, base_domain]
            host = ".".join(labels)
        if len(host) > 253:
            trimmed = []
            for lb in labels:
                if len(host) <= 253:
                    trimmed.append(lb); continue
                if lb in (base_domain, noise):
                    trimmed.append(lb); continue
                cut = lb[:max(1, len(lb) - (len(host) - 253))]
                cut = cut.rstrip("-") or "x"
                trimmed.append(cut)
                host = ".".join(trimmed + labels[len(trimmed):])
            host = ".".join(trimmed)

    return host

@dp.callback_query(lambda c: c.data.startswith("linkplan:"))
async def choose_link_plan(callback: types.CallbackQuery, state: FSMContext):
    try:
        max_clicks = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.message.answer("❌ Некорректный выбор плана.")
        await callback.answer()
        return

    if max_clicks not in LINK_PLANS:
        await callback.message.answer("❌ Такой план недоступен.")
        await callback.answer()
        return

    cost = LINK_PLANS[max_clicks]
    bal = Decimal(str(get_balance(callback.from_user.id) or 0))

    if bal < cost:
        need = (cost - bal).quantize(Decimal("0.01"))
        await callback.message.edit_text(
            "💳 Недостаточно средств для выбранной услуги.\n\n"
            f"📌 План: {max_clicks} переход(а/ов) — {_fmt_dec(cost)} USDT\n"
            f"🪙 Баланс: {_fmt_dec(bal)} USDT\n"
            f"💸 Не хватает: {_fmt_dec(need)} USDT\n\n"
            "Пополните баланс и попробуйте снова.",
            reply_markup=topup_or_back_kb(),
        )
        await callback.answer()
        return

    # баланс хватает — просим ссылку и запоминаем выбранный план
    await state.update_data(max_clicks=max_clicks, cost=str(cost))
    await state.set_state(LinkStates.waiting_for_url)

    await callback.message.edit_text(
        f"✍️ Отправь ссылку, которую нужно сократить.\n\n"
        f"📌 План: {max_clicks} переход(а/ов)\n"
        f"💰 Стоимость: {_fmt_dec(cost)} USDT (будет списана при создании)\n\n"
        "Пример: https://example.com/page?x=1",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(LinkStates.waiting_for_url)
async def handle_url(msg: types.Message, state: FSMContext):
    # 1) достаём выбранный план
    data = await state.get_data()
    try:
        max_clicks = int(data.get("max_clicks"))
        cost = Decimal(str(data.get("cost")))
    except Exception:
        await msg.reply("⚠️ Сессия выбора плана потеряна. Нажми «🛠 Сгенерировать ссылку» ещё раз.")
        await state.clear()
        return

    # 2) валидируем URL
    candidate = _extract_url_from_message(msg)
    ok, original_url, err = _validate_and_normalize_url(candidate)
    if not ok:
        await msg.reply(f"❌ Некорректная ссылка:\n\n{err}")
        return

    # 3) генерим поддомен (с шумом) ОДИН РАЗ и будем хранить его в БД
    # функция _build_host_from_url должна добавлять шум с дефисами, например:
    # www.youtube.com.watch-abc-12d-9kq.vrf.lat
    try:
        short_host = _build_host_from_url(original_url, DOMAIN)
    except Exception as e:
        await msg.reply(f"❌ Не удалось сформировать короткий хост: {e}")
        await state.clear()
        return

    # 4) списываем средства (Decimal)
    ok_spend, new_balance, _ = minus_balance(msg.from_user.id, cost)
    if not ok_spend:
        bal_txt = _fmt_dec(Decimal(str(new_balance))) if new_balance is not None else "0"
        need = (cost - Decimal(str(new_balance or 0))).quantize(Decimal("0.01")) if new_balance is not None else cost
        await msg.reply(
            "💳 Недостаточно средств для выбранной услуги..\n\n"
            f"🪙 Баланс: {bal_txt} USDT\n"
            f"💸 Нужно: {_fmt_dec(cost)} USDT (не хватает {_fmt_dec(need)})",
            reply_markup=topup_or_back_kb()
        )
        await state.clear()
        return

    # 5) создаём запись со своим max_clicks и СОХРАНЯЕМ short_host в БД
    db_user = get_user(msg.from_user.id)  # должен вернуть словарь с "id" (внутренний users.id)
    if not db_user or "id" not in db_user:
        # откат списания в случае проблемы
        add_balance(msg.from_user.id, float(cost))
        await msg.reply("❌ Ошибка: пользователь не найден. Попробуйте снова.")
        await state.clear()
        return

    # ВАЖНО: _save_link_with_slug должна принимать и сохранять short_host в колонку links.short_host
    # (ALTER TABLE links ADD COLUMN short_host TEXT; — если ещё не добавлена)
    slug = _save_link_with_slug(
        original_url=original_url,
        user_id=db_user["id"],
        max_clicks=max_clicks,
        short_host=short_host,          # 👈 сохраняем в БД
    )
    if not slug:
        # откат списания, если не удалось сохранить
        add_balance(msg.from_user.id, float(cost))
        await msg.reply("❌ Не удалось сохранить ссылку. Средства возвращены. Попробуйте ещё раз.")
        await state.clear()
        return

    # 6) ответ пользователю
    short_link = f"{PREFERRED_SCHEME}://{short_host}/link/{slug}"
    left = (new_balance or Decimal("0")).quantize(Decimal("0.01"))
    await msg.reply(
        f"✅ Готово!\n"
        f"🔗 <code>{short_link}</code> 🔗\n"
        f"👀 Лимит переходов: <b>{max_clicks}</b>\n"
        f"💰 Списано: <b>{_fmt_dec(cost)} USDT</b>\n"
        f"💼 Баланс: {left} USDT",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await state.clear()



# ---- Мои ссылки + пагинация ----

LINKS_PER_PAGE = 5

def paginate_links(links: list[dict], page: int) -> list[dict]:
    start = page * LINKS_PER_PAGE
    return links[start : start + LINKS_PER_PAGE]

def build_links_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_links:{page-1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"my_links:{page+1}"))
    row.append(InlineKeyboardButton(text="🏠 Меню", callback_data="back_to_menu"))
    return InlineKeyboardMarkup(inline_keyboard=[row])

@dp.callback_query(lambda c: c.data.startswith("my_links"))
async def my_links_callback(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    if not user:
        await callback.message.answer("⚠️ Пользователь не найден в БД")
        return
    if user.get("banned"):
        await callback.message.answer("⛔ ВЫ ЗАБАНЕНЫ ⛔")
        return

    # номер страницы
    try:
        page = int(callback.data.split(":", 1)[1]) if ":" in callback.data else 0
    except Exception:
        page = 0
    if page < 0:
        page = 0

    links = get_links(user["telegram_id"])  # должен возвращать список словарей с полями: original_url, link (slug), short_host, clicks, max_clicks, created_at
    if not links:
        await callback.message.edit_text("— Ссылок пока нет —", reply_markup=back_to_menu)
        return

    # пагинация
    total_pages = max((len(links) + LINKS_PER_PAGE - 1) // LINKS_PER_PAGE, 1)
    if page >= total_pages:
        page = total_pages - 1

    start = page * LINKS_PER_PAGE
    end = start + LINKS_PER_PAGE
    page_links = links[start:end]

    # сборка текста
    lines = [f"🔗 Твои ссылки (стр. {page + 1}/{total_pages}):", ""]
    for l in page_links:
        clicks = int(l.get("clicks", 0) or 0)

        # max_clicks может прийти как int/str/None/Decimal — приведём к int с запасом
        raw_mc = l.get("max_clicks", 1)
        try:
            max_clicks = int(raw_mc) if raw_mc is not None else 1
        except (TypeError, ValueError):
            max_clicks = 1
        if max_clicks < 1:
            max_clicks = 1

        # статус по лимиту
        status = "🟢" if clicks < max_clicks else "🔴"

        # короткий линк — через short_host, fallback на SERVER_URL
        slug = l.get("link", "") or ""
        short_host = (l.get("short_host") or "").strip()
        short_url = f"https://{short_host}/link/{slug}"

        # экранируем для HTML
        orig = escape(l.get("original_url", "N/A"))
        short_esc = escape(short_url)
        created = escape(str(l.get("created_at", "N/A")))

        lines += [
            f"🌍 Оригинальная: {orig}",
            f"➡️ Короткая: <code>{short_esc}</code>",
            f"👀 Переходов: {clicks}/{max_clicks} {status}",
            f"🕒 Создана: {created} UTC",
            ""
        ]

    text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=build_links_keyboard(page, total_pages),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ---- Навигация ----

@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f"👋 {callback.from_user.first_name} 👋\n\nГлавное меню ⬇️", reply_markup=main_menu
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("user_panel"))
async def user_panel_callback(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    if user and user.get("banned"):
        await callback.message.answer("⛔ ВЫ ЗАБАНЕНЫ ⛔")
        return

    bal = Decimal(str(user.get("balance", 0)))
    await callback.message.edit_text(
        "👤 Личный кабинет\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        f"🆔 Telegram ID: {callback.from_user.id}\n"
        f"💰 Баланс: {_fmt_dec(bal)} USDT\n"
        f"🕘 Регистрация: {user.get('created_at', 'N/A')} UTC\n"
        "➖➖➖➖➖➖➖➖➖➖",
        reply_markup=user_panel_kb,
    )

# ---- Пополнение через CryptoBot ----

class TopUpStates(StatesGroup):
    waiting_amount = State()

@dp.callback_query(lambda c: c.data == "topup")
async def topup_start(callback: types.CallbackQuery):
    await callback.message.edit_text("Выберите способ оплаты:", reply_markup=payment_method_kb)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "paymethod:back")
async def paymethod_back(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Выберите способ оплаты:", reply_markup=payment_method_kb)
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data == "paymethod:cryptobot")
async def paymethod_cryptobot(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Выберите криптовалюту:", reply_markup=crypto_assets_kb())
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("asset:"))
async def asset_chosen(callback: types.CallbackQuery):
    asset = callback.data.split(":", 1)[1]
    if asset not in SUPPORTED_ASSETS:
        await callback.message.edit_text("❌ Эта монета не поддерживается.", reply_markup=payment_method_kb)
        await callback.answer()
        return

    min_amt = MIN_AMOUNT.get(asset, Decimal("0"))
    await callback.message.edit_text(
        f"Выберите сумму для <b>{asset}</b>:\n"
        f"🔹 Минимум: <b>{_fmt_dec(min_amt)} {asset}</b>",
        reply_markup=amounts_kb(asset),
        parse_mode="HTML",
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("amount:"))
async def amount_fixed(callback: types.CallbackQuery):
    _, asset, val = callback.data.split(":")
    try:
        amount = Decimal(val)
        if amount <= 0:
            raise ValueError
    except Exception:
        await callback.message.answer("❌ Неверная сумма. Попробуйте снова.")
        await callback.answer()
        return

    min_amt = MIN_AMOUNT.get(asset, Decimal("0"))
    if amount < min_amt:
        await callback.message.edit_text(
            f"⚠️ Сумма слишком мала для <b>{asset}</b>.\n"
            f"Минимальная сумма: <b>{_fmt_dec(min_amt)} {asset}</b>\n\n"
            "Выберите сумму:",
            reply_markup=amounts_kb(asset),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        invoice_id, pay_url = await create_topup_invoice(
            user_id=callback.from_user.id, amount=float(amount), asset=asset
        )
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось создать счёт: {e}")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"checkinv:{invoice_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="paymethod:cryptobot")],
        ]
    )
    await callback.message.edit_text(
        f"🧾 Счёт создан: <b>{_fmt_dec(amount)} {asset}</b>\n"
        f"ID: <code>{invoice_id}</code>\n"
        f"Откройте оплату, затем нажмите «Проверить оплату».",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("amount_custom:"))
async def amount_custom(callback: types.CallbackQuery, state: FSMContext):
    asset = callback.data.split(":", 1)[1]
    await state.update_data(asset=asset)
    await state.set_state(TopUpStates.waiting_amount)

    min_amt = MIN_AMOUNT.get(asset, Decimal("0"))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="paymethod:cryptobot")]])
    await callback.message.edit_text(
        f"Введите сумму в <b>{asset}</b> (минимум <b>{_fmt_dec(min_amt)} {asset}</b>):",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()

@dp.message(TopUpStates.waiting_amount)
async def amount_entered(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    asset = data.get("asset", "USDT")
    raw = (msg.text or "").strip().replace(",", ".")

    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise ValueError
    except Exception:
        await msg.reply("❌ Неверная сумма. Введите положительное число (например, 12 или 12.34).")
        return

    min_amt = MIN_AMOUNT.get(asset, Decimal("0"))
    if amount < min_amt:
        await msg.reply(
            f"⚠️ Сумма слишком мала для <b>{asset}</b>.\n"
            f"Минимальная сумма: <b>{_fmt_dec(min_amt)} {asset}</b>\n"
            f"Попробуйте снова.",
            parse_mode="HTML",
        )
        return

    try:
        invoice_id, pay_url = await create_topup_invoice(
            user_id=msg.from_user.id, amount=float(amount), asset=asset
        )
    except Exception as e:
        await msg.reply(f"❌ Не удалось создать счёт: {e}")
        return
    finally:
        await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"checkinv:{invoice_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="paymethod:cryptobot")],
        ]
    )
    await msg.answer(
        f"🧾 Счёт создан: <b>{_fmt_dec(amount)} {asset}</b>\n"
        f"ID: <code>{invoice_id}</code>\n"
        f"Откройте оплату, затем нажмите «Проверить оплату».",
        reply_markup=kb,
        parse_mode="HTML",
    )

@dp.callback_query(lambda c: c.data.startswith("checkinv:"))
async def topup_check(callback: types.CallbackQuery):
    invoice_id = callback.data.split(":", 1)[1]

    try:
        status = await check_and_credit(invoice_id)   # 👈 без колбэка
    except Exception as e:
        await callback.message.answer(f"⚠️ Не удалось получить статус: {e}")
        await callback.answer()
        return

    if status == "paid":
        await callback.message.answer("✅ Оплата подтверждена и зачислена!")
    elif status == "already_paid":
        await callback.message.answer("ℹ️ Этот счёт уже был зачислён ранее.")
    elif status == "active":   # у CryptoBot статус 'active', не 'pending'
        await callback.message.answer("⏳ Счёт ещё не оплачен. Завершите оплату и нажмите «Проверить оплату».")
    elif status == "expired":
        await callback.message.answer("⌛ Срок действия счёта истёк. Создайте новый.")
    else:
        await callback.message.answer("⚠️ Счёт не найден. Попробуйте снова.")
    await callback.answer()

class NowPayStates(StatesGroup):
    waiting_amount_usd = State()

NOWP_MIN_USD = Decimal("10")


@dp.callback_query(lambda c: c.data == "paymethod:NowPayments")
async def paymethod_nowpayments(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(NowPayStates.waiting_amount_usd)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="paymethod:back")]]
    )
    await callback.message.edit_text(
        "Введите сумму пополнения в <b>USD</b>.\n"
        f"🔹 Минимум: <b>{_fmt_dec(NOWP_MIN_USD)} USD</b>\n\n"
        "После ввода я дам ссылку на оплату NOWPayments — монету вы выберете на их странице.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(NowPayStates.waiting_amount_usd)
async def nowp_amount_entered(msg: types.Message, state: FSMContext):
    raw = (msg.text or "").strip().replace(",", ".")
    try:
        amount_usd = Decimal(raw)
        if amount_usd <= 0:
            raise ValueError
    except Exception:
        await msg.reply("❌ Неверная сумма. Введите положительное число (например, 12 или 12.34).")
        return

    if amount_usd < NOWP_MIN_USD:
        await msg.reply(
            f"⚠️ Сумма слишком мала.\nМинимальная сумма: <b>{_fmt_dec(NOWP_MIN_USD)} USD</b>",
            parse_mode="HTML",
        )
        return

    try:
        order_id = f"NP-{msg.from_user.id}-{int(time.time())}-{secrets.token_hex(3)}"
        inv = await np_create_invoice(
            amount=float(amount_usd),
            price_currency="usd",
            order_id=order_id,
            user_telegram_id=msg.from_user.id,
            success_url = "https://nowpayments.io",
            cancel_url = "https://nowpayments.io",
            ipn_url=f"{SERVER_URL}/nowpayments/ipn",
            order_description="Пополнение баланса через NOWPayments",
        )
    except Exception as e:
        await msg.reply(f"❌ Не удалось создать счёт NOWPayments: {e}")
        return
    finally:
        await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Открыть оплату (NOWPayments)", url=inv.invoice_url)],
            [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"np_check:{order_id}")],
        ]
    )
    await msg.answer(
        f"🧾 Счёт создан: <b>{_fmt_dec(amount_usd)} USD</b>\n"
        f"ID: <code>{inv.id}</code>\n"
        "Откройте страницу и выберите удобную монету. Баланс зачислится автоматически после оплаты.",
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=False,
    )




@dp.callback_query(lambda c: c.data.startswith("np_check:"))
async def nowp_check_status(callback: types.CallbackQuery):
    order_id = callback.data.split(":", 1)[1]

    inv = get_nowp_invoice(order_id)
    if not inv:
        await callback.message.answer("❌ Счёт не найден. Создайте новый и попробуйте снова.")
        await callback.answer()
        return

    # payload (содержит iid, invoice_url, возможно payment_id)
    try:
        payload = json.loads(inv.get("payload") or "{}")
    except Exception:
        payload = {}

    payment_id = payload.get("payment_id")
    iid = payload.get("iid") or payload.get("invoice_id")
    invoice_url = payload.get("invoice_url")
    tg_id = payload.get("tg")

    # 1) если payment_id ещё нет — пробуем найти его через /payment?invoiceId=...
    if not payment_id and iid:
        try:
            resp = await np_list_by_iid(iid=str(iid), limit=5)
            items = resp.get("data") or []
            if items:
                candidate = next(
                    (p for p in items if (p.get("payment_status") or "").lower() not in {"failed", "expired"}),
                    items[0]
                )
                payment_id = str(candidate.get("payment_id"))
                update_nowp_payment_details(
                    order_id=order_id,
                    payment_id=payment_id,
                    pay_currency=(candidate.get("pay_currency") or "").upper() or None,
                    pay_amount=float(candidate.get("pay_amount") or 0) if candidate.get("pay_amount") else None,
                    extra={
                        "invoice_id": candidate.get("invoice_id"),
                        "purchase_id": candidate.get("purchase_id"),
                        "actually_paid": candidate.get("actually_paid"),
                        "outcome_currency": candidate.get("outcome_currency"),
                        "outcome_amount": candidate.get("outcome_amount"),
                    }
                )
        except Exception as e:
            await callback.message.answer(f"⚠️ Не удалось найти платёж по инвойсу: {e}")
            await callback.answer()
            return

    # если всё ещё нет payment_id → значит человек даже не начал оплату
    if not payment_id:
        msg = [
            "⏳ Платёж ещё не создан на стороне NOWPayments.",
            "Откройте страницу оплаты, выберите монету и нажмите «Pay».",
        ]
        if invoice_url:
            msg.append(f"\n🔗 Страница оплаты: {invoice_url}")
        await callback.message.answer("\n".join(msg))
        await callback.answer()
        return

    # 2) получаем статус по payment_id
    try:
        info = await np_get_status(str(payment_id))
    except Exception as e:
        await callback.message.answer(f"⚠️ Не удалось получить статус платежа: {e}")
        await callback.answer()
        return

    status = (info.get("payment_status") or "").lower()
    pay_currency = (info.get("pay_currency") or "").upper()
    pay_amount = Decimal(str(info.get("pay_amount") or 0))

    # 3) финальные статусы → пробуем идемпотентно зачислить баланс
    if status in {"finished", "confirmed", "sending"}:
        if mark_nowp_paid(order_id):
            if tg_id:
                try:
                    rate = await get_rate_usdt(pay_currency)
                    credits = (pay_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    add_balance(int(tg_id), credits)
                    await callback.message.answer(
                        f"✅ Оплата подтверждена и зачислена!\n"
                        f"Зачислено: {credits} USDT (экв.)\n"
                        f"Монета: {pay_currency}, сумма: {pay_amount}"
                    )
                except Exception as e:
                    await callback.message.answer(
                        f"✅ Оплата подтверждена, но не удалось зачислить баланс: {e}"
                    )
            else:
                await callback.message.answer("✅ Оплата подтверждена, но не удалось найти пользователя.")
        else:
            await callback.message.answer("ℹ️ Этот счёт уже был зачислён ранее.")

    elif status == "waiting":
        await callback.message.answer("🕒 Ожидание оплаты. Завершите перевод на странице NOWPayments.")
    elif status == "confirming":
        await callback.message.answer("⛓ Идёт подтверждение в блокчейне. Ожидаем финализацию…")
    elif status == "partially_paid":
        await callback.message.answer("⚠️ Оплачено меньше требуемого. Дозавершите оплату или создайте новый счёт.")
    elif status == "failed":
        await callback.message.answer("❌ Платёж не прошёл. Попробуйте снова.")
    elif status == "expired":
        await callback.message.answer("⌛ Срок оплаты истёк. Создайте новый счёт.")
    else:
        await callback.message.answer(f"ℹ️ Статус: {status}")

    await callback.answer()


PROXYNOVA_COMB_URL = "https://api.proxynova.com/comb"

# максимум, который показываем и листаем, даже если API вернёт 10 000
COMB_TOTAL_SOFT_MAX = 50

# Безопасные пределы пагинации (ProxyNova капризничает при больших значениях)
COMB_LIMIT_DEF = 15
COMB_LIMIT_MAX = 25

# ================== FSM =====================

class LeakCheckStates(StatesGroup):
    waiting_for_query = State()

# ================== Валидация ввода =========

_email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_username_re = re.compile(r"^[A-Za-z0-9._-]{3,64}$")

def is_email(s: str) -> bool:
    return bool(_email_re.match(s or ""))

def is_username(s: str) -> bool:
    return bool(_username_re.match(s or ""))

# ================== Утилиты форматирования ===

def mask_password(p: str) -> str:
    """
    Маскируем пароль: показываем первые 1–2 символа и длину, остальное — звёздочки.
    """
    if not p:
        return ""
    if len(p) == 1:
        return p[0] + "*"
    if len(p) == 2:
        return p[0] + "*"
    return p[:2] + "*" * (len(p) - 2)

def split_line(line: str) -> Tuple[str, str]:
    """
    Разбиваем строку формата 'email:password' по первому двоеточию.
    """
    if ":" in line:
        email, pwd = line.split(":", 1)
    else:
        email, pwd = line, ""
    return email.strip(), pwd.strip()

def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⤴️ В меню", callback_data="back_to_menu")],
        ]
    )

# ================== Работа с ProxyNova comb ==

async def fetch_comb(query: str, start: int = 0, limit: int = COMB_LIMIT_DEF) -> dict:
    """
    Вызов публичного comb API.
    Возвращает dict: {"count": int, "lines": [ "email:pass", ... ]}
    """
    params = {"query": query, "start": str(start), "limit": str(limit)}
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(PROXYNOVA_COMB_URL, params=params)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or "count" not in data or "lines" not in data:
            raise RuntimeError("unexpected comb api response structure")
        return data

async def safe_fetch_comb(query: str, start: int, limit: int) -> dict:
    """
    Защищённый запрос к comb:
    - ограничиваем limit до COMB_LIMIT_MAX
    - нормализуем start
    - при 400 пытаемся откатиться на последнюю валидную страницу
    """
    limit = _clamp(int(limit or COMB_LIMIT_DEF), 1, COMB_LIMIT_MAX)
    start = max(0, int(start or 0))
    try:
        return await fetch_comb(query=query, start=start, limit=limit)
    except Exception as e:
        msg = str(e)
        if "400 Bad Request" in msg and start > 0:
            try:
                head = await fetch_comb(query=query, start=0, limit=1)
                total = int(head.get("count", 0))
                if total > 0:
                    last_start = max(0, total - limit)
                    if last_start != start:
                        return await fetch_comb(query=query, start=last_start, limit=limit)
            except Exception:
                pass
        raise

# ================== Клавиатуры ==============

def comb_pager_kb(*, query: str, start: int, limit: int, total: int, reveal: bool) -> InlineKeyboardMarkup:
    # total тут уже <= COMB_TOTAL_SOFT_MAX
    prev_start = max(0, start - limit)
    next_start = start + limit

    rows: list[list[InlineKeyboardButton]] = []

    nav_row: list[InlineKeyboardButton] = []
    if start > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"comb:page:{query}:{prev_start}:{limit}:{int(reveal)}"
            )
        )
    if next_start < total:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперёд ➡️",
                callback_data=f"comb:page:{query}:{next_start}:{limit}:{int(reveal)}"
            )
        )
    if nav_row:
        rows.append(nav_row)

    rows.append([
        InlineKeyboardButton(
            text=("🙈 Скрыть пароли" if reveal else "👁 Показать пароли"),
            callback_data=f"comb:reveal:{query}:{start}:{limit}:{int(not reveal)}"
        )
    ])
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="close_this")])
    rows.append([InlineKeyboardButton(text="⤴️ В меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ================== Рендер страницы =========

def _format_comb_lines(lines: list[str], reveal: bool) -> str:
    out = []
    for raw in lines:
        email, pwd = split_line(raw)
        pwd_view = pwd if reveal else mask_password(pwd)
        out.append(f"📧 <code>{email}</code>\n🔑 <code>{pwd_view}</code>")
    return "\n\n".join(out)

async def render_comb_page(
    *,
    query: str,
    start: int = 0,
    limit: int = COMB_LIMIT_DEF,
    reveal: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    # жёстко ограничим limit и start ещё до запроса
    limit = _clamp(int(limit or COMB_LIMIT_DEF), 1, COMB_LIMIT_MAX)
    start = max(0, int(start or 0))

    # тянем страницу как обычно
    data = await safe_fetch_comb(query=query, start=start, limit=limit)

    api_total = int(data.get("count", 0))
    lines     = data.get("lines") or []

    # «мягкий» лимит на общее число результатов
    total = min(api_total, COMB_TOTAL_SOFT_MAX)
    if total == 0:
        return "✅ Ничего не найдено.", back_menu_kb()

    # не даём уйти за рамки 50
    if start >= total:
        start = max(0, total - limit)
        # подстраховка: если надо — дотянем другие строки
        if start < api_total:
            data  = await safe_fetch_comb(query=query, start=start, limit=limit)
            lines = data.get("lines") or []

    shown_to = min(start + limit, total)

    header = f"📊 Совпадения: <b>{total}</b>\nПоказано: {start+1}–{shown_to}"
    body   = _format_comb_lines(lines, reveal=reveal)
    kb     = comb_pager_kb(query=query, start=start, limit=limit, total=total, reveal=reveal)

    return header + "\n\n" + body, kb

# ================== Хендлеры =================

# Старт по кнопке
@dp.callback_query(F.data == "check_email_leak")
async def on_check_leak_click(callback: types.CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    if user and user.get("banned"):
        await callback.message.answer("⛔ ВЫ ЗАБАНЕНЫ ⛔")
        return

    cost = Decimal("0.5")  # сразу задаём Decimal
    bal = Decimal(str(get_balance(callback.from_user.id) or 0))

    if bal < cost:
        need = (cost - bal).quantize(Decimal("0.01"))
        await callback.message.edit_text(
            "💳 Недостаточно средств для выбранной услуги.\n\n"
            f"🪙 Баланс: {_fmt_dec(bal)} USDT\n"
            f"💸 Не хватает: {_fmt_dec(need)} USDT\n\n"
            "Пополните баланс и попробуйте снова.",
            reply_markup=topup_or_back_kb(),
        )
        await callback.answer()
        return
    await state.set_state(LeakCheckStates.waiting_for_query)
    await callback.message.edit_text(
        "🔎 Введите email или username для проверки в утечках.\n\n"
        "✉️ Пример email: <code>name@example.com</code>\n"
        "👤 Пример ника: <code>john_doe</code>\n\n"
        "⚠️ Стоимость услуги 0.50 USDT ⚠️",
        parse_mode="HTML",
        reply_markup=back_menu_kb(),
    )
    await callback.answer()

# Приём строки запроса
@dp.message(LeakCheckStates.waiting_for_query, F.text)
async def on_leak_query(msg: types.Message, state: FSMContext):
    raw = (msg.text or "").strip()

    if not (is_email(raw) or is_username(raw)):
        await msg.reply(
            "❌ Неверный формат.\n"
            "Отправьте email (<code>user@mail.com</code>) или ник (латиница/цифры/._- , 3–64 символа).",
            parse_mode="HTML"
        )
        return

    # Если email → берём только часть до "@"
    query = raw.split("@", 1)[0] if (is_email(raw) and "@" in raw) else raw

    await state.clear()
    await msg.reply("⏳ Ищу совпадения…")
    cost = Decimal("0.5")  # сразу задаём Decimal
    try:
        text, kb = await render_comb_page(query=query, start=0, limit=COMB_LIMIT_DEF, reveal=False)
        # 3) списываем средства
        ok_spend, new_balance, _ = minus_balance(msg.from_user.id, cost)
        if not ok_spend:
            bal_txt = _fmt_dec(Decimal(str(new_balance))) if isinstance(new_balance, (int, float)) else "0"
            need = (cost - Decimal(str(new_balance or 0))).quantize(
                Decimal("0.01")) if new_balance is not None else cost
            await msg.reply(
                "💳 Недостаточно средств для этого плана.\n\n"
                f"🪙 Баланс: {bal_txt} USDT\n"
                f"💸 Нужно: {_fmt_dec(cost)} USDT (не хватает {_fmt_dec(need)})",
                reply_markup=topup_or_back_kb()
            )
            await state.clear()
            return
        await msg.reply(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
    except Exception as e:
        await msg.reply(f"⚠️ Ошибка запроса: {e}")

# Пагинация
@dp.callback_query(F.data.startswith("comb:page:"))
async def on_comb_page(callback: types.CallbackQuery):
    try:
        _, _, query, start, limit, reveal = callback.data.split(":", 5)
        start  = max(0, int(start))
        limit  = _clamp(int(limit), 1, COMB_LIMIT_MAX)
        reveal = bool(int(reveal))
    except Exception:
        await callback.answer("Некорректные параметры", show_alert=True)
        return

    # «мягкий» стоп: не листаем дальше 50 результатов
    if start >= COMB_TOTAL_SOFT_MAX:
        start = max(0, COMB_TOTAL_SOFT_MAX - limit)

    try:
        text, kb = await render_comb_page(query=query, start=start, limit=limit, reveal=reveal)
        await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
    except Exception as e:
        await callback.message.edit_text(f"⚠️ Ошибка запроса: {e}")
    finally:
        await callback.answer()


@dp.callback_query(F.data.startswith("comb:reveal:"))
async def on_comb_toggle_reveal(callback: types.CallbackQuery):
    try:
        _, _, query, start, limit, reveal_next = callback.data.split(":", 5)
        start       = max(0, int(start))
        limit       = _clamp(int(limit), 1, COMB_LIMIT_MAX)
        reveal_next = bool(int(reveal_next))
    except Exception:
        await callback.answer("Некорректные параметры", show_alert=True)
        return

    if start >= COMB_TOTAL_SOFT_MAX:
        start = max(0, COMB_TOTAL_SOFT_MAX - limit)

    try:
        text, kb = await render_comb_page(query=query, start=start, limit=limit, reveal=reveal_next)
        await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
    except Exception as e:
        await callback.message.edit_text(f"⚠️ Ошибка запроса: {e}")
    finally:
        await callback.answer()

# Закрыть сообщение
@dp.callback_query(F.data == "close_this")
async def close_this(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    finally:
        await callback.answer()


CAMXPLOIT_PATH = "CamXploit/CamXploit.py"  # укажите путь к скрипту
SCAN_TIMEOUT = 300  # сек, после которых убиваем скан
USER_JOBS: dict[int, str] = {}  # telegram_id -> job_id (простая защита от параллельных задач)

class CamStates(StatesGroup):
    waiting_ip = State()

def _is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip.strip())
        return True
    except Exception:
        return False

@dp.callback_query(F.data == "scan_cam")
async def on_scan_cam_click(callback: types.CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    if user and user.get("banned"):
        await callback.message.answer("⛔ ВЫ ЗАБАНЕНЫ ⛔")
        return

    cost = Decimal("0.5")  # сразу задаём Decimal
    bal = Decimal(str(get_balance(callback.from_user.id) or 0))

    if bal < cost:
        need = (cost - bal).quantize(Decimal("0.01"))
        await callback.message.edit_text(
            "💳 Недостаточно средств для выбранной услуги.\n\n"
            f"🪙 Баланс: {_fmt_dec(bal)} USDT\n"
            f"💸 Не хватает: {_fmt_dec(need)} USDT\n\n"
            "Пополните баланс и попробуйте снова.",
            reply_markup=topup_or_back_kb(),
        )
        await callback.answer()
        return
    # простая защита: не более одной активной задачи на пользователя
    if USER_JOBS.get(callback.from_user.id):
        await callback.answer("⏳ Уже выполняется один скан. Подождите завершения.", show_alert=True)
        return


    await state.set_state(CamStates.waiting_ip)
    await callback.message.edit_text(
        "🔎 Введите публичный IP-адрес для проверки на уязвимые камеры.\n\n"
        "💰 Стоимость услуги 0.5 USDT", reply_markup=back_to_menu

    )
    await callback.answer()

@dp.message(CamStates.waiting_ip, F.text)
async def on_cam_ip(msg: types.Message, state: FSMContext):
    ip = (msg.text or "").strip()
    if not _is_valid_ip(ip):
        await msg.reply("❌ Некорректный IP. Пример: `8.8.8.8`", parse_mode="Markdown")
        return

    # отметим «идёт задача»
    job_id = str(uuid4())
    USER_JOBS[msg.from_user.id] = job_id

    await state.clear()


    cost = Decimal("0.5")
    ok_spend, new_balance, _ = minus_balance(msg.from_user.id, cost)
    if not ok_spend:
        bal_txt = _fmt_dec(Decimal(str(new_balance))) if isinstance(new_balance, (int, float)) else "0"
        need = (cost - Decimal(str(new_balance or 0))).quantize(
            Decimal("0.01")) if new_balance is not None else cost
        await msg.reply(
            "💳 Недостаточно средств для этого плана.\n\n"
            f"🪙 Баланс: {bal_txt} USDT\n"
            f"💸 Нужно: {_fmt_dec(cost)} USDT (не хватает {_fmt_dec(need)})",
            reply_markup=topup_or_back_kb()
        )
        await state.clear()
        return
    note = await msg.reply(
        f"⏳ Запустил скан <code>{escape(ip)}</code>. Это может занять пару минут…\n\n"
             f"💸 Списано {cost} USDT\n"
             f"🪙 Баланс: {new_balance} USDT",
        parse_mode="HTML"
    )
    # фоновая задача
    asyncio.create_task(_run_camxploit_process(
        ip=ip,
        chat_id=msg.chat.id,
        reply_to=note.message_id,
        user_id=msg.from_user.id,
        job_id=job_id,
    ))

async def _run_camxploit_process(*, ip: str, chat_id: int, reply_to: int, user_id: int, job_id: str):
    """
    Запускаем CamXploit как отдельный процесс, собираем stdout/stderr, по окончании шлём результат.
    """
    try:
        # проверяем наличие файла
        if not os.path.isfile(CAMXPLOIT_PATH):
            await bot.send_message(chat_id, "⚠️ CamXploit не найден на сервере. Сообщите администратору.")
            return

        # Формируем команду. У CamXploit нет параметра для IP — он спрашивает интерактивно.
        # Поэтому передадим IP через stdin.
        proc = await asyncio.create_subprocess_exec(
            "python3", CAMXPLOIT_PATH,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Отправим IP во входной поток программы + перевод строки
        try:
            proc.stdin.write((ip + "\n").encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception:
            pass

        try:
            outs, errs = await asyncio.wait_for(proc.communicate(), timeout=SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await bot.send_message(chat_id, "⌛ Время сканирования истекло. Попробуйте позже или другой IP.")
            return

        stdout = outs.decode("utf-8", errors="replace")
        stderr = errs.decode("utf-8", errors="replace")

        # Если вывод огромный — отправим файлом
        text_preview = stdout.strip()
        if not text_preview:
            text_preview = stderr.strip()

        if not text_preview:
            await bot.send_message(chat_id, "⚠️ Скан не дал результатов (пустой вывод).")
            return


        # большой — как файл
        fname = f"camxploit_{ip.replace('.', '_')}.txt"
        path = f"/tmp/{fname}"
        with open(path, "w", encoding="utf-8") as f:
            f.write(stdout or stderr)
        await bot.send_document(chat_id, types.FSInputFile(path, filename=fname),
                                caption=f"✅ Результат сканирования <code>{ip}</code>",
                                parse_mode="HTML",
                                reply_to_message_id=reply_to)
        try:
            os.remove(path)
        except Exception:
            pass

    except Exception as e:
        await bot.send_message(chat_id, f"❌ Ошибка сканирования: {e}")
    finally:
        # снимаем флаг активной задачи
        if USER_JOBS.get(user_id) == job_id:
            USER_JOBS.pop(user_id, None)

@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    text = (
        "<b>ℹ️ Возможности бота</b>\n\n"

        "🛠 <b>Сгенерировать ссылку</b>\n"
        "Создаёт ссылку неотличимую от оригинала, по клику на которую тебе придет лог с подробной информацией о жертве. "
        "Полезно, если хочешь узнать айпи или другую информацию.\n\n"

        "🔗 <b>Мои ссылки</b>\n"
        "Здесь ты найдёшь все созданные ссылки, увидишь количество переходов "
        "и сможешь управлять ими.\n\n"

        "💳 <b>Пополнить</b>\n"
        "Пополнение баланса. На данный момент доступно через CryptoBot - без лимита и NowPayments - лимит от 10 долларов.\n\n"

        "📷 <b>Скан камеры</b>\n"
        "Проверка IP-адреса на наличие уязвимых камер, пробует подобрать пароль. "
        "В случае успеха дает данные для подключения\n\n"

        "🕵️ <b>Пробив личности</b>\n"
        "Поиск информации о личности по номеру телефона, почте, нику, ФИО или другому запросу.\n\n "

        "✉️ <b>Проверить утечки паролей по почте</b>\n"
        "Поиск слитых паролей по почте или нику в одной из самых крупных утечек баз данных.\n\n "
    )

    await message.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=back_to_menu
    )


# ---- Запуск ----

async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="📌 Запустить меню"),
        BotCommand(command="info", description="ℹ️ Описание функционала"),
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
