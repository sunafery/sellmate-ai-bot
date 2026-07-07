import telebot
from telebot.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
import re, os, base64, time, threading, json as json_module
import urllib.request
from datetime import datetime, timedelta
from groq import Groq
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
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
# CRYPTO PAY (без CryptoBot — Railway блокирует pay.crypt.bot)
# Используем Telegram Stars для автоматической оплаты
# USDT — ручная обработка через support
# ─────────────────────────────────────────────
USDT_ADDRESS = os.environ.get("USDT_ADDRESS", "")
CRYPTO_READY = True  # Stars всегда работают

# ─────────────────────────────────────────────
# AI GENERATION WITH PROGRESS
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
# MENUS
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
# /start
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

# ─────────────────────────────────────────────
# SIMPLE COMMANDS
# ─────────────────────────────────────────────
@bot.message_handler(commands=['menu'])
def cmd_menu(message):
    bot.reply_to(message, "📋  Main menu", reply_markup=build_main_menu())

@bot.message_handler(commands=['new'])
def cmd_new(message):
    uid = message.from_user.id
    user_history[uid] = []
    user_last_request.pop(uid, None)
    bot.reply_to(message, "🔄  Fresh start! Send me a product name or photo.")

@bot.message_handler(commands=['settings'])
def cmd_settings(message):
    bot.reply_to(message, "⚙️  Settings", reply_markup=build_settings_markup())

@bot.message_handler(commands=['subscription'])
def cmd_sub(message):
    bot.reply_to(message, get_sub_text(), reply_markup=build_sub_markup())

@bot.message_handler(commands=['myid'])
def cmd_myid(message):
    bot.reply_to(message, "Your Telegram ID: " + str(message.from_user.id))

@bot.message_handler(commands=['support'])
def cmd_support(message):
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("✉️  Contact Support", url="https://t.me/" + OWNER_USERNAME))
    bot.reply_to(message, "🛠️  Support\n\nHave a question? We reply fast.", reply_markup=m)

@bot.message_handler(commands=['referral'])
def cmd_referral(message):
    uid  = message.from_user.id
    link = "https://t.me/" + BOT_USERNAME + "?start=ref_" + str(uid)
    m    = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("📤  Share link", switch_inline_query=link))
    bot.reply_to(message,
        "🎁  Invite & Earn\n\n"
        "For every friend who joins:\n"
        "· You get +" + str(REFERRAL_BONUS) + " free requests\n"
        "· They get " + str(FREE_LIMIT) + " free requests\n\n"
        "Your referral link:\n" + link, reply_markup=m)

@bot.message_handler(commands=['balance'])
def cmd_balance(message):
    uid    = message.from_user.id
    plan   = get_plan(uid)
    expiry = pro_users.get(uid)
    if uid == OWNER_ID:
        bot.reply_to(message, "👑  Creator — unlimited"); return
    if expiry and expiry > datetime.now():
        labels = {"starter":"🥉 Starter","pro":"🥈 Pro","business":"🥇 Business"}
        extra  = ""
        if plan == "starter":
            left  = max(0, 50 - get_settings(uid).get("monthly_used",0))
            extra = "\nRequests this month: " + str(left) + "/50"
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("⬆️  Upgrade", callback_data="menu_subscription"))
        bot.reply_to(message, labels.get(plan,"") + " Plan" + extra + "\nExpires: " + expiry.strftime("%b %d, %Y"), reply_markup=m)
    else:
        left = get_free_left(uid)
        m    = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("💎  View Plans", callback_data="menu_subscription"))
        bot.reply_to(message, "🎁  Free Plan\nRequests left: " + str(left) + "/" + str(FREE_LIMIT), reply_markup=m)

@bot.message_handler(commands=['history'])
def cmd_history(message):
    uid   = message.from_user.id
    items = user_text_history.get(uid, [])
    if not items:
        bot.reply_to(message, "No saved listings yet. Create your first one!"); return
    m = InlineKeyboardMarkup(row_width=1)
    for i, item in enumerate(items):
        preview = item["text"].replace("\n"," ")[:45]
        m.add(InlineKeyboardButton(str(i+1) + "  " + preview + "…", callback_data="hist_"+str(i)))
    bot.reply_to(message, "📜  Your listings — tap to reload:", reply_markup=m)

@bot.message_handler(commands=['cryptotest'])
def cmd_cryptotest(message):
    if message.from_user.id != OWNER_ID:
        return
    lines = []
    lines.append("USDT_ADDRESS: " + (USDT_ADDRESS[:20] + "..." if USDT_ADDRESS else "❌ EMPTY"))
    lines.append("WEBHOOK_URL: " + (os.environ.get("WEBHOOK_URL","") or "❌ EMPTY"))
    lines.append("Stars payment: ✅ Active (always works)")
    lines.append("USDT payment: manual (Stars recommended)")
    bot.reply_to(message, "🔍 Payment Diagnostics\n\n" + "\n".join(lines))


@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    if message.from_user.id != OWNER_ID: return
    active = sum(1 for e in pro_users.values() if e > datetime.now())
    plans  = {}
    for p in user_plan.values(): plans[p] = plans.get(p,0)+1
    bot.reply_to(message,
        "📊  Stats\n\n"
        "Total users: " + str(len(all_users)) +
        "\nActive subs: " + str(active) +
        "\nPlans: " + str(plans) +
        "\nReferrals: " + str(len(referred_by)))

@bot.message_handler(commands=['activate'])
def cmd_activate(message):
    if message.from_user.id != OWNER_ID: return
    try:
        parts  = message.text.split()
        tid    = int(parts[1])
        plan   = parts[2] if len(parts)>2 else "pro"
        days   = int(parts[3]) if len(parts)>3 else 30
        expiry = activate_plan(tid, plan, days)
        bot.reply_to(message, "Done. " + str(tid) + " → " + plan + " until " + expiry.strftime("%b %d, %Y"))
        try: bot.send_message(tid, "✅  Your SellMate AI plan is active until " + expiry.strftime("%b %d, %Y") + " 🚀")
        except Exception: pass
    except (IndexError, ValueError):
        bot.reply_to(message, "Use: /activate 123456789 pro 30")

@bot.message_handler(commands=['deactivate'])
def cmd_deactivate(message):
    if message.from_user.id != OWNER_ID: return
    try:
        tid = int(message.text.split()[1])
        pro_users.pop(tid, None); user_plan.pop(tid, None)
        bot.reply_to(message, "Revoked for " + str(tid))
        try: bot.send_message(tid, "Your SellMate AI subscription was deactivated.")
        except Exception: pass
    except (IndexError, ValueError):
        bot.reply_to(message, "Use: /deactivate 123456789")

# ─────────────────────────────────────────────
# SUBSCRIPTION TEXT
# ─────────────────────────────────────────────
def get_sub_text():
    return (
        "💎  SellMate AI Plans\n\n"
        "🥉  Starter · 200 ⭐/mo (~$2.99)\n"
        "50 requests · All platforms\n\n"
        "🥈  Pro · 500 ⭐/mo (~$6.99)\n"
        "Unlimited · Keywords · Ad copy · Photo scan\n\n"
        "🥇  Business · 1200 ⭐/mo (~$16.99)\n"
        "Everything in Pro + bulk generation + competitor analysis + priority support\n\n"
        "Pay with Telegram Stars (instant) or USDT crypto (auto-activates)."
    )

# ─────────────────────────────────────────────
# LIMIT MESSAGE
# ─────────────────────────────────────────────
def send_limit_msg(chat_id):
    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("🥇 Business — 1200 ⭐/mo", callback_data="pay_business"),
        InlineKeyboardButton("🥈 Pro — 500 ⭐/mo",       callback_data="pay_pro"),
        InlineKeyboardButton("🥉 Starter — 200 ⭐/mo",   callback_data="pay_starter"),
        InlineKeyboardButton("🎁 Invite friends · earn requests", callback_data="ref_hint")
    )
    bot.send_message(chat_id,
        "✨  Free requests used up\n\n"
        "Choose a plan to keep going:\n\n"
        "🥇  Business — Unlimited + bulk + competitor analysis\n"
        "🥈  Pro — Unlimited requests · All platforms · Ad copy\n"
        "🥉  Starter — 50 requests/month · Great to start\n\n"
        "Or invite friends and earn free requests.", reply_markup=m)

# ─────────────────────────────────────────────
# CALLBACKS — MENU
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("menu_"))
def cb_menu(call):
    action = call.data.replace("menu_","")
    bot.answer_callback_query(call.id)
    if action == "main":
        safe_edit(call, "What would you like to do?", build_main_menu())
    elif action == "subscription":
        safe_edit(call, get_sub_text(), build_sub_markup())
    elif action == "settings":
        safe_edit(call, "⚙️  Settings", build_settings_markup())
    elif action == "support":
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("✉️  Contact Support", url="https://t.me/"+OWNER_USERNAME))
        m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_main"))
        safe_edit(call, "🛠️  Support\n\nHave a question? We reply fast.", m)
    elif action == "history":
        uid   = call.from_user.id
        items = user_text_history.get(uid,[])
        if not items:
            safe_edit(call, "No saved listings yet. Send a product to create your first!", build_back()); return
        m = InlineKeyboardMarkup(row_width=1)
        for i,item in enumerate(items):
            preview = item["text"].replace("\n"," ")[:45]
            m.add(InlineKeyboardButton(str(i+1)+"  "+preview+"…", callback_data="hist_"+str(i)))
        m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_main"))
        safe_edit(call, "📜  Your listings — tap to reload:", m)

# ─────────────────────────────────────────────
# CALLBACKS — QUICK ACTIONS
# ─────────────────────────────────────────────
QUICK_PROMPTS = {
    "listing":   ("✨  Create Listing",   "Send me your product name and details (material, size, color, condition).\n\nExample: Handmade ceramic mug, sage green, 12oz, botanical leaf pattern"),
    "photo":     ("📸  Scan Product",     "Send me a product photo and I'll identify it and write a complete listing automatically."),
    "improve":   ("🔍  Improve Listing",  "Paste your existing listing and I'll show you exactly what to fix and give you an improved version."),
    "ideas":     ("💡  Product Ideas",    "Tell me your niche, skills or what you have, and I'll suggest profitable product ideas for your marketplace."),
    "translate": ("🌍  Translate",        "Send me a listing and tell me the target language. I'll translate and adapt it for that market."),
    "adcopy":    ("📣  Ad Copy",          "Send me your product details and which platform you're advertising on (Instagram, TikTok, Pinterest, Facebook Ads)."),
}

@bot.callback_query_handler(func=lambda c: c.data.startswith("quick_"))
def cb_quick(call):
    uid    = call.from_user.id
    action = call.data.replace("quick_","")
    bot.answer_callback_query(call.id)
    if action not in QUICK_PROMPTS: return
    label, prompt = QUICK_PROMPTS[action]
    history = user_history.setdefault(uid, [])
    s       = get_settings(uid)
    if not history: history.append({"role":"system","content":build_system_prompt(s)})
    history.append({"role":"assistant","content":prompt})
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Back", callback_data="menu_main"))
    safe_edit(call, "💬  " + label + "\n\n" + prompt, m)

# ─────────────────────────────────────────────
# CALLBACKS — POST ACTIONS
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("copy_") or c.data.startswith("action_"))
def cb_post_action(call):
    uid  = call.from_user.id
    data = call.data
    items= user_text_history.get(uid,[])
    bot.answer_callback_query(call.id)
    if not items:
        bot.send_message(call.message.chat.id, "No listing found. Generate one first."); return

    last_text = items[-1]["text"]
    history   = user_history.setdefault(uid,[])
    s         = get_settings(uid)

    if data == "copy_title":
        sep = "━━━━━━━━━━━━━━━━━━━━━━"
        if "📝  SEO Title" in last_text:
            part = last_text.split("📝  SEO Title")[1].split(sep)[1].strip() if sep in last_text.split("📝  SEO Title")[1] else ""
            bot.send_message(call.message.chat.id, "📋  Title copied:\n\n" + part)
        else:
            bot.send_message(call.message.chat.id, "📋  " + last_text[:300])

    elif data == "copy_desc":
        if "📄  Description" in last_text:
            sep  = "━━━━━━━━━━━━━━━━━━━━━━"
            part = last_text.split("📄  Description")[1].split(sep)[1].strip() if sep in last_text.split("📄  Description")[1] else ""
            bot.send_message(call.message.chat.id, "📄  Description copied:\n\n" + part)
        else:
            bot.send_message(call.message.chat.id, last_text[:1000])

    elif data == "copy_tags":
        if "🏷  Tags" in last_text:
            sep  = "━━━━━━━━━━━━━━━━━━━━━━"
            part = last_text.split("🏷  Tags")[1].split(sep)[1].strip() if sep in last_text.split("🏷  Tags")[1] else ""
            bot.send_message(call.message.chat.id, "🏷  Tags copied:\n\n" + part)

    elif data == "action_improve":
        if not history: history.append({"role":"system","content":build_system_prompt(s)})
        history.append({"role":"user","content":"Please improve this listing — make it more compelling and better optimized."})
        _generate_with_progress(uid, call.message.chat.id, history, s)

    elif data == "action_regen":
        msgs = [m for m in history if m["role"]=="user"]
        if msgs:
            last_user_msg = msgs[-1]["content"]
            user_history[uid] = []
            history = user_history[uid]
            history.append({"role":"system","content":build_system_prompt(s)})
            history.append({"role":"user","content":last_user_msg})
            _generate_with_progress(uid, call.message.chat.id, history, s)

    elif data == "action_translate":
        if not history: history.append({"role":"system","content":build_system_prompt(s)})
        history.append({"role":"assistant","content":"Which language would you like me to translate this listing to?"})
        bot.send_message(call.message.chat.id, "🌍  Which language should I translate this listing to?\n\nJust type the language name, e.g. Spanish, French, German, Japanese…")

# ─────────────────────────────────────────────
# CALLBACKS — HISTORY
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("hist_"))
def cb_hist(call):
    uid = call.from_user.id
    idx = int(call.data.replace("hist_",""))
    items = user_text_history.get(uid,[])
    if idx >= len(items): bot.answer_callback_query(call.id,"Not found"); return
    item = items[idx]
    s    = get_settings(uid)
    user_history[uid] = [
        {"role":"system","content":build_system_prompt(s)},
        {"role":"assistant","content":item["text"]}
    ]
    bot.answer_callback_query(call.id,"Loaded!")
    bot.send_message(call.message.chat.id,
        "✅  Listing from " + item["ts"] + " — loaded.\n\nAsk me to improve, translate or regenerate it.\n\n" + item["text"],
        reply_markup=post_action_markup())

# ─────────────────────────────────────────────
# CALLBACKS — SETTINGS
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def cb_settings(call):
    uid  = call.from_user.id
    s    = get_settings(uid)
    data = call.data
    lang_names = {"en":"English","es":"Spanish","de":"German","fr":"French","it":"Italian",
                  "pt":"Portuguese","ru":"Russian","ja":"Japanese","zh":"Chinese","ar":"Arabic"}

    if data == "set_reset":
        user_settings[uid] = {"model":"smart","platform":"auto","tone":"auto","length":"auto","language":"en"}
        bot.answer_callback_query(call.id,"Settings reset ✔")
        safe_edit(call,"⚙️  Settings (reset to defaults)",build_settings_markup()); return

    if data.startswith("set_open_"):
        bot.answer_callback_query(call.id)
        sec = data.replace("set_open_","")
        builders = {"platform":("🛍️  Choose platform",build_platform_markup),
                    "model":("🤖  Choose AI model",build_model_markup),
                    "tone":("🎨  Choose tone",build_tone_markup),
                    "length":("📄  Choose length",build_length_markup),
                    "language":("🌍  Choose language",build_language_markup)}
        if sec in builders:
            label, fn = builders[sec]
            safe_edit(call, label, fn(s)); return

    if data.startswith("set_platform_"):
        s["platform"] = data.replace("set_platform_","")
        bot.answer_callback_query(call.id,"✅ Updated"); safe_edit(call,"🛍️  Platform",build_platform_markup(s))
    elif data.startswith("set_model_"):
        s["model"] = data.replace("set_model_","")
        bot.answer_callback_query(call.id,"✅ Updated"); safe_edit(call,"🤖  AI Model",build_model_markup(s))
    elif data.startswith("set_tone_"):
        s["tone"] = data.replace("set_tone_","")
        bot.answer_callback_query(call.id,"✅ Updated"); safe_edit(call,"🎨  Tone",build_tone_markup(s))
    elif data.startswith("set_length_"):
        s["length"] = data.replace("set_length_","")
        bot.answer_callback_query(call.id,"✅ Updated"); safe_edit(call,"📄  Length",build_length_markup(s))
    elif data.startswith("set_language_"):
        new_lang = data.replace("set_language_","")
        s["language"] = new_lang
        user_history[uid] = []
        bot.answer_callback_query(call.id,"✅ Language updated")
        safe_edit(call,"🌍  Language set to "+lang_names.get(new_lang,new_lang)+"!\n\nI'll now respond in this language.",build_language_markup(s))

# ─────────────────────────────────────────────
# CALLBACKS — PAYMENTS (Stars)
# ─────────────────────────────────────────────
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
    addr = USDT_ADDRESS or "Contact support"
    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("🥉 Starter — $2.99 USDT", callback_data="pay_usdt_starter"),
        InlineKeyboardButton("🥈 Pro — $6.99 USDT",     callback_data="pay_usdt_pro"),
        InlineKeyboardButton("🥇 Business — $16.99 USDT", callback_data="pay_usdt_business"),
        InlineKeyboardButton("⬅️ Back", callback_data="menu_subscription")
    )
    safe_edit(call,
        "💰  Pay with USDT (TRC-20)\n\n"
        "Send USDT to this address:\n"
        "`" + addr + "`\n\n"
        "After payment:\n"
        "1. Send screenshot here\n"
        "2. Send /myid\n"
        "Activated within 1 hour.\n\n"
        "Choose your plan to see exact amount:", m)

@bot.callback_query_handler(func=lambda c: c.data.startswith("pay_usdt_"))
def cb_usdt_plan(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
    plans = {
        "pay_usdt_starter":  ("2.99",  "Starter",  "starter",  30),
        "pay_usdt_pro":      ("6.99",  "Pro",       "pro",      30),
        "pay_usdt_business": ("16.99", "Business",  "business", 30),
    }
    plan = plans.get(call.data)
    if not plan: return
    amount, label, plan_key, days = plan
    addr = USDT_ADDRESS or "Contact support for address"
    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("✉️  Confirm payment to support", url="https://t.me/" + OWNER_USERNAME),
        InlineKeyboardButton("⬅️ Back", callback_data="pay_usdt")
    )
    safe_edit(call,
        "💰  " + label + " — $" + amount + " USDT\n\n"
        "Send exactly $" + amount + " USDT (TRC-20) to:\n"
        "`" + addr + "`\n\n"
        "Then tap the button below, send the screenshot + /myid.\n"
        "Subscription activates within 1 hour.", m)

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
# CORE GENERATION
# ─────────────────────────────────────────────
def _generate_with_progress(uid, chat_id, history, s):
    progress_msg = bot.send_message(chat_id, make_progress_bar(0))

    def run_stages():
        for stage in LISTING_STAGES[1:]:
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
