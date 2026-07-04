"""
Entry point — starts webhook server, registers CryptoBot webhook URL,
then runs the Telegram bot.
"""
import os
import time
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

PORT         = int(os.environ.get("PORT", 8080))
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL", "")   # e.g. https://your-app.up.railway.app

# 1. Start Flask webhook server
import webhook
seller_bot.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
webhook.start(port=PORT)
time.sleep(1)  # Let Flask bind the port

# 2. Register CryptoBot webhook URL
if WEBHOOK_URL:
    import crypto_pay
    full_url = WEBHOOK_URL.rstrip("/") + "/crypto-webhook"
    ok = crypto_pay.set_webhook(full_url)
    if ok:
        logger.info(f"CryptoBot webhook registered: {full_url}")
    else:
        logger.warning("Failed to register CryptoBot webhook — check CRYPTO_BOT_TOKEN")
else:
    logger.warning("WEBHOOK_URL not set — CryptoBot webhook won't be registered automatically")

# 3. Start Telegram bot (blocking)
logger.info("Starting Telegram bot...")
import seller_bot   # this calls bot.polling() at module end
