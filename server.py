# server.py
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from html import escape
from datetime import datetime
from typing import Optional

import httpx
import user_agents

from payments.nowpayments.webhook import webhook_router
from bot import _get_username
from config import BOT_TOKEN, VPNAPI_KEY, LOG_CHANNEL_ID, PREFERRED_SCHEME
from database.db_api import get_connection
from payments.cryptopay.service import check_and_credit, credit_if_first_time

app = FastAPI()
app.include_router(webhook_router)   # слушает POST /nowpayments/ipn

IPINFO_TOKEN = ""  # можно оставить пустым


# ========== helpers ==========

def _client_ip(req: Request) -> str:
    ip = req.headers.get("x-forwarded-for") or (req.client.host if req.client else None)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
    return ip or "unknown"


def _is_tg_preview(user_agent: str) -> bool:
    return "TelegramBot" in (user_agent or "")


def _get_telegram_id_by_user_id(user_db_id: int) -> Optional[int]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM users WHERE id = ?", (user_db_id,))
        row = cur.fetchone()
        return int(row["telegram_id"]) if row and row["telegram_id"] is not None else None
    finally:
        conn.close()


def _get_short_host_by_slug(slug: str) -> Optional[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT short_host FROM links WHERE link = ?", (slug,))
        row = cur.fetchone()
        if not row:
            return None
        return (row["short_host"] if hasattr(row, "keys") else row[0]) or None
    finally:
        conn.close()


# ========== health ==========

@app.get("/health")
async def health():
    return {"ok": True}


# ========== redirect (новая логика только по БД) ==========

@app.get("/link/{slug}")
async def redirect_link(request: Request, slug: str):
    headers = request.headers
    user_agent_str = headers.get("user-agent", "Unknown")
    accept_lang = headers.get("accept-language", "N/A")
    ip = _client_ip(request)

    conn = get_connection()
    cur = conn.cursor()
    try:
        # Ищем ТОЛЬКО по новой схеме (никаких расшифровок/фолбэков)
        cur.execute(
            "SELECT id, user_id, original_url, clicks, max_clicks, short_host "
            "FROM links WHERE link = ?",
            (slug,)
        )
        row = cur.fetchone()
        if not row:
            return _html_404()

        link_id = row["id"]
        user_db_id = row["user_id"]
        original_url = row["original_url"]
        clicks = int(row["clicks"] or 0)
        max_clicks = int(row["max_clicks"] or 0)
        short_host = (row["short_host"] or "").strip()

        # short_host обязателен в новой логике
        if not short_host:
            return _html_404()

        short_url = f"{PREFERRED_SCHEME}://{short_host}/link/{slug}"

        # Превью Telegram — редирект без инкремента
        if _is_tg_preview(user_agent_str):
            return RedirectResponse(original_url)

        # лимит достигнут -> 403 + уведомление, клики НЕ инкрементим
        if clicks > max_clicks:
            chat_id = _get_telegram_id_by_user_id(user_db_id)
            if chat_id:
                try:
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            data={
                                "chat_id": chat_id,
                                "text": (
                                    "<b>⚠️ Кто-то кликнул по твоей ссылке, но достигнут лимит переходов! ⚠️</b>\n\n"
                                    "⏳ Ссылка больше недоступна для переходов ⏳\n"
                                    "❌ Пользователя не перевело на оригинальную ссылку ❌\n\n"
                                    f"🌍 Оригинальная ссылка: {escape(original_url)}\n"
                                    f"➡️ Короткая ссылка: {escape(short_url)}"
                                ),
                                "parse_mode": "HTML",
                                "disable_web_page_preview": True
                            },
                            timeout=5.0
                        )
                except Exception as e:
                    print("Ошибка уведомления Telegram:", e)
            return _html_403()

        # инкремент кликов
        cur.execute("UPDATE links SET clicks = clicks + 1 WHERE id = ?", (link_id,))
        conn.commit()

        # уведомление владельцу
        chat_id = _get_telegram_id_by_user_id(user_db_id)
        if chat_id:
            await _notify_click_to_owner(
                user_id=chat_id,
                original_url=original_url,
                short_url=short_url,     # уже готовая короткая
                request=request,
                user_agent_str=user_agent_str,
                accept_lang=accept_lang,
                ip=ip
            )
        else:
            print(f"[WARN] Не найден telegram_id для users.id={user_db_id}")

        return RedirectResponse(original_url)

    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _html_404() -> HTMLResponse:
    html = """
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <title>404 Not Found</title>
      <style>
        body {
          background-color: #fff;
          color: #000;
          font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
          text-align: center;
          padding: 80px;
        }
        h1 { font-size: 28px; margin-bottom: 20px; }
        p  { color: #666; font-size: 16px; }
        .code { margin-top: 30px; font-size: 14px; color: #999; }
      </style>
    </head>
    <body>
      <h1>404 Not Found</h1>
      <p>The requested resource was not found on this server.</p>
      <div class="code">nginx/1.24.0</div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=404)


def _html_403() -> HTMLResponse:
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>403 Forbidden</title>
      <style>
        body {
          background-color: #fff;
          color: #000;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          text-align: center;
          padding: 80px;
        }
        h1 { font-size: 26px; margin-bottom: 20px; }
        p  { color: #666; font-size: 16px; }
        .code { margin-top: 30px; font-size: 14px; color: #999; }
      </style>
    </head>
    <body>
      <h1>403 Forbidden</h1>
      <p>You don't have permission to access this resource.</p>
      <div class="code">nginx/1.24.0</div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=403)


# ========== notify helper ==========

async def _notify_click_to_owner(
    user_id: int | str,
    original_url: str,
    short_url: str,
    request: Request,
    user_agent_str: str,
    accept_lang: str,
    ip: Optional[str]
):
    ua = user_agents.parse(user_agent_str)
    geo_info = {}
    vpn_info = {
        "vpn": False,          # булево
        "proxy": False,        # булево
        "tor": False,          # булево
        "asn": "N/A",
        "org": "N/A",
        "connection_type": "N/A",
        "timezone": "N/A",
        "sources": {}          # для отладки
    }

    def _dc_suspect(org: str) -> bool:
        if not org:
            return False
        org_l = org.lower()
        dc_keywords = [
            "ovh", "hetzner", "digitalocean", "aws", "amazon", "google", "gcp",
            "microsoft", "azure", "contabo", "linode", "vultr", "leaseweb",
            "ionos", "scaleway", "akama", "cloudflare", "oracle cloud"
        ]
        return any(k in org_l for k in dc_keywords)

    async with httpx.AsyncClient(timeout=5.0) as client:
        # --- Гео (ip-api.com) ---
        try:
            if ip:
                geo_resp = await client.get(
                    f"http://ip-api.com/json/{ip}?fields=status,message,country,regionName,city,zip,lat,lon,isp",
                )
                if geo_resp.status_code == 200:
                    geo_info = geo_resp.json()
        except Exception as e:
            print("Ошибка geo:", e)

        # --- ipinfo.io (ASN/ORG/таймзона + эвристика DC) ---
        try:
            if ip:
                url = f"https://ipinfo.io/{ip}/json"
                if IPINFO_TOKEN:
                    url += f"?token={IPINFO_TOKEN}"
                info_resp = await client.get(url)
                if info_resp.status_code == 200:
                    info = info_resp.json()
                    org_raw = info.get("org", "")
                    asn = org_raw.split(" ")[0] if org_raw else "N/A"
                    org = org_raw or "N/A"
                    vpn_info.update({
                        "asn": asn,
                        "org": org,
                        "connection_type": info.get("type", "N/A"),
                        "timezone": info.get("timezone", "N/A"),
                    })
                    # эвристика дата-центра = считаем как VPN-трафик
                    if _dc_suspect(org):
                        vpn_info["vpn"] = True
                        vpn_info["sources"]["ipinfo_dc_heuristic"] = True
                    else:
                        vpn_info["sources"]["ipinfo_dc_heuristic"] = False
        except Exception as e:
            print("Ошибка ipinfo:", e)

        # --- ip-api.com (proxy/hosting) ---
        try:
            if ip:
                ipa = await client.get(f"http://ip-api.com/json/{ip}?fields=proxy,hosting")
                if ipa.status_code == 200:
                    data = ipa.json()
                    if bool(data.get("proxy")):
                        vpn_info["proxy"] = True
                        vpn_info["sources"]["ip-api_proxy"] = True
                    else:
                        vpn_info["sources"]["ip-api_proxy"] = False

                    if bool(data.get("hosting")):
                        vpn_info["vpn"] = True
                        vpn_info["sources"]["ip-api_hosting"] = True
                    else:
                        vpn_info["sources"]["ip-api_hosting"] = False
        except Exception as e:
            print("Ошибка ip-api:", e)

        # --- vpnapi.io (vpn/proxy/tor) ---
        try:
            if ip and VPNAPI_KEY:
                vpn_resp = await client.get(
                    f"https://vpnapi.io/api/{ip}?key={VPNAPI_KEY}",
                )
                if vpn_resp.status_code == 200:
                    vdata = vpn_resp.json().get("security", {})
                    vpn_flag   = bool(vdata.get("vpn"))
                    proxy_flag = bool(vdata.get("proxy"))
                    tor_flag   = bool(vdata.get("tor"))

                    if vpn_flag:
                        vpn_info["vpn"] = True
                    if proxy_flag:
                        vpn_info["proxy"] = True
                    if tor_flag:
                        vpn_info["tor"] = True

                    vpn_info["sources"]["vpnapi_vpn"] = vpn_flag
                    vpn_info["sources"]["vpnapi_proxy"] = proxy_flag
                    vpn_info["sources"]["vpnapi_tor"] = tor_flag
            else:
                vpn_info["sources"]["vpnapi_vpn"] = None
                vpn_info["sources"]["vpnapi_proxy"] = None
                vpn_info["sources"]["vpnapi_tor"] = None
        except Exception as e:
            print("Ошибка VPNAPI.io:", e)

        # --- Текст уведомления ---
        msg_text = f"""
<b>🔗 Кто-то кликнул по твоей ссылке!</b>

🕒 Время: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
🌐 IP: {ip or 'N/A'}
🖥 User-Agent: <code>{escape(user_agent_str)}</code>
🌏 Язык системы: {escape(accept_lang)}

💻 Платформа: {escape(ua.os.family)} {escape(ua.os.version_string)}
🌍 Браузер: {escape(ua.browser.family)} {escape(ua.browser.version_string)}
📱 Устройство: {escape(ua.device.family)}

🏙 Гео:
- Страна: {geo_info.get('country', 'N/A')}
- Регион: {geo_info.get('regionName', 'N/A')}
- Город: {geo_info.get('city', 'N/A')}
- ZIP: {geo_info.get('zip', 'N/A')}
- ISP: {geo_info.get('isp', 'N/A')}
- Координаты: {geo_info.get('lat', 'N/A')}, {geo_info.get('lon', 'N/A')}
- <a href="https://www.google.com/maps?q={geo_info.get('lat', 'N/A')},{geo_info.get('lon', 'N/A')}">Google Maps</a>

🌐 Сеть:
- ASN: {vpn_info.get('asn')}
- Организация: {vpn_info.get('org')}
- Часовой пояс: {vpn_info.get('timezone')}

🔒 VPN/Proxy/Tor:
- VPN: {str(vpn_info.get('vpn'))}
- Proxy: {str(vpn_info.get('proxy'))}
- Tor: {str(vpn_info.get('tor'))}

🌍 Оригинальная ссылка: <code>{escape(original_url)}</code>
➡️ Короткая ссылка: <code>{escape(short_url)}</code>
""".strip()

        # Копия для закрытого канала
        username = await _get_username(user_id)
        admin_text = (
            "🛡 <b>Новый клик</b>\n"
            f"👤 Владелец (TG): <code>{user_id}</code>\n"
            f"🏷 Ник: {username}\n\n"
            f"{msg_text}"
        )

        # Отправка пользователю
        try:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": user_id,
                    "text": msg_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                },
            )
        except Exception as e:
            print("Ошибка отправки пользователю:", e)

        # Отправка в закрытый канал (если указан)
        if LOG_CHANNEL_ID:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={
                        "chat_id": LOG_CHANNEL_ID,
                        "text": admin_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
            except Exception as e:
                print("Ошибка отправки в канал:", e)


# ========== Crypto Pay webhook ==========

def _extract_invoice_id(payload: dict) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "invoice_id" and isinstance(v, (str, int)):
                    found.append(str(v))
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(payload)
    return found[0] if found else None


@app.post("/cryptopay/webhook")
async def cryptopay_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)

    invoice_id = _extract_invoice_id(payload)
    if not invoice_id:
        return JSONResponse({"ok": True, "skipped": "no_invoice_id"})

    # TODO: опционально проверить подпись вебхука по заголовку
    # sig = request.headers.get("Crypto-Pay-Api-Signature")

    async def credit_callback(user_id: int, amount: float, asset: str):
        # пытаемся зачислить только один раз
        credited = await credit_if_first_time(
            invoice_id=str(invoice_id),
            tg_id=int(user_id),
            amount=amount,
            asset=asset,
        )
        if not credited:
            return  # уже было оплачено/зачислено ранее

        # уведомление пользователю
        text = (
            f"✅ Оплата получена!\n"
            f"Зачислено: {amount:g} {asset} (в USDT по текущему курсу)."
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": user_id, "text": text}
                )
        except Exception as e:
            print("Ошибка уведомления Telegram:", e)

    try:
        status = await check_and_credit(invoice_id, credit_callback=credit_callback)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    return JSONResponse({"ok": True, "status": status})


# ========== run local ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)