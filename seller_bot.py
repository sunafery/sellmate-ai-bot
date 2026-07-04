import telebot
from telebot.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
import re, os, base64, time, threading, json as json_module
import urllib.request, urllib.parse
from datetime import datetime, timedelta
from groq import Groq

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "sellmate_ai_bot")

bot    = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

OWNER_ID       = 1249820876
OWNER_USERNAME = "sunafery"

FREE_LIMIT     = 3
REFERRAL_BONUS = 3

STARS_STARTER  = 200
STARS_PRO      = 500
STARS_BUSINESS = 1200

MODELS = {
    "smart": "llama-3.3-70b-versatile",
    "fast":  "llama-3.1-8b-instant"
}
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
user_free_left        = {}
user_history          = {}
pro_users             = {}
user_settings         = {}
user_text_history     = {}
referred_by           = {}
all_users             = set()
user_plan             = {}
user_first_seen       = {}
user_last_request     = {}
pending_crypto        = {}   # invoice_id -> {uid, plan, days}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def clean(text):
    return re.sub(r'[\u3040-\u30ff\uac00-\ud7af\u4e00-\u9fff]', '', text)

def get_settings(uid):
    if uid not in user_settings:
        user_settings[uid] = {"model":"smart","platform":"auto","tone":"auto","length":"auto","language":"en"}
    return user_settings[uid]

def get_plan(uid):
    return user_plan.get(uid, "free")

def is_unlimited(uid):
    if uid == OWNER_ID: return True
    plan   = get_plan(uid)
    expiry = pro_users.get(uid)
    return plan != "free" and expiry is not None and expiry > datetime.now()

def has_requests(uid):
    if is_unlimited(uid):
        if get_plan(uid) == "starter":
            return get_settings(uid).get("monthly_used", 0) < 50
        return True
    return user_free_left.get(uid, FREE_LIMIT) > 0

def deduct(uid):
    if uid == OWNER_ID: return
    plan = get_plan(uid)
    if plan == "pro" or plan == "business": return
    if plan == "starter":
        s = get_settings(uid); s["monthly_used"] = s.get("monthly_used",0) + 1; return
    user_free_left[uid] = max(0, user_free_left.get(uid, FREE_LIMIT) - 1)

def get_free_left(uid):
    if uid not in user_free_left: user_free_left[uid] = FREE_LIMIT
    return user_free_left[uid]

def get_footer(uid):
    if uid == OWNER_ID or get_plan(uid) in ("pro","business"): return ""
    if get_plan(uid) == "starter":
        left = max(0, 50 - get_settings(uid).get("monthly_used",0))
        return "\n\n─────────────────\n🥉 Starter · " + str(left) + " requests left this month"
    left = get_free_left(uid)
    if left <= 0: return "\n\n─────────────────\n❌ No requests left · /subscription"
    return "\n\n─────────────────\n🎁 Free requests: " + str(left) + " left · /subscription"

def add_history(uid, text):
    if uid not in user_text_history: user_text_history[uid] = []
    user_text_history[uid].append({"text": text, "ts": datetime.now().strftime("%b %d, %H:%M")})
    if len(user_text_history[uid]) > 10: user_text_history[uid].pop(0)

def get_first_name(msg):
    return msg.from_user.first_name or "there"

def is_returning(uid):
    return uid in user_first_seen and (datetime.now() - user_first_seen[uid]).days > 0

def activate_plan(uid, plan, days):
    expiry = datetime.now() + timedelta(days=days)
    pro_users[uid] = expiry
    user_plan[uid] = plan
    get_settings(uid)["monthly_used"] = 0
    return expiry

def safe_edit(call_or_cid, text, markup, mid=None):
    try:
        if hasattr(call_or_cid, 'message'):
            bot.edit_message_text(text, call_or_cid.message.chat.id, call_or_cid.message.message_id, reply_markup=markup)
        else:
            bot.edit_message_text(text, call_or_cid, mid, reply_markup=markup)
    except Exception:
        cid = call_or_cid.message.chat.id if hasattr(call_or_cid,'message') else call_or_cid
        bot.send_message(cid, text, reply_markup=markup)

# ─────────────────────────────────────────────
# CRYPTO BOT — ИСПРАВЛЕННАЯ ВЕРСИЯ (как на видео)
# ─────────────────────────────────────────────
def crypto_request(method, params=None):
    if not CRYPTO_BOT_TOKEN: 
        print("❌ CRYPTO_BOT_TOKEN not set")
        return None
    try:
        data = json_module.dumps(params or {}).encode()
        req  = urllib.request.Request(
            "https://pay.crypt.bot/api/" + method, 
            data=data,
            headers={
                "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN, 
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json_module.loads(r.read().decode('utf-8'))
            if not result.get("ok"):
                print(f"❌ Crypto API Error: {result.get('error')}")
                return None
            return result.get("result")
    except Exception as e:
        print(f"❌ Crypto request error: {e}")
        return None

def create_invoice(amount, label, payload):
    return crypto_request("createInvoice", {
        "asset": "USDT", 
        "amount": str(amount),
        "description": "SellMate AI — " + label,
        "payload": payload, 
        "expires_in": 3600,
        "allow_anonymous": False
    })

def create_crypto_invoice(user_id: int, plan: str):
    """Создание invoice (как на видео)"""
    prices = {
        "starter":  (2.99,  "Starter",  30),
        "pro":      (6.99,  "Pro",      30),
        "business": (16.99, "Business", 30),
    }
    if plan not in prices:
        print("❌ Unknown plan")
        return None
    amount, label, days = prices[plan]
    payload = f"crypto_{plan}_{user_id}_{days}"
    
    print(f"🔄 Creating invoice → User: {user_id} | Plan: {plan} | ${amount}")
    
    invoice = create_invoice(amount, label, payload)
    
    if invoice and isinstance(invoice, dict):
        print(f"✅ Invoice created! ID: {invoice.get('invoice_id')}")
        inv_id = str(invoice.get("invoice_id"))
        if inv_id:
            pending_crypto[inv_id] = {"uid": user_id, "plan": plan, "days": days}
        return invoice
    else:
        print("❌ Failed to create invoice from API")
        return None

# ─────────────────────────────────────────────
# POLLING
# ─────────────────────────────────────────────
def poll_crypto():
    while True:
        time.sleep(6)
        if not pending_crypto or not CRYPTO_BOT_TOKEN: continue
        try:
            ids  = list(pending_crypto.keys())[:100]
            data = crypto_request("getInvoices", {"invoice_ids": ids})
            if not data: continue
            items = data.get("items", []) if isinstance(data, dict) else []
            for inv in items:
                iid    = str(inv.get("invoice_id",""))
                status = inv.get("status","")
                if status == "paid" and iid in pending_crypto:
                    info   = pending_crypto.pop(iid)
                    expiry = activate_plan(info["uid"], info["plan"], info["days"])
                    labels = {"starter":"🥉 Starter","pro":"🥈 Pro","business":"🥇 Business"}
                    try:
                        bot.send_message(info["uid"],
                            "✅ Payment confirmed!\n\n"
                            + labels.get(info["plan"],"") + " plan activated.\n"
                            "Valid until: " + expiry.strftime("%b %d, %Y") + "\n\n"
                            "Let's build your next bestseller 🚀")
                    except Exception: pass
        except Exception: pass

threading.Thread(target=poll_crypto, daemon=True).start()

# ─────────────────────────────────────────────
# AI GENERATION WITH PROGRESS (остальное без изменений)
# ─────────────────────────────────────────────
def make_progress_bar(pct):
    filled = int(pct / 10)
    empty  = 10 - filled
    bar    = "█" * filled + "░" * empty
    return "⚡ SellMate AI is working...\n\n[" + bar + "]  " + str(pct) + "%"

PROGRESS_STEPS = [0, 10, 25, 40, 55, 70, 82, 91, 97, 100]

def run_progress(chat_id, progress_msg_id):
    for pct in PROGRESS_STEPS[1:]:
        time.sleep(0.6)
        try:
            bot.edit_message_text(make_progress_bar(pct), chat_id, progress_msg_id)
        except Exception:
            pass

def build_system_prompt(s):
    platform = s.get("platform","auto")
    tone     = s.get("tone","auto")
    length   = s.get("length","auto")
    lang     = s.get("language","en")

    lang_map = {
        "en": "English",
        "es": "Spanish (Español) — write everything in Spanish",
        "de": "German (Deutsch) — write everything in German",
        "fr": "French (Français) — write everything in French",
        "it": "Italian (Italiano) — write everything in Italian",
        "pt": "Portuguese (Português) — write everything in Portuguese",
        "ru": "Russian (Русский) — write everything in Russian",
        "ja": "Japanese — write everything in Japanese using appropriate script",
        "zh": "Chinese Simplified — write everything in Simplified Chinese",
        "ar": "Arabic — write everything in Arabic"
    }
    lang_full = lang_map.get(lang, "English")

    platform_map = {
        "etsy":     "Etsy — emotional, handmade/vintage focus. Title max 140 chars. 13 tags (comma separated). Story-driven description.",
        "amazon":   "Amazon — conversion-focused. Title max 200 chars. 5 bullet points starting with capital letter. Keyword-dense.",
        "ebay":     "eBay — factual, honest. Exact condition, specs, what's included. Buyers want facts.",
        "shopify":  "Shopify/DTC — brand storytelling. Lifestyle focus. How product makes customer feel.",
        "facebook": "Facebook Marketplace — casual, local. Short, mention price range and condition.",
        "depop":    "Depop — Gen-Z trendy. Short, punchy. Sizing, condition, styling tips. 3-5 hashtags.",
        "poshmark": "Poshmark — polished. Brand name prominent. Measurements, condition, original retail price.",
        "vinted":   "Vinted — honest European style. Measurements, flaws, washing instructions.",
        "auto":     "Auto-detect best format: handmade/vintage → Etsy style; mass-market → Amazon style."
    }

    tone_map = {
        "professional": "Use professional, authoritative tone.",
        "friendly":     "Use warm, friendly, conversational tone.",
        "luxury":       "Use elevated luxury tone — sophisticated, aspirational.",
        "casual":       "Use casual, laid-back tone."
    }

    length_map = {"short": "3-5 sentences max.", "medium": "6-10 sentences.", "long": "12+ sentences, very detailed."}

    return (
        "You are SellMate AI, the world's most advanced marketplace selling assistant. "
        "You help sellers on Etsy, Amazon, eBay, Shopify, Facebook, Depop, Poshmark, Vinted.\n\n"
        "Platform: " + platform_map.get(platform, platform_map["auto"]) + "\n"
        + (tone_map.get(tone,"") + " " if tone in tone_map else "")
        + (length_map.get(length,"") + " " if length in length_map else "")
        + "\n\nMANDATORY LANGUAGE RULE: " + lang_full + ". "
        "You MUST write your ENTIRE response in this language only. "
        "Every word, every label, every sentence must be in this language. "
        "Do NOT use English unless the language IS English. No exceptions.\n\n"
        "When asked to write a listing, return ONLY a JSON object (no extra text before or after):\n"
        '{"title":"...","description":"...","tags":"tag1, tag2, ...","keywords":"kw1, kw2, ...","photo_tips":"...","selling_tips":"..."}\n\n'
        "All values inside the JSON must also be in the specified language.\n\n"
        "For all other requests respond naturally in plain text in the specified language.\n\n"
        "Rules:\n"
        "- Trust user-stated brand/model completely\n"
        "- No invented facts\n"
        "- Think like a buyer, write for buyers\n"
        "- Use conversation context for edits\n"
        "- No unsolicited offers to rewrite"
    )

def parse_listing(raw):
    try:
        clean_raw = raw.strip()
        clean_raw = re.sub(r"^```[a-z]*\n?", "", clean_raw)
        clean_raw = re.sub(r"\n?```$", "", clean_raw).strip()
        match = re.search(r'\{[\s\S]*\}', clean_raw)
        if match:
            return json_module.loads(match.group())
    except Exception:
        pass
    return None

def format_listing(d, uid):
    sep   = "━━━━━━━━━━━━━━━━━━━━━━"
    lines = []

    def block(icon, label, content):
        if content and content.strip():
            lines.append(sep)
            lines.append(icon + "  " + label)
            lines.append(sep)
            lines.append(content.strip())
            lines.append("")

    block("📝", "SEO Title",    d.get("title",""))
    block("📄", "Description",  d.get("description",""))
    block("🏷", "Tags",         d.get("tags",""))
    block("🔑", "Keywords",     d.get("keywords",""))
    block("📸", "Photo Tips",   d.get("photo_tips",""))
    block("📈", "Selling Tips", d.get("selling_tips",""))

    if lines:
        lines.append(sep)

    return "\n".join(lines) + get_footer(uid)

def post_action_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📋 Copy Title",       callback_data="copy_title"),
        InlineKeyboardButton("📄 Copy Description", callback_data="copy_desc"),
        InlineKeyboardButton("🏷 Copy Tags",        callback_data="copy_tags"),
        InlineKeyboardButton("✏️ Improve",          callback_data="action_improve"),
        InlineKeyboardButton("🔄 Regenerate",       callback_data="action_regen"),
        InlineKeyboardButton("🌍 Translate",        callback_data="action_translate")
    )
    return markup

# ─────────────────────────────────────────────
# MENUS (остальное без изменений)
# ─────────────────────────────────────────────
def build_main_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✨ Create Listing", callback_data="quick_listing"),
        InlineKeyboardButton("📸 Scan Product",  callback_data="quick_photo"),
        InlineKeyboardButton("🔍 Improve Listing",callback_data="quick_improve"),
        InlineKeyboardButton("💡 Product Ideas", callback_data="quick_ideas"),
        InlineKeyboardButton("🌍 Translate",     callback_data="quick_translate"),
        InlineKeyboardButton("📣 Ad Copy",       callback_data="quick_adcopy")
    )
    markup.add(
        InlineKeyboardButton("💎 Plans",   callback_data="menu_subscription"),
        InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings")
    )
    markup.add(InlineKeyboardButton("📜 History", callback_data="menu_history"))
    return markup

def build_back(): 
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_main"))
    return m

def build_sub_markup():
    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("🥉 Starter — 200 ⭐/mo",   callback_data="pay_starter"),
        InlineKeyboardButton("🥈 Pro — 500 ⭐/mo",       callback_data="pay_pro"),
        InlineKeyboardButton("🥇 Business — 1200 ⭐/mo", callback_data="pay_business"),
        InlineKeyboardButton("💰 Pay with USDT (crypto)", callback_data="pay_usdt"),
        InlineKeyboardButton("💳 Pay with Crypto", callback_data="pay_crypto"),
        InlineKeyboardButton("⬅️ Back", callback_data="menu_main")
    )
    return m

def build_usdt_markup():
    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("🥉 Starter — $2.99 USDT",   callback_data="pay_usdt_starter"),
        InlineKeyboardButton("🥈 Pro — $6.99 USDT",       callback_data="pay_usdt_pro"),
        InlineKeyboardButton("🥇 Business — $16.99 USDT", callback_data="pay_usdt_business"),
        InlineKeyboardButton("⬅️ Back", callback_data="menu_subscription")
    )
    return m

def build_settings_markup():
    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("🛍️ Platform  >",   callback_data="set_open_platform"),
        InlineKeyboardButton("🤖 AI Model  >",   callback_data="set_open_model"),
        InlineKeyboardButton("🎨 Tone  >",        callback_data="set_open_tone"),
        InlineKeyboardButton("📄 Length  >",      callback_data="set_open_length"),
        InlineKeyboardButton("🌍 Language  >",    callback_data="set_open_language"),
        InlineKeyboardButton("🔄 Reset defaults", callback_data="set_reset"),
        InlineKeyboardButton("⬅️ Back",           callback_data="menu_main")
    )
    return m

def build_platform_markup(s):
    opts = [("auto","🤖 Auto"),("etsy","🟠 Etsy"),("amazon","📦 Amazon"),("ebay","🟡 eBay"),
            ("shopify","🟢 Shopify"),("facebook","🔵 Facebook"),("depop","🟣 Depop"),
            ("poshmark","🩷 Poshmark"),("vinted","🔷 Vinted")]
    opts.sort(key=lambda x: 0 if x[0]==s["platform"] else 1)
    m = InlineKeyboardMarkup(row_width=1)
    for k,l in opts:
        m.add(InlineKeyboardButton(("✅ " if s["platform"]==k else "")+l, callback_data="set_platform_"+k))
    m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return m

def build_model_markup(s):
    opts = [("smart","🧠 Smart (accurate)"),("fast","⚡ Fast (instant)")]
    opts.sort(key=lambda x: 0 if x[0]==s["model"] else 1)
    m = InlineKeyboardMarkup(row_width=1)
    for k,l in opts:
        m.add(InlineKeyboardButton(("✅ " if s["model"]==k else "")+l, callback_data="set_model_"+k))
    m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return m

def build_tone_markup(s):
    opts = [("auto","🤖 Auto"),("professional","👔 Professional"),("friendly","😊 Friendly"),
            ("luxury","💎 Luxury"),("casual","😎 Casual")]
    opts.sort(key=lambda x: 0 if x[0]==s["tone"] else 1)
    m = InlineKeyboardMarkup(row_width=1)
    for k,l in opts:
        m.add(InlineKeyboardButton(("✅ " if s["tone"]==k else "")+l, callback_data="set_tone_"+k))
    m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return m

def build_length_markup(s):
    opts = [("auto","🤖 Auto"),("short","📌 Short"),("medium","📝 Medium"),("long","📋 Detailed")]
    opts.sort(key=lambda x: 0 if x[0]==s["length"] else 1)
    m = InlineKeyboardMarkup(row_width=1)
    for k,l in opts:
        m.add(InlineKeyboardButton(("✅ " if s["length"]==k else "")+l, callback_data="set_length_"+k))
    m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return m

def build_language_markup(s):
    opts = [("en","🇬🇧 English"),("es","🇪🇸 Spanish"),("de","🇩🇪 German"),("fr","🇫🇷 French"),
            ("it","🇮🇹 Italian"),("pt","🇧🇷 Portuguese"),("ru","🇷🇺 Russian"),
            ("ja","🇯🇵 Japanese"),("zh","🇨🇳 Chinese"),("ar","🇸🇦 Arabic")]
    opts.sort(key=lambda x: 0 if x[0]==s["language"] else 1)
    m = InlineKeyboardMarkup(row_width=2)
    m.add(*[InlineKeyboardButton(("✅ " if s["language"]==k else "")+l, callback_data="set_language_"+k) for k,l in opts])
    m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_settings"))
    return m

# ─────────────────────────────────────────────
# COMMANDS SETUP
# ─────────────────────────────────────────────
DEFAULT_CMDS = [
    BotCommand("start", "Home"),
    BotCommand("menu", "Main menu"),
    BotCommand("new", "New conversation"),
    BotCommand("balance", "My plan & balance"),
    BotCommand("history", "Saved listings"),
    BotCommand("referral", "Invite & earn"),
    BotCommand("subscription", "Upgrade plan"),
    BotCommand("settings", "Settings"),
    BotCommand("support", "Support"),
    BotCommand("myid", "My Telegram ID"),
]
OWNER_CMDS = DEFAULT_CMDS + [
    BotCommand("activate", "Grant subscription"),
    BotCommand("deactivate", "Revoke subscription"),
    BotCommand("stats", "Statistics"),
]
try:
    bot.set_my_commands(DEFAULT_CMDS, scope=BotCommandScopeDefault())
    bot.set_my_commands(OWNER_CMDS, scope=BotCommandScopeChat(OWNER_ID))
except Exception: pass

# ─────────────────────────────────────────────
# /start и все остальные handlers (без изменений)
# ─────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def start(message):
    uid  = message.from_user.id
    name = get_first_name(message)
    user_history[uid] = []
    all_users.add(uid)

    parts = message.text.split()
    if len(parts) > 1 and parts[1].startswith("ref_") and uid not in referred_by:
        try:
            rid = int(parts[1].replace("ref_",""))
            if rid != uid:
                referred_by[uid] = rid
                user_free_left[rid] = user_free_left.get(rid, FREE_LIMIT) + REFERRAL_BONUS
                try: bot.send_message(rid, "🎉  " + name + " joined through your link!\nYou earned +" + str(REFERRAL_BONUS) + " free requests.")
                except Exception: pass
        except ValueError: pass

    if uid == OWNER_ID:
        bot.reply_to(message, "👑  Creator mode — unlimited access.")
        return

    if uid not in user_free_left: user_free_left[uid] = FREE_LIMIT

    returning = is_returning(uid)
    user_first_seen.setdefault(uid, datetime.now())

    if returning:
        greeting = (
            "👋  Welcome back, " + name + "!\n\n"
            "Ready to create another bestseller?\n\n"
            "Your workspace:"
        )
    else:
        greeting = (
            "✨  Welcome to SellMate AI\n\n"
            "Your AI-powered selling assistant for every marketplace.\n\n"
            "Write listings, research keywords, analyze photos and grow your sales — "
            "all in seconds.\n\n"
            "🎁  You have " + str(FREE_LIMIT) + " free requests to start."
        )

    bot.reply_to(message, greeting)
    bot.send_message(message.chat.id, "What would you like to do?", reply_markup=build_main_menu())

# ... (все остальные функции до конца файла остаются без изменений, как в твоём файле)

@bot.callback_query_handler(func=lambda c: c.data in ("pay_starter","pay_pro","pay_business"))
def cb_stars_pay(call):
    bot.answer_callback_query(call.id)
    plans = {
        "pay_starter":  (STARS_STARTER,  "Starter",  "sub_starter",  30),
        "pay_pro":      (STARS_PRO,      "Pro",      "sub_pro",      30),
        "pay_business": (STARS_BUSINESS, "Business", "sub_business", 30),
    }
    stars, name, payload, days = plans[call.data]
    bot.send_invoice(call.message.chat.id,
        title="SellMate AI — " + name,
        description="Unlock " + name + " for 30 days",
        invoice_payload=payload,
        provider_token="", currency="XTR",
        prices=[LabeledPrice("SellMate AI "+name, stars)])

@bot.callback_query_handler(func=lambda c: c.data == "pay_usdt")
def cb_usdt_menu(call):
    bot.answer_callback_query(call.id)
    safe_edit(call, "💰  Pay with USDT\n\nChoose your plan — activates automatically:", build_usdt_markup())

@bot.callback_query_handler(func=lambda c: c.data.startswith("pay_usdt_"))
def cb_usdt_plan(call):
    uid  = call.from_user.id
    bot.answer_callback_query(call.id)
    plans = {
        "pay_usdt_starter":  (2.99,  "Starter",  "starter",  30),
        "pay_usdt_pro":      (6.99,  "Pro",       "pro",      30),
        "pay_usdt_business": (16.99, "Business",  "business", 30),
    }
    plan = plans.get(call.data)
    if not plan: return
    amount, label, plan_key, days = plan

    invoice = create_invoice(amount, label, "usdt_"+plan_key+"_"+str(uid)+"_"+str(days))
    if invoice:
        inv_id = str(invoice.get("invoice_id",""))
        if inv_id: pending_crypto[inv_id] = {"uid":uid,"plan":plan_key,"days":days}
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("💰  Pay $"+str(amount)+" USDT", url=invoice.get("pay_url","")))
        m.add(InlineKeyboardButton("⬅️ Back", callback_data="pay_usdt"))
        bot.send_message(call.message.chat.id,
            "💰  " + label + " — $" + str(amount) + " USDT\n\n"
            "Tap to pay. Subscription activates automatically within seconds after payment.",
            reply_markup=m)
    else:
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("✉️  Contact support", url="https://t.me/"+OWNER_USERNAME))
        bot.send_message(call.message.chat.id,
            "⚠️  Crypto payments are being configured.\nContact support to pay via USDT.", reply_markup=m)

@bot.callback_query_handler(func=lambda c: c.data == "ref_hint")
def cb_ref_hint(call):
    uid  = call.from_user.id
    bot.answer_callback_query(call.id)
    link = "https://t.me/"+BOT_USERNAME+"?start=ref_"+str(uid)
    m    = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("📤  Share link", switch_inline_query=link))
    bot.send_message(call.message.chat.id, "Your referral link:\n"+link, reply_markup=m)

@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    uid     = message.from_user.id
    payload = message.successful_payment.invoice_payload
    plan_map = {"sub_starter":("starter",30),"sub_pro":("pro",30),"sub_business":("business",30)}
    plan_key, days = plan_map.get(payload,("pro",30))
    expiry = activate_plan(uid, plan_key, days)
    labels = {"starter":"🥉 Starter","pro":"🥈 Pro","business":"🥇 Business"}
    bot.reply_to(message,
        "✅  Payment confirmed!\n\n"
        + labels.get(plan_key,"") + " plan is now active.\n"
        "Valid until: " + expiry.strftime("%b %d, %Y") + "\n\n"
        "Let's build your next bestseller 🚀",
        reply_markup=build_main_menu())

# ─────────────────────────────────────────────
# CRYPTO PAY CALLBACKS
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "pay_crypto")
def cb_crypto_pay(call):
    bot.answer_callback_query(call.id, "Creating payment...")
    safe_edit(call, "💳 Pay with Crypto (USDT)\n\nChoose plan:", build_crypto_markup())

def build_crypto_markup():
    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("🥉 Starter — $2.99", callback_data="crypto_starter"),
        InlineKeyboardButton("🥈 Pro — $6.99", callback_data="crypto_pro"),
        InlineKeyboardButton("🥇 Business — $16.99", callback_data="crypto_business"),
        InlineKeyboardButton("⬅️ Back", callback_data="menu_subscription")
    )
    return m

@bot.callback_query_handler(func=lambda c: c.data.startswith("crypto_"))
def cb_process_crypto(call):
    uid = call.from_user.id
    plan = call.data.replace("crypto_", "")
    bot.answer_callback_query(call.id, "Creating payment...")
    
    invoice = create_crypto_invoice(uid, plan)
    if not invoice:
        bot.send_message(uid, "❌ Could not create invoice.\nMake sure CRYPTO_BOT_TOKEN is correct.")
        return
    
    url = invoice.get("bot_invoice_url")
    if not url:
        bot.send_message(uid, "❌ No payment link.")
        return
    
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("💳 Pay Now", url=url))
    
    bot.send_message(uid,
        f"✅ Payment for **{plan.capitalize()}** ready!\n"
        f"Amount: ${[2.99,6.99,16.99][['starter','pro','business'].index(plan)]} USDT\n\n"
        "Tap button to pay inside Telegram (as in the video).",
        reply_markup=m, parse_mode="Markdown")

# ─────────────────────────────────────────────
# CORE GENERATION (без изменений)
# ─────────────────────────────────────────────
def _generate_with_progress(uid, chat_id, history, s):
    progress_msg = bot.send_message(chat_id, make_progress_bar(0))

    def run_stages():
        for stage in ["⚡ Analyzing...", "🧠 Thinking...", "✍️ Writing listing..."]:
            time.sleep(0.7)
            try: bot.edit_message_text(stage, chat_id, progress_msg.message_id)
            except Exception: pass

    t = threading.Thread(target=run_stages, daemon=True)
    t.start()

    model   = MODELS.get(s.get("model","smart"), MODELS["smart"])
    trimmed = [history[0]] + history[-11:] if len(history)>12 else history

    try:
        resp = client.chat.completions.create(model=model, messages=trimmed, max_tokens=900, temperature=0.85)
        raw  = clean(resp.choices[0].message.content)
        history.append({"role":"assistant","content":raw})
        t.join(timeout=6)

        parsed = parse_listing(raw)
        if parsed:
            text = format_listing(parsed, uid)
        else:
            text = raw + get_footer(uid)

        add_history(uid, text)
        deduct(uid)

        try: bot.delete_message(chat_id, progress_msg.message_id)
        except Exception: pass

        bot.send_message(chat_id, text, reply_markup=post_action_markup())
    except Exception:
        t.join(timeout=2)
        try: bot.edit_message_text("Something went wrong. Please try again.", chat_id, progress_msg.message_id)
        except Exception: pass

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    all_users.add(uid)
    if not has_requests(uid): send_limit_msg(message.chat.id); return

    bot.send_chat_action(message.chat.id,'typing')
    s = get_settings(uid)

    try:
        fi   = bot.get_file(message.photo[-1].file_id)
        data = bot.download_file(fi.file_path)
        b64  = base64.b64encode(data).decode()
        cap  = message.caption or "Identify this product and write a complete marketplace listing."
        resp = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role":"system","content":build_system_prompt(s) +
                 " If caption explicitly names item/brand, trust that completely over visual guess."},
                {"role":"user","content":[
                    {"type":"text","text":cap},
                    {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}
                ]}
            ], max_tokens=900)
        raw    = clean(resp.choices[0].message.content)
        parsed = parse_listing(raw)
        text   = format_listing(parsed, uid) if parsed else raw + get_footer(uid)
        add_history(uid, text)
        deduct(uid)
        bot.reply_to(message, text, reply_markup=post_action_markup())
    except Exception:
        bot.reply_to(message,"Couldn't process the photo. Try describing the item in text.")

@bot.message_handler(func=lambda m: True)
def generate(message):
    uid  = message.from_user.id
    all_users.add(uid)
    if not has_requests(uid): send_limit_msg(message.chat.id); return

    last = user_last_request.get(uid,"")
    if message.text.strip().lower() == last.strip().lower() and last:
        items = user_text_history.get(uid,[])
        bot.reply_to(message,
            "♻️  Same request detected — no credit charged.\n\n"
            "Here's your previous result:"
            + (("\n\n"+items[-1]["text"]) if items else "") + get_footer(uid),
            reply_markup=post_action_markup() if items else None)
        return

    s       = get_settings(uid)
    history = user_history.setdefault(uid,[])

    if not history:
        history.append({"role":"system","content":build_system_prompt(s)})

    history.append({"role":"user","content":message.text})
    user_last_request[uid] = message.text

    is_listing_request = any(kw in message.text.lower() for kw in [
        "write","create","listing","mug","shirt","bag","jacket","shoes","sneakers","handmade",
        "vintage","digital","print","poster","necklace","ring","candle","soap","art","photo"
    ])

    if is_listing_request and len(message.text) > 15:
        _generate_with_progress(uid, message.chat.id, history, s)
    else:
        bot.send_chat_action(message.chat.id,'typing')
        model   = MODELS.get(s.get("model","smart"), MODELS["smart"])
        trimmed = [history[0]] + history[-11:] if len(history)>12 else history
        try:
            resp = client.chat.completions.create(model=model, messages=trimmed, max_tokens=800, temperature=0.8)
            text = clean(resp.choices[0].message.content)
            history.append({"role":"assistant","content":text})
            add_history(uid, text)
            deduct(uid)
            bot.reply_to(message, text + get_footer(uid))
        except Exception:
            bot.reply_to(message,"Something went wrong. Please try again.")

print("SellMate AI is running...")
bot.polling(none_stop=True)
