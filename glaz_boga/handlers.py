# handlers/dyxless.py
from __future__ import annotations

import contextlib
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from typing import Any

import httpx
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.db_api import get_balance, get_user, minus_balance

try:
    from config import DYXLESS_TOKEN
except Exception:
    DYXLESS_TOKEN = None

dyx_router = Router()

API_URL = "https://api-dyxless.cfd/query"
REQ_TIMEOUT = 12.0           # сек
MAX_ITEMS_TO_SHOW = 5        # максимум элементов массива data для показа в сообщении
MAX_JSON_LEN = 3500          # если превью длиннее — отдадим файлом



back_to_menu = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]]
)



# ---------- FSM ----------
class PersonStates(StatesGroup):
    waiting_query = State()


# ---------- helpers ----------

def topup_or_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")],
        ]
    )

def _digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def _build_query_variants(raw: str) -> list[str]:
    """
    Делаем несколько разумных вариантов для одного ввода:
      - как есть
      - email → lower
      - телефон → цифры, 8→7, добавление 7 если длина 10
    """
    raw = (raw or "").strip()
    variants: list[str] = []
    if not raw:
        return variants

    variants.append(raw)

    # e-mail
    if "@" in raw and "." in raw:
        variants.append(raw.lower())

    # телефон
    d = _digits(raw)
    if len(d) >= 7:
        # как есть (цифры)
        variants.append(d)
        # 8XXXXXXXXXX → 7XXXXXXXXXX (для РФ)
        if len(d) in (10, 11) and d.startswith("8"):
            variants.append("7" + d[1:])
        # если 10 цифр без кода страны — добавим 7
        if len(d) == 10 and not d.startswith(("7", "8")):
            variants.append("7" + d)
    # оставим уникальные и в исходном порядке
    seen = set()
    uniq = []
    for q in variants:
        if q not in seen:
            uniq.append(q)
            seen.add(q)
    return uniq

def _take_first_items(data: Any, n: int) -> Any:
    """
    Берём первые n элементов массива/объекта для превью.
    Если data — список, то data[:n]; если dict — оставляем как есть.
    """
    if isinstance(data, list):
        return data[:n]
    return data

def _pretty_json(obj: Any) -> str:
    return escape(json.dumps(obj, ensure_ascii=False, indent=2))

def _fmt_dec(d: Decimal) -> str:
    """
    Красивый вывод Decimal:
    - всегда 2 знака после запятой
    - вторая цифра округляется вверх
    """
    d_q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d_q:.2f}"


# ---------- handlers ----------
@dyx_router.callback_query(F.data == "check_person_data")
async def on_check_person_data(cb: types.CallbackQuery, state: FSMContext):
    user = get_user(cb.from_user.id)
    if user and user.get("banned"):
        await cb.message.answer("⛔ ВЫ ЗАБАНЕНЫ ⛔")
        return

    cost = Decimal("1.00")
    bal = Decimal(str(get_balance(cb.from_user.id) or 0))

    if bal < cost:
        need = (cost - bal).quantize(Decimal("0.01"))
        await cb.message.edit_text(
            "💳 Недостаточно средств для выбранного плана.\n\n"
            f"📌 Стоимость: {cost} USDT\n"
            f"🪙 Баланс: {_fmt_dec(bal)} USDT\n"
            f"💸 Не хватает: {_fmt_dec(need)} USDT\n\n"
            "Пополните баланс и попробуйте снова.",
            reply_markup=topup_or_back_kb(),
        )
        await cb.answer()
        return
    if not DYXLESS_TOKEN:
        await cb.answer("Токен API не настроен", show_alert=True)
        return

    text = (
        "🔎 <b>Проверка по базе</b>\n\n"
        "Отправь в ответ <b>поисковый запрос</b> (телефон, email, логин и т.п.).\n"
        "Я попробую найти как можно больше информации.\n\n"
        "💰 Стоимость услуги 1 USDT"
    )
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_menu)
    await state.set_state(PersonStates.waiting_query)
    await cb.answer()


@dyx_router.message(PersonStates.waiting_query, F.text)
async def do_check_person_data(msg: types.Message, state: FSMContext):
    user_input = (msg.text or "").strip()
    if not user_input:
        await msg.reply("❌ Пустой запрос. Введи телефон/e-mail/строку для поиска.")
        return

    await state.clear()
    wait = await msg.reply("⏳ Делаю запрос…")

    query_variants = _build_query_variants(user_input)
    if not query_variants:
        await wait.edit_text("❌ Не удалось построить варианты запроса.")
        return

    headers = {"Content-Type": "application/json"}
    chosen_query = None
    last_resp = None
    last_status = None
    last_code = None

    try:
        async with httpx.AsyncClient(timeout=REQ_TIMEOUT) as client:
            for candidate in query_variants:
                payload = {"query": candidate, "token": DYXLESS_TOKEN}
                r = await client.post(API_URL, json=payload, headers=headers)
                last_code = r.status_code

                # сервер мог вернуть не-JSON (например 401/403/500)
                try:
                    data = r.json()
                except ValueError:
                    last_resp = {"error": f"Non-JSON response, HTTP {r.status_code}"}
                    if r.status_code >= 500:
                        # при 5xx нет смысла пробовать другие варианты
                        break
                    continue

                last_resp = data
                last_status = bool(data.get("status"))
                counts = int(data.get("counts") or 0)

                # если API говорит ошибка — пробуем следующий вариант
                if not last_status:
                    continue

                # нашли хоть что-то
                if counts > 0:
                    chosen_query = candidate
                    break
            # конец цикла
    except httpx.ReadTimeout:
        await wait.edit_text("⌛ Превышено время ожидания ответа API. Попробуй позже.")
        return
    except httpx.HTTPError as e:
        await wait.edit_text(f"⚠️ Ошибка сети: {escape(str(e))}", parse_mode="HTML")
        return

    # Нет валидного ответа вообще
    if last_resp is None:
        await wait.edit_text(f"⚠️ API не отвечает (HTTP {last_code or 'n/a'}).")
        return

    # Если API вернул status=false для всех вариантов
    if last_status is False:
        err_txt = last_resp.get("error") or last_resp.get("message") or f"API вернуло статус false (HTTP {last_code})."
        await wait.edit_text(f"❌ Запрос отклонён: {escape(str(err_txt))}", parse_mode="HTML")
        return
    cost = Decimal('1.00')
    ok_spend, new_balance, _ = minus_balance(msg.from_user.id, cost)
    if not ok_spend:
        bal_txt = _fmt_dec(Decimal(str(new_balance))) if new_balance is not None else "0"
        need = (cost - Decimal(str(new_balance or 0))).quantize(Decimal("0.01")) if new_balance is not None else cost
        await msg.reply(
            "💳 Недостаточно средств для этого плана.\n\n"
            f"🪙 Баланс: {bal_txt} USDT\n"
            f"💸 Нужно: {_fmt_dec(cost)} USDT (не хватает {_fmt_dec(need)})",
            reply_markup=topup_or_back_kb()
        )
        await state.clear()
        return

    # Если ничего не нашли
    data = last_resp
    counts = int(data.get("counts") or 0)
    if counts == 0:
        diag = f"(HTTP {last_code}; пробовали {len(query_variants)} вариант(ов))"
        await wait.edit_text(
            "✅ Готово\n"
            f"🔎 Запрос: <code>{escape(user_input)}</code>\n"
            "📦 Найдено записей: <b>0</b>\n",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    # Есть данные — готовим превью
    data_array = data.get("data") or []
    preview = _take_first_items(data_array, MAX_ITEMS_TO_SHOW)
    pretty = _pretty_json(preview)

    head = (
        "✅ Готово\n"
        f"🔎 Запрос: <code>{escape(chosen_query or user_input)}</code>\n"
        f"📦 Найдено записей: <b>{counts}</b>\n"
    )




    # Иначе — файлом (полный data)
    from aiogram.types import FSInputFile
    import os, tempfile

    try:
        fd, path = tempfile.mkstemp(prefix="dyx_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data_array, f, ensure_ascii=False, indent=2)

        caption = head + "\n📎 Полный результат во вложении."
        await msg.answer_document(
            document=FSInputFile(path, filename="result.json"),
            caption=caption,
            parse_mode="HTML"
        )
        await wait.delete()
    finally:
        with contextlib.suppress(Exception):
            os.remove(path)