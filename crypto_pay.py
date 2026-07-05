"""
CryptoBot Crypto Pay API module
Docs: https://help.crypt.bot/crypto-pay-api
"""
import os, hmac, hashlib, json, logging, urllib.request
from datetime import datetime

logger = logging.getLogger("crypto_pay")

CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")
API_BASE         = "https://pay.crypt.bot/api"

PLANS = {
    "starter":  {"label": "Starter",  "amount": "2.99",  "days": 30},
    "pro":      {"label": "Pro",       "amount": "6.99",  "days": 30},
    "business": {"label": "Business", "amount": "16.99", "days": 30},
}

_processed_invoices: set = set()

def _api(method: str, params: dict):
    if not CRYPTO_BOT_TOKEN:
        logger.error("CRYPTO_BOT_TOKEN is empty!")
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
            raw    = r.read()
            result = json.loads(raw)
            if result.get("ok"):
                return result["result"]
            # Log full error so we can debug
            logger.error(f"CryptoBot API [{method}] error: {result}")
            return None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error(f"CryptoBot HTTPError [{method}]: {e.code} — {body}")
    except Exception as e:
        logger.error(f"CryptoBot request failed [{method}]: {type(e).__name__}: {e}")
    return None

def create_invoice(user_id: int, plan_key: str, asset: str = "USDT"):
    plan = PLANS.get(plan_key)
    if not plan:
        logger.error(f"Unknown plan: {plan_key}")
        return None
    payload = f"{user_id}_{plan_key}_{plan['days']}"
    logger.info(f"Creating invoice: user={user_id} plan={plan_key} asset={asset} amount={plan['amount']}")
    result = _api("createInvoice", {
        "asset":       asset,
        "amount":      plan["amount"],
        "description": f"SellMate AI — {plan['label']} ({plan['days']} days)",
        "payload":     payload,
        "expires_in":  3600,
    })
    if result:
        logger.info(f"Invoice created: id={result.get('invoice_id')} pay_url={result.get('pay_url','?')[:40]}")
    else:
        logger.error("create_invoice returned None — check logs above for details")
    return result

def verify_webhook(token_header: str, body_bytes: bytes) -> bool:
    return token_header == CRYPTO_BOT_TOKEN

def parse_webhook(body: dict):
    update_type = body.get("update_type")
    if update_type != "invoice_paid":
        logger.info(f"Webhook update_type={update_type} — ignored")
        return None
    invoice = body.get("payload", {})
    status  = invoice.get("status")
    if status != "paid":
        logger.info(f"Invoice status={status} — not paid yet")
        return None
    invoice_id = str(invoice.get("invoice_id", ""))
    if invoice_id in _processed_invoices:
        logger.warning(f"Duplicate webhook invoice_id={invoice_id} — ignored")
        return None
    raw_payload = invoice.get("payload", "")
    parts = raw_payload.split("_")
    if len(parts) != 3:
        logger.error(f"Bad payload format: '{raw_payload}'")
        return None
    try:
        user_id  = int(parts[0])
        plan_key = parts[1]
        days     = int(parts[2])
    except ValueError:
        logger.error(f"Cannot parse payload: {raw_payload}")
        return None
    if plan_key not in PLANS:
        logger.error(f"Unknown plan '{plan_key}' in payload")
        return None
    _processed_invoices.add(invoice_id)
    logger.info(f"Payment confirmed: invoice={invoice_id} user={user_id} plan={plan_key}")
    return {
        "user_id":    user_id,
        "plan_key":   plan_key,
        "days":       days,
        "invoice_id": invoice_id,
        "amount":     invoice.get("amount"),
        "asset":      invoice.get("asset"),
    }

def set_webhook(url: str) -> bool:
    result = _api("setWebhook", {"url": url})
    if result is not None:
        logger.info(f"Webhook set: {url}")
        return True
    return False

def get_me():
    return _api("getMe", {})
