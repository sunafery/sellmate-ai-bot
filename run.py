"""
Entry point — starts Flask webhook server + Telegram bot with auto-restart.
"""
import os, time, logging, threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s"
)
logger = logging.getLogger("run")

PORT        = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

# ── 1. Start Flask webhook server ─────────────────────────────
import webhook
webhook.start(port=PORT)
time.sleep(1.5)
logger.info(f"Flask webhook server running on port {PORT}")

# ── 2. Register CryptoBot webhook URL ─────────────────────────
if WEBHOOK_URL:
    import crypto_pay
    me = crypto_pay.get_me()
    if me:
        logger.info(f"CryptoBot API OK — app: {me.get('name','?')}")
        full_url = WEBHOOK_URL + "/crypto-webhook"
        ok = crypto_pay.set_webhook(full_url)
        logger.info(f"Webhook {'registered' if ok else 'FAILED'}: {full_url}")
    else:
        logger.error("CryptoBot API returned None — check CRYPTO_BOT_TOKEN")
else:
    logger.warning("WEBHOOK_URL not set — skipping CryptoBot webhook registration")

# ── 3. Import bot (registers all handlers) ────────────────────
import seller_bot

# ── 4. Run polling with auto-restart ─────────────────────────
def run_bot():
    while True:
        try:
            logger.info("Bot polling started")
            seller_bot.bot.polling(none_stop=True, interval=0, timeout=25)
        except Exception as e:
            logger.error(f"Bot polling crashed: {e}")
            logger.info("Restarting in 5 seconds...")
            time.sleep(5)

# Run in main thread so process stays alive
run_bot()
