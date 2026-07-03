import telebot
from telebot.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
import re
import os
import base64
import urllib.request
import json as json_module
from datetime import datetime, timedelta
from groq import Groq

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

OWNER_ID = 1249820876
OWNER_USERNAME = "sunafery"
BOT_USERNAME = os.environ.get("BOT_USERNAME", "your_bot_username")

FREE_LIMIT = 3
REFERRAL_BONUS = 3

STARS_STARTER = 200
STARS_PRO = 500
STARS_BUSINESS = 1200

MODELS = {
    "smart": "llama-3.3-70b-versatile",
    "fast": "llama-3.1-8b-instant"
}
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

user_free_left = {}
user_history = {}
pro_users = {}
user_settings = {}
user_text_history = {}
referred_by = {}
all_users = set()
user_plan = {}

PLATFORMS = {
    "auto": "Auto-detect",
    "etsy": "Etsy",
    "amazon": "Amazon",
    "ebay": "eBay",
    "shopify": "Shopify",
    "facebook": "Facebook Marketplace",
    "depop": "Depop",
    "poshmark": "Poshmark",
    "vinted": "Vinted"
}

WELCOME_TEXT = (
    "🚀 Welcome to SellMate AI\n\n"
    "Your personal AI selling assistant for every marketplace.\n\n"
    "I help sellers on Etsy, Amazon, eBay, Shopify, Facebook Marketplace "
    "and more write listings that actually sell.\n\n"
    "What I do in seconds:\n"
    "✍️ Write SEO-optimized product listings\n"
    "🏷️ Generate titles, descriptions & tags\n"
    "📷 Analyze your product photo\n"
    "🔍 Research keywords for any platform\n"
    "💬 Rewrite, improve or translate any listing\n"
    "📊 Suggest pricing strategy\n"
    "📣 Write ad copy for social media\n\n"
    "Just send me your product name and details — or a photo.\n\n"
    "🎁 You start with 3 free requests. No credit card needed."
)

MENU_MAIN_TEXT = (
    "🏠 Main Menu\n\n"
    "What would you like to do today?"
)

def get_sub_text():
    return (
        "💎 SellMate AI Plans\n\n"
        "Upgrade and never worry about limits again.\n\n"
        "🥉 Starter — 200 ⭐ / month (~$2.99)\n"
        "50 AI requests per month\n"
        "All platforms\n"
        "Basic listing generator\n\n"
        "🥈 Pro — 500 ⭐ / month (~$6.99)\n"
        "Unlimited requests\n"
        "All platforms\n"
        "SEO keyword research\n"
        "Photo analysis\n"
        "Ad copy generator\n"
        "Priority responses\n\n"
        "🥇 Business — 1200 ⭐ / month (~$16.99)\n"
        "Everything in Pro\n"
        "Bulk listing generation\n"
        "Competitor analysis\n"
        "Price strategy advisor\n"
        "Dedicated support\n\n"
        "⭐ Pay with Telegram Stars — instant, automatic\n"
        "💰 Pay with USDT crypto — tap button below"
    )

SETTINGS_MAIN_TEXT = "⚙️ Settings\n\nCustomize your experience:"

def get_settings(uid):
    if uid not in user_settings:
        user_settings[uid] = {
            "model": "smart",
            "platform": "auto",
            "tone": "auto",
            "length": "auto",
            "language": "en"
        }
    return user_settings[uid]

def get_plan(uid):
    return user_plan.get(uid, "free")

def clean_text(text):
    return re.sub(r'[\u3040-\u30ff\uac00-\ud7af\u4e00-\u9fff]', '', text)

def is_unlimited(uid):
    if uid == OWNER_ID:
        return True
    plan = get_plan(uid)
    if plan == "free":
        return False
    expiry = pro_users.get(uid)
    return expiry is not None and expiry > datetime.now()

def has_requests(uid):
    if is_unlimited(uid):
        plan = get_plan(uid)
        if plan == "starter":
            used = user_settings.get(uid, {}).get("monthly_used", 0)
            return used < 50
        return True
    left = user_free_left.get(uid, FREE_LIMIT)
    return left > 0

def get_free_left(uid):
    if uid not in user_free_left:
        user_free_left[uid] = FREE_LIMIT
    return user_free_left[uid]

def deduct_request(uid):
    if uid == OWNER_ID:
        return
    plan = get_plan(uid)
    if plan == "starter":
        s = get_settings(uid)
        s["monthly_used"] = s.get("monthly_used", 0) + 1
    elif plan in ("pro", "business"):
        return
    else:
        if uid not in user_free_left:
            user_free_left[uid] = FREE_LIMIT
        if user_free_left[uid] > 0:
            user_free_left[uid] -= 1

def get_footer(uid):
    if uid == OWNER_ID:
        return ""
    plan = get_plan(uid)
    expiry = pro_users.get(uid)
    if expiry and expiry > datetime.now():
        if plan == "starter":
            s = get_settings(uid)
            used = s.get("monthly_used", 0)
            left = max(0, 50 - used)
            return "\n\n─────────────────\n🥉 Starter — " + str(left) + " requests left this month"
        return ""
    left = get_free_left(uid)
    if left <= 0:
        return "\n\n─────────────────\n❌ Free requests used up · /subscription to continue"
    return "\n\n─────────────────\n🎁 Free requests left: " + str(left) + " · /subscription for unlimited"

def add_to_text_history(uid, text):
    if uid not in user_text_history:
        user_text_history[uid] = []
    user_text_history[uid].append(text)
    if len(user_text_history[uid]) > 10:
        user_text_history[uid].pop(0)

def build_system_prompt(settings_):
    platform = settings_.get("platform", "auto")
    tone = settings_.get("tone", "auto")
    length = settings_.get("length", "auto")
    lang = settings_.get("language", "en")

    platform_rules = {
        "etsy": "Platform: Etsy. Write emotionally compelling copy. Include SEO title (max 140 chars), full description with story and materials, 13 comma-separated tags. Etsy buyers value handmade authenticity and uniqueness.",
        "amazon": "Platform: Amazon. Write conversion-focused copy. Include keyword-rich title (max 200 chars), 5 bullet points starting with capital letters highlighting features and benefits, and a detailed description. Use Amazon-style language.",
        "ebay": "Platform: eBay. Write clear and honest descriptions. Include specific item condition, exact measurements/specs, and what's included. eBay buyers want facts, not fluff.",
        "shopify": "Platform: Shopify/DTC store. Write brand-forward copy that tells a story and builds desire. Focus on lifestyle and how the product makes the customer feel.",
        "facebook": "Platform: Facebook Marketplace. Write casual, friendly descriptions. Keep it short, mention price range, condition, and pickup/shipping options. Local buyers want quick info.",
        "depop": "Platform: Depop. Write trendy, Gen-Z friendly copy. Short punchy description, sizing info, condition, styling tips. End with 3-5 hashtags.",
        "poshmark": "Platform: Poshmark. Write polished descriptions. Mention brand prominently, exact measurements, condition details, and original retail price if known.",
        "vinted": "Platform: Vinted. Write honest, detailed condition descriptions. European audience values authenticity. Mention measurements, any flaws, and washing instructions.",
        "auto": "Auto-detect the best format based on the item. If it seems handmade or vintage, use Etsy style. If it seems mass-market, use Amazon style."
    }

    tone_rules = {
        "auto": "",
        "professional": "Use a professional, authoritative tone.",
        "friendly": "Use a warm, friendly, conversational tone.",
        "luxury": "Use an elevated, luxury tone — sophisticated vocabulary, aspirational feel.",
        "casual": "Use a casual, laid-back tone."
    }

    length_rules = {
        "auto": "",
        "short": "Keep it brief — 3-5 sentences max.",
        "medium": "Standard length — 6-10 sentences.",
        "long": "Write a comprehensive, detailed listing."
    }

    lang_rule = "Respond in English." if lang == "en" else "Respond in " + lang + "."

    return (
        "You are SellMate AI, the world's most advanced AI selling assistant for online marketplace sellers. "
        "You help sellers on Etsy, Amazon, eBay, Shopify, Facebook Marketplace, Depop, Poshmark, Vinted and more.\n\n"
        "Your capabilities:\n"
        "1. LISTING WRITER: Write complete, SEO-optimized product listings with titles, descriptions and tags\n"
        "2. KEYWORD RESEARCHER: Find the best keywords for any platform and product\n"
        "3. PRICING ADVISOR: Suggest competitive pricing based on product details\n"
        "4. AD COPY WRITER: Create compelling social media ads (Instagram, TikTok, Pinterest)\n"
        "5. LISTING IMPROVER: Rewrite and optimize existing listings\n"
        "6. TRANSLATOR: Translate listings to any language while keeping SEO\n"
        "7. COMPETITOR ANALYST: Analyze what makes top listings work\n"
        "8. PHOTO ADVISOR: Describe what makes a great product photo for the specific platform\n\n"
        + platform_rules.get(platform, platform_rules["auto"]) + "\n\n"
        "Core rules:\n"
        "- If user explicitly states a brand, model or material — trust that completely, never override\n"
        "- Do not invent facts you don't know\n"
        "- Always be specific — generic listings don't sell\n"
        "- Think like a buyer, write for buyers\n"
        "- If asked to rewrite or improve, use the conversation context\n"
        "- Don't add offers to rewrite unless asked\n"
        + tone_rules.get(tone, "") + " "
        + length_rules.get(length, "") + "\n"
        + lang_rule
    )

def get_user_state(uid):
    if uid not in user_history:
        user_history[uid] = []
    return user_history[uid]

def safe_edit(call, text, markup):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=markup)

def build_main_menu_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✍️ Write Listing", callback_data="quick_listing"),
        InlineKeyboardButton("🔍 Keywords", callback_data="quick_keywords"),
        InlineKeyboardButton("📷 Analyze Photo", callback_data="quick_photo"),
        InlineKeyboardButton("📣 Ad Copy", callback_data="quick_adcopy"),
        InlineKeyboardButton("💰 Pricing", callback_data="quick_pricing"),
        InlineKeyboardButton("🌍 Translate", callback_data="quick_translate")
    )
    markup.add(
        InlineKeyboardButton("ℹ️ About", callback_data="menu_about"),
        InlineKeyboardButton("💎 Plans", callback_data="menu_subscription")
    )
    markup.add(
        InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
        InlineKeyboardButton("🛠️ Support", callback_data="menu_support")
    )
    return markup

def build_back_main():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main"))
    return markup

def build_sub_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🥉 Starter — 200 ⭐ / month", callback_data="pay_starter"),
        InlineKeyboardButton("🥈 Pro — 500 ⭐ / month", callback_data="pay_pro"),
        InlineKeyboardButton("🥇 Business — 1200 ⭐ / month", callback_data="pay_business"),
        InlineKeyboardButton("💰 Pay with USDT crypto", callback_data="pay_usdt"),
        InlineKeyboardButton("⬅️ Back", callback_data="menu_main")
    )
    return markup

def build_usdt_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🥉 Starter — $2.99 USDT", callback_data="pay_usdt_starter"),
        InlineKeyboardButton("🥈 Pro — $6.99 USDT", callback_data="pay_usdt_pro"),
        InlineKeyboardButton("🥇 Business — $16.99 USDT", callback_data="pay_usdt_business"),
        InlineKeyboardButton("⬅️ Back", callback_data="menu_subscription")
    )
    return markup

def build_settings_main_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🛍️ Platform  >", callback_data="set_open_platform"),
        InlineKeyboardButton("🤖 AI Model  >", callback_data="set_open_model"),
        InlineKeyboardButton("🎨 Tone  >", callback_data="set_open_tone"),
        InlineKeyboardButton("📄 Length  >", callback_data="set_open_length"),
        InlineKeyboardButton("🌍 Language  >", callback_data="set_open_language"),
        InlineKeyboardButton("🔄 Reset to defaults", callback_data="set_reset"),
        InlineKeyboardButton("⬅️ Back", callback_data="menu_main")
    )
    return markup

def build_platform_markup(s):
    options = [
        ("auto", "🤖 Auto-detect"),
        ("etsy", "🟠 Etsy"),
        ("amazon", "📦 Amazon"),
        ("ebay", "🟡 eBay"),
        ("shopify", "🟢 Shopify"),
        ("facebook", "🔵 Facebook Marketplace"),
        ("depop", "🟣 Depop"),
        ("poshmark", "🩷 Poshmark"),
        ("vinted", "🔷 Vinted")
    ]
    options.sort(key=lambda x: 0 if x[0] == s["platform"] else 1)
    markup = InlineKeyboardMarkup(row_width=1)
    for key, label in options:
        prefix = "✅ " if s["platform"] == key else ""
        markup.add(InlineKeyboardButton(prefix + label, callback_data="set_platform_" + key))
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return markup

def build_model_markup(s):
    options = [("smart", "🧠 Smart (accurate)"), ("fast", "⚡ Fast (instant)")]
    options.sort(key=lambda x: 0 if x[0] == s["model"] else 1)
    markup = InlineKeyboardMarkup(row_width=1)
    for key, label in options:
        prefix = "✅ " if s["model"] == key else ""
        markup.add(InlineKeyboardButton(prefix + label, callback_data="set_model_" + key))
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return markup

def build_tone_markup(s):
    options = [
        ("auto", "🤖 Auto"),
        ("professional", "👔 Professional"),
        ("friendly", "😊 Friendly"),
        ("luxury", "💎 Luxury"),
        ("casual", "😎 Casual")
    ]
    options.sort(key=lambda x: 0 if x[0] == s["tone"] else 1)
    markup = InlineKeyboardMarkup(row_width=1)
    for key, label in options:
        prefix = "✅ " if s["tone"] == key else ""
        markup.add(InlineKeyboardButton(prefix + label, callback_data="set_tone_" + key))
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return markup

def build_length_markup(s):
    options = [("auto", "🤖 Auto"), ("short", "📌 Short"), ("medium", "📝 Medium"), ("long", "📋 Detailed")]
    options.sort(key=lambda x: 0 if x[0] == s["length"] else 1)
    markup = InlineKeyboardMarkup(row_width=1)
    for key, label in options:
        prefix = "✅ " if s["length"] == key else ""
        markup.add(InlineKeyboardButton(prefix + label, callback_data="set_length_" + key))
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return markup

def build_language_markup(s):
    options = [
        ("en", "🇬🇧 English"), ("es", "🇪🇸 Spanish"), ("de", "🇩🇪 German"),
        ("fr", "🇫🇷 French"), ("it", "🇮🇹 Italian"), ("pt", "🇧🇷 Portuguese"),
        ("ru", "🇷🇺 Russian"), ("ja", "🇯🇵 Japanese"), ("zh", "🇨🇳 Chinese"),
        ("ar", "🇸🇦 Arabic")
    ]
    options.sort(key=lambda x: 0 if x[0] == s["language"] else 1)
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = []
    for key, label in options:
        prefix = "✅ " if s["language"] == key else ""
        buttons.append(InlineKeyboardButton(prefix + label, callback_data="set_language_" + key))
    markup.add(*buttons)
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return markup

def create_crypto_invoice(amount_usd, plan_name, payload):
    if not CRYPTO_BOT_TOKEN:
        return None
    try:
        data = json_module.dumps({
            "asset": "USDT", "amount": str(amount_usd),
            "description": "SellMate AI — " + plan_name,
            "payload": payload, "expires_in": 3600
        }).encode()
        req = urllib.request.Request(
            "https://pay.crypt.bot/api/createInvoice", data=data,
            headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN, "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json_module.loads(resp.read())
            if result.get("ok"):
                return result["result"]
    except Exception:
        pass
    return None

def activate_plan(uid, plan, days):
    expiry = datetime.now() + timedelta(days=days)
    pro_users[uid] = expiry
    user_plan[uid] = plan
    s = get_settings(uid)
    s["monthly_used"] = 0
    return expiry

DEFAULT_COMMANDS = [
    BotCommand("start", "Start / Home"),
    BotCommand("menu", "Main menu"),
    BotCommand("new", "New conversation"),
    BotCommand("balance", "My balance & plan"),
    BotCommand("settings", "Settings"),
    BotCommand("history", "Recent listings"),
    BotCommand("referral", "Invite & earn"),
    BotCommand("subscription", "Upgrade plan"),
    BotCommand("support", "Support"),
    BotCommand("myid", "My Telegram ID")
]

OWNER_COMMANDS = DEFAULT_COMMANDS + [
    BotCommand("activate", "Grant subscription"),
    BotCommand("deactivate", "Revoke subscription"),
    BotCommand("stats", "Bot statistics")
]

try:
    bot.set_my_commands(commands=DEFAULT_COMMANDS, scope=BotCommandScopeDefault())
    bot.set_my_commands(commands=OWNER_COMMANDS, scope=BotCommandScopeChat(OWNER_ID))
except Exception:
    pass

@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    user_history[uid] = []
    all_users.add(uid)

    parts = message.text.split()
    if len(parts) > 1 and parts[1].startswith("ref_") and uid not in referred_by:
        try:
            referrer_id = int(parts[1].replace("ref_", ""))
            if referrer_id != uid:
                referred_by[uid] = referrer_id
                user_free_left[referrer_id] = user_free_left.get(referrer_id, FREE_LIMIT) + REFERRAL_BONUS
                try:
                    bot.send_message(referrer_id, "🎉 Someone joined through your link! You earned +" + str(REFERRAL_BONUS) + " free requests.")
                except Exception:
                    pass
        except ValueError:
            pass

    if uid == OWNER_ID:
        bot.reply_to(message, "👋 Creator mode — unlimited access.\n\n/activate /deactivate /stats available.")
        return

    if uid not in user_free_left:
        user_free_left[uid] = FREE_LIMIT

    bot.reply_to(message, WELCOME_TEXT)
    bot.send_message(message.chat.id, MENU_MAIN_TEXT, reply_markup=build_main_menu_markup())

@bot.message_handler(commands=['menu'])
def menu_command(message):
    bot.reply_to(message, MENU_MAIN_TEXT, reply_markup=build_main_menu_markup())

@bot.message_handler(commands=['new'])
def new_topic(message):
    uid = message.from_user.id
    user_history[uid] = []
    bot.reply_to(message, "🔄 Fresh start! Context cleared.\n\nSend your product name and details — or a photo.")

@bot.message_handler(commands=['balance'])
def balance_command(message):
    uid = message.from_user.id
    if uid == OWNER_ID:
        bot.reply_to(message, "💎 Creator account — unlimited access.")
        return
    plan = get_plan(uid)
    expiry = pro_users.get(uid)
    if expiry and expiry > datetime.now():
        if plan == "starter":
            s = get_settings(uid)
            used = s.get("monthly_used", 0)
            left = max(0, 50 - used)
            bot.reply_to(message, "🥉 Starter Plan\nRequests left this month: " + str(left) + "/50\nExpires: " + expiry.strftime("%m/%d/%Y") + "\n\nUpgrade to Pro for unlimited → /subscription")
        elif plan == "pro":
            bot.reply_to(message, "🥈 Pro Plan — Unlimited requests\nExpires: " + expiry.strftime("%m/%d/%Y"))
        elif plan == "business":
            bot.reply_to(message, "🥇 Business Plan — Unlimited requests + all features\nExpires: " + expiry.strftime("%m/%d/%Y"))
    else:
        left = get_free_left(uid)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💎 View Plans", callback_data="menu_subscription"))
        bot.reply_to(message, "🎁 Free Plan\nRequests remaining: " + str(left) + "/" + str(FREE_LIMIT) + "\n\nUpgrade for unlimited access:", reply_markup=markup)

@bot.message_handler(commands=['support'])
def support_command(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✉️ Contact Support", url="https://t.me/" + OWNER_USERNAME))
    bot.reply_to(message, "🛠️ Support\n\nHave a question or found a bug? Tap below — we reply fast.", reply_markup=markup)

@bot.message_handler(commands=['referral'])
def referral_command(message):
    uid = message.from_user.id
    link = "https://t.me/" + BOT_USERNAME + "?start=ref_" + str(uid)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📤 Share your link", switch_inline_query=link))
    bot.reply_to(message,
        "🎁 Invite & Earn\n\n"
        "Share your referral link and earn free requests for every person who joins.\n\n"
        "You get: +" + str(REFERRAL_BONUS) + " free requests per referral\n"
        "They get: 3 free requests to start\n\n"
        "Your link:\n" + link, reply_markup=markup)

@bot.message_handler(commands=['history'])
def history_command(message):
    uid = message.from_user.id
    items = user_text_history.get(uid, [])
    if not items:
        bot.reply_to(message, "No saved listings yet. Create your first one!")
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for i, item in enumerate(items):
        preview = item.replace("\n", " ")[:50]
        markup.add(InlineKeyboardButton(str(i + 1) + ") " + preview + "...", callback_data="hist_" + str(i)))
    bot.reply_to(message, "📜 Your recent listings — tap to reload and continue editing:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("hist_"))
def history_callback(call):
    uid = call.from_user.id
    idx = int(call.data.replace("hist_", ""))
    items = user_text_history.get(uid, [])
    if idx >= len(items):
        bot.answer_callback_query(call.id, "Not found")
        return
    selected = items[idx]
    settings_ = get_settings(uid)
    user_history[uid] = [
        {"role": "system", "content": build_system_prompt(settings_)},
        {"role": "assistant", "content": selected}
    ]
    bot.answer_callback_query(call.id, "Loaded!")
    bot.send_message(call.message.chat.id, "✅ Listing loaded. Ask me to edit it:\n\n" + selected)

@bot.message_handler(commands=['settings'])
def settings_command(message):
    bot.reply_to(message, SETTINGS_MAIN_TEXT, reply_markup=build_settings_main_markup())

@bot.message_handler(commands=['subscription'])
def subscription_command(message):
    bot.reply_to(message, get_sub_text(), reply_markup=build_sub_markup())

@bot.message_handler(commands=['myid'])
def myid_command(message):
    bot.reply_to(message, "Your Telegram ID: " + str(message.from_user.id))

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != OWNER_ID:
        return
    active = sum(1 for uid, exp in pro_users.items() if exp > datetime.now())
    plans = {}
    for uid, plan in user_plan.items():
        plans[plan] = plans.get(plan, 0) + 1
    bot.reply_to(message,
        "📊 SellMate AI Stats\n\n"
        "Total users: " + str(len(all_users)) + "\n"
        "Active subscriptions: " + str(active) + "\n"
        "Plan breakdown: " + str(plans) + "\n"
        "Referred users: " + str(len(referred_by)))

@bot.message_handler(commands=['activate'])
def activate_command(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        plan = parts[2] if len(parts) > 2 else "pro"
        days = int(parts[3]) if len(parts) > 3 else 30
        expiry = activate_plan(target_id, plan, days)
        expiry_str = expiry.strftime("%m/%d/%Y")
        bot.reply_to(message, "Done. User " + str(target_id) + " — " + plan + " until " + expiry_str)
        try:
            plan_labels = {"starter": "🥉 Starter", "pro": "🥈 Pro", "business": "🥇 Business"}
            bot.send_message(target_id, "✅ Your " + plan_labels.get(plan, plan) + " plan is active until " + expiry_str + ". Welcome to SellMate AI! 🚀")
        except Exception:
            pass
    except (IndexError, ValueError):
        bot.reply_to(message, "Use: /activate 123456789 pro 30")

@bot.message_handler(commands=['deactivate'])
def deactivate_command(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        target_id = int(message.text.split()[1])
        pro_users.pop(target_id, None)
        user_plan.pop(target_id, None)
        bot.reply_to(message, "Subscription revoked for " + str(target_id))
        try:
            bot.send_message(target_id, "Your SellMate AI subscription has been deactivated.")
        except Exception:
            pass
    except (IndexError, ValueError):
        bot.reply_to(message, "Use: /deactivate 123456789")

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def main_menu_callback(call):
    uid = call.from_user.id
    action = call.data.replace("menu_", "")
    bot.answer_callback_query(call.id)
    if action == "main":
        safe_edit(call, MENU_MAIN_TEXT, build_main_menu_markup())
    elif action == "about":
        safe_edit(call,
            "🚀 About SellMate AI\n\n"
            "SellMate AI is the most powerful AI selling assistant built for marketplace sellers worldwide.\n\n"
            "Whether you sell on Etsy, Amazon, eBay, Shopify or any other platform — "
            "SellMate writes listings that rank higher, convert better and save you hours every week.\n\n"
            "Supported platforms:\n"
            "🟠 Etsy   📦 Amazon   🟡 eBay\n"
            "🟢 Shopify   🔵 Facebook Marketplace\n"
            "🟣 Depop   🩷 Poshmark   🔷 Vinted\n\n"
            "Built by sellers, for sellers.",
            build_back_main())
    elif action == "support":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✉️ Contact Support", url="https://t.me/" + OWNER_USERNAME))
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_main"))
        safe_edit(call, "🛠️ Support\n\nSomething wrong or have an idea?\nWe reply fast — tap below.", markup)
    elif action == "subscription":
        safe_edit(call, get_sub_text(), build_sub_markup())
    elif action == "settings":
        safe_edit(call, SETTINGS_MAIN_TEXT, build_settings_main_markup())

@bot.callback_query_handler(func=lambda call: call.data.startswith("quick_"))
def quick_action_callback(call):
    uid = call.from_user.id
    action = call.data.replace("quick_", "")
    bot.answer_callback_query(call.id)

    prompts = {
        "listing": "Great! Send me your product name and details (material, size, color, condition, platform if specific) and I'll write a full listing for you.",
        "keywords": "Send me your product name and platform, and I'll research the best keywords and tags to maximize your visibility.",
        "photo": "Send me a photo of your product and I'll identify it and write a complete listing automatically.",
        "adcopy": "Send me your product details and which platform you want to advertise on (Instagram, TikTok, Pinterest, Facebook Ads) — I'll write scroll-stopping ad copy.",
        "pricing": "Send me your product details (what it is, materials, condition, competition) and I'll suggest a competitive pricing strategy with reasoning.",
        "translate": "Send me the listing you want to translate and tell me the target language — I'll translate it while keeping SEO optimization."
    }

    labels = {
        "listing": "✍️ Write Listing",
        "keywords": "🔍 Keyword Research",
        "photo": "📷 Photo Analysis",
        "adcopy": "📣 Ad Copy",
        "pricing": "💰 Pricing Strategy",
        "translate": "🌍 Translate Listing"
    }

    if action in prompts:
        history = get_user_state(uid)
        s = get_settings(uid)
        if len(history) == 0:
            history.append({"role": "system", "content": build_system_prompt(s)})
        history.append({"role": "assistant", "content": prompts[action]})
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main"))
        bot.send_message(call.message.chat.id, "💬 " + labels.get(action, "") + "\n\n" + prompts[action], reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_open_") or call.data == "set_reset")
def settings_open_callback(call):
    uid = call.from_user.id
    s = get_settings(uid)
    bot.answer_callback_query(call.id)
    if call.data == "set_reset":
        user_settings[uid] = {"model": "smart", "platform": "auto", "tone": "auto", "length": "auto", "language": "en"}
        safe_edit(call, SETTINGS_MAIN_TEXT, build_settings_main_markup())
        return
    section = call.data.replace("set_open_", "")
    if section == "platform":
        safe_edit(call, "🛍️ Choose your default platform:", build_platform_markup(s))
    elif section == "model":
        safe_edit(call, "🤖 Choose AI model:", build_model_markup(s))
    elif section == "tone":
        safe_edit(call, "🎨 Choose writing tone:", build_tone_markup(s))
    elif section == "length":
        safe_edit(call, "📄 Choose listing length:", build_length_markup(s))
    elif section == "language":
        safe_edit(call, "🌍 Choose response language:", build_language_markup(s))

@bot.callback_query_handler(func=lambda call: any(call.data.startswith(p) for p in ["set_platform_", "set_model_", "set_tone_", "set_length_", "set_language_"]))
def settings_value_callback(call):
    uid = call.from_user.id
    s = get_settings(uid)
    data = call.data
    bot.answer_callback_query(call.id, "✅ Updated")
    if data.startswith("set_platform_"):
        s["platform"] = data.replace("set_platform_", "")
        safe_edit(call, "🛍️ Choose your default platform:", build_platform_markup(s))
    elif data.startswith("set_model_"):
        s["model"] = data.replace("set_model_", "")
        safe_edit(call, "🤖 Choose AI model:", build_model_markup(s))
    elif data.startswith("set_tone_"):
        s["tone"] = data.replace("set_tone_", "")
        safe_edit(call, "🎨 Choose writing tone:", build_tone_markup(s))
    elif data.startswith("set_length_"):
        s["length"] = data.replace("set_length_", "")
        safe_edit(call, "📄 Choose listing length:", build_length_markup(s))
    elif data.startswith("set_language_"):
        s["language"] = data.replace("set_language_", "")
        safe_edit(call, "🌍 Choose response language:", build_language_markup(s))

@bot.callback_query_handler(func=lambda call: call.data in ["pay_starter", "pay_pro", "pay_business"])
def stars_payment_callback(call):
    bot.answer_callback_query(call.id)
    plans = {
        "pay_starter": (STARS_STARTER, "Starter", "sub_starter", 30),
        "pay_pro": (STARS_PRO, "Pro", "sub_pro", 30),
        "pay_business": (STARS_BUSINESS, "Business", "sub_business", 30)
    }
    stars, name, payload, days = plans[call.data]
    prices = [LabeledPrice(label="SellMate AI — " + name, amount=stars)]
    bot.send_invoice(call.message.chat.id,
        title="SellMate AI " + name,
        description="Unlock " + name + " features for 30 days",
        invoice_payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices)

@bot.callback_query_handler(func=lambda call: call.data == "pay_usdt")
def usdt_menu_callback(call):
    bot.answer_callback_query(call.id)
    safe_edit(call, "💰 Pay with USDT\n\nChoose your plan:", build_usdt_markup())

@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_usdt_"))
def usdt_plan_callback(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
    plans = {
        "pay_usdt_starter": (2.99, "Starter", "crypto_starter", 30),
        "pay_usdt_pro": (6.99, "Pro", "crypto_pro", 30),
        "pay_usdt_business": (16.99, "Business", "crypto_business", 30)
    }
    plan = plans.get(call.data)
    if not plan:
        return
    amount, name, payload, days = plan
    invoice = create_crypto_invoice(amount, name, payload + "_" + str(uid) + "_" + str(days))
    if invoice:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💰 Pay $" + str(amount) + " USDT", url=invoice["pay_url"]))
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="pay_usdt"))
        bot.send_message(call.message.chat.id,
            "💰 " + name + " — $" + str(amount) + " USDT\n\n"
            "Tap to pay. Activates automatically after payment.",
            reply_markup=markup)
    else:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✉️ Contact support", url="https://t.me/" + OWNER_USERNAME))
        bot.send_message(call.message.chat.id,
            "⚠️ Crypto payments are being configured.\nPlease contact support to pay via USDT.",
            reply_markup=markup)

@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    uid = message.from_user.id
    payload = message.successful_payment.invoice_payload
    plan_map = {
        "sub_starter": ("starter", 30),
        "sub_pro": ("pro", 30),
        "sub_business": ("business", 30)
    }
    plan_info = plan_map.get(payload, ("pro", 30))
    plan_name, days = plan_info
    expiry = activate_plan(uid, plan_name, days)
    labels = {"starter": "🥉 Starter", "pro": "🥈 Pro", "business": "🥇 Business"}
    bot.reply_to(message,
        "✅ Payment confirmed!\n\n"
        + labels.get(plan_name, plan_name) + " Plan is now active.\n"
        "Valid until: " + expiry.strftime("%m/%d/%Y") + "\n\n"
        "Let's go! Send me a product to write your first listing. 🚀")

def send_limit_message(chat_id):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🥈 Pro — 500 ⭐/mo (Unlimited)", callback_data="pay_pro"),
        InlineKeyboardButton("🥉 Starter — 200 ⭐/mo (50 req)", callback_data="pay_starter"),
        InlineKeyboardButton("🎁 Invite friends for free requests", callback_data="referral_hint")
    )
    bot.send_message(chat_id,
        "✨ You've used all your free requests!\n\n"
        "Upgrade to keep writing listings that sell:\n\n"
        "🥈 Pro — Unlimited requests · All platforms · Ad copy · Keywords\n"
        "🥉 Starter — 50 requests/month · Perfect to get started\n\n"
        "Or invite a friend and earn +" + str(REFERRAL_BONUS) + " free requests each.",
        reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "referral_hint")
def referral_hint(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
    link = "https://t.me/" + BOT_USERNAME + "?start=ref_" + str(uid)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📤 Share link", switch_inline_query=link))
    bot.send_message(call.message.chat.id, "Your referral link:\n" + link, reply_markup=markup)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    all_users.add(uid)
    if not has_requests(uid):
        send_limit_message(message.chat.id)
        return
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        b64 = base64.b64encode(downloaded).decode('utf-8')
        caption = message.caption if message.caption else "Identify this product and write a complete marketplace listing with title, description, and tags."
        s = get_settings(uid)
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": build_system_prompt(s) + " If the user's caption explicitly names the item or brand, trust that completely over your visual guess."},
                {"role": "user", "content": [
                    {"type": "text", "text": caption},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}
                ]}
            ],
            max_tokens=800
        )
        text = clean_text(response.choices[0].message.content)
        add_to_text_history(uid, text)
        deduct_request(uid)
        bot.reply_to(message, text + get_footer(uid))
    except Exception:
        bot.reply_to(message, "Couldn't process the photo. Try again or describe the item in text.")

@bot.message_handler(func=lambda m: True)
def generate(message):
    uid = message.from_user.id
    all_users.add(uid)
    history = get_user_state(uid)
    settings_ = get_settings(uid)

    if not has_requests(uid):
        send_limit_message(message.chat.id)
        return

    bot.send_chat_action(message.chat.id, 'typing')

    if len(history) == 0:
        history.append({"role": "system", "content": build_system_prompt(settings_)})

    history.append({"role": "user", "content": message.text})
    trimmed = [history[0]] + history[-11:] if len(history) > 12 else history
    model_name = MODELS.get(settings_.get("model", "smart"), MODELS["smart"])

    try:
        response = client.chat.completions.create(model=model_name, messages=trimmed, max_tokens=800, temperature=0.8)
        text = clean_text(response.choices[0].message.content)
        history.append({"role": "assistant", "content": text})
        add_to_text_history(uid, text)
        deduct_request(uid)
        bot.reply_to(message, text + get_footer(uid))
    except Exception:
        bot.reply_to(message, "Something went wrong. Please try again in a minute.")

print("SellMate AI is running...")
bot.polling(none_stop=True)
