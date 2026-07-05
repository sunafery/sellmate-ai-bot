"""
Entry point — runs the Telegram bot with auto-restart.
Flask webhook removed (CryptoBot not accessible from Railway).
Payments: Telegram Stars (automatic) + USDT manual.
"""
import os, time, logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s"
)
logger = logging.getLogger("run")

import seller_bot

def run_bot():
    while True:
        try:
            logger.info("Bot polling started")
            seller_bot.bot.polling(none_stop=True, interval=0, timeout=25)
        except Exception as e:
            logger.error(f"Bot polling crashed: {e}")
            logger.info("Restarting in 5 seconds...")
            time.sleep(5)

run_bot()
