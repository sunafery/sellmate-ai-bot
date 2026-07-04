"""
CryptoBot Crypto Pay API module
Docs: https://help.crypt.bot/crypto-pay-api
"""
import os
import hmac
import hashlib
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime

logger = logging.getLogger(__name__)

CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")
API_BASE         = "https://pay.crypt.bot/api"

# ── Subscription plans ─────────────────────────────────────────
PLANS = {
    "starter":  {"label": "Starter",  "amount": "2.99",  "days": 30},
    "pro":      {"label": "Pro",       "amount": "6.99",  "days": 30},
    "business": {"label": "Business", "amount": "16.99", "days": 30},
}

# ── Processed invoices (anti-double-activation) ────────────────
_processed_invoices: set[str] = set()

# ── Low-level request ──────────────────────────────────────────
def _api(method: str, params: dict) -> dict | None:
    if not CRYPTO_BOT_TOKEN:
        logger.error("CRYPTO_BOT_TOKEN not set")
        return None
    try:
        data = json.dumps(params).encode()
        req  = urllib.request.Request(
            f"{API_BASE}/{method}", data=data,
            headers={
                "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN,
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            if result.get("ok"):
                return result["result"]
            logger.error(f"CryptoBot API error [{method}]: {result}")
    except Exception as e:
        logger.error(f"CryptoBot request failed [{method}]: {e}")
    return None

# ── Create invoice ─────────────────────────────────────────────
def create_invoice(user_id: int, plan_key: str, asset: str = "USDT") -> dict | None:
    plan = PLANS.get(plan_key)
    if not plan:
        logger.error(f"Unknown plan: {plan_key}")
        return None

    payload = f"{user_id}_{plan_key}_{plan['days']}"

    result = _api("createInvoice", {
        "asset":       asset,
        "amount":      plan["amount"],
        "description": f"SellMate AI — {plan['label']} ({plan['days']} days)",
        "payload":     payload,
        "expires_in":  3600,
    })

    if result:
        logger.info(f"Invoice created: id={result.get('invoice_id')} user={user_id} plan={plan_key}")

    return result

# ── Webhook signature verification ────────────────────────────
def verify_webhook(token_header: str, body_bytes: bytes) -> bool:
    """
    CryptoBot sends header: 'Crypto-Pay-API-Token': <your token>
    Simple check — token must match.
    """
    return token_header == CRYPTO_BOT_TOKEN

# ── Parse webhook payload ──────────────────────────────────────
def parse_webhook(body: dict) -> dict | None:
    """
    Returns {user_id, plan_key, days, invoice_id, amount, asset}
    or None if irrelevant / already processed.
    """
    update_type = body.get("update_type")
    if update_type != "invoice_paid":
        return None

    invoice = body.get("payload", {})
    status  = invoice.get("status")
    if status != "paid":
        return None

    invoice_id = str(invoice.get("invoice_id", ""))
    if invoice_id in _processed_invoices:
        logger.warning(f"Duplicate webhook for invoice {invoice_id} — ignored")
        return None

    raw_payload = invoice.get("payload", "")
    parts = raw_payload.split("_")
    if len(parts) != 3:
        logger.error(f"Bad payload format: {raw_payload}")
        return None

    try:
        user_id  = int(parts[0])
        plan_key = parts[1]
        days     = int(parts[2])
    except ValueError:
        logger.error(f"Cannot parse payload: {raw_payload}")
        return None

    if plan_key not in PLANS:
        logger.error(f"Unknown plan in payload: {plan_key}")
        return None

    _processed_invoices.add(invoice_id)
    logger.info(f"Payment confirmed: invoice={invoice_id} user={user_id} plan={plan_key} days={days}")

    return {
        "user_id":    user_id,
        "plan_key":   plan_key,
        "days":       days,
        "invoice_id": invoice_id,
        "amount":     invoice.get("amount"),
        "asset":      invoice.get("asset"),
        "paid_at":    invoice.get("paid_at", datetime.utcnow().isoformat()),
    }

# ── Set webhook URL via API ────────────────────────────────────
def set_webhook(url: str) -> bool:
    result = _api("setWebhook", {"url": url})
    if result:
        logger.info(f"CryptoBot webhook set: {url}")
        return True
    return False

# ── Get app info ───────────────────────────────────────────────
def get_me() -> dict | None:
    return _api("getMe", {})
