# payments/cryptopay/settings.py
import os

CRYPTO_PAY_API_BASE = os.getenv("CRYPTO_PAY_API_BASE", "https://pay.crypt.bot/api")
CRYPTO_PAY_TOKEN    = (os.getenv("CRYPTO_PAY_TOKEN") or "455482:AAgWfzUqbVFnvRTU0UdsaJoPgrRzLYbQxuY").strip()  # ← сюда временно вставь новый токен вместо REPLACE_ME
CRYPTO_DESC         = os.getenv("CRYPTO_DESC", "Пополнение баланса")
CRYPTO_EXPIRES_IN   = int((os.getenv("CRYPTO_EXPIRES_IN") or "0").strip() or "0")

if CRYPTO_PAY_TOKEN in ("", "REPLACE_ME"):
    raise RuntimeError("CRYPTO_PAY_TOKEN is missing. Set it in code or env.")
