"""
Flask webhook server for CryptoBot payments.
Runs in a separate thread alongside the Telegram bot.
"""
import os
import json
import logging
import threading
from flask import Flask, request, jsonify

logger     = logging.getLogger(__name__)
app        = Flask(__name__)
_on_paid   = None   # callback: fn(payment_info: dict)

def register_payment_callback(fn):
    """Call this from seller_bot.py to receive payment events."""
    global _on_paid
    _on_paid = fn

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "SellMate AI webhook active"}), 200

@app.route("/crypto-webhook", methods=["POST"])
def crypto_webhook():
    import crypto_pay

    # ── Verify token ──────────────────────────────────────────
    token_header = request.headers.get("Crypto-Pay-API-Token", "")
    body_bytes   = request.get_data()

    if not crypto_pay.verify_webhook(token_header, body_bytes):
        logger.warning("Webhook: invalid token")
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    # ── Parse body ────────────────────────────────────────────
    try:
        body = json.loads(body_bytes)
    except Exception:
        return jsonify({"ok": False, "error": "Bad JSON"}), 400

    logger.info(f"Webhook received: update_type={body.get('update_type')}")

    payment = crypto_pay.parse_webhook(body)
    if payment and _on_paid:
        try:
            _on_paid(payment)
        except Exception as e:
            logger.error(f"Payment callback error: {e}")

    return jsonify({"ok": True}), 200

def start(port: int = 8080):
    """Start Flask in a daemon thread."""
    def run():
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    t = threading.Thread(target=run, daemon=True, name="webhook-server")
    t.start()
    logger.info(f"Webhook server started on port {port}")
    return t
