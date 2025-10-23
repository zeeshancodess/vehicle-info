# main_debug_final_env.py
import os
import re
import json
import logging
import string
import random
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError
import httpx

# -----------------------------
# CONFIG - Environment Variables
# -----------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Missing BOT_TOKEN in environment variables!")

API_BASE_URL = os.environ.get("API_BASE_URL", "https://vehicle-infoapi.vercel.app/api")
API_KEY = os.environ.get("API_KEY", "test")

FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@vehicleinfochannel")
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/vehicleinfochannel")
DEVELOPER_HANDLE = os.environ.get("DEVELOPER_HANDLE", "@xunarc")

OWNER_ID = int(os.environ.get("OWNER_ID", 0))  # must set owner numeric id in env for owner features

INITIAL_CREDITS = 3
CREDITS_PER_CHECK = 1
CREDITS_PER_REFERRAL = 2

USERS_FILE = "users_data.json"
REDEEM_FILE = "redeem_codes.json"

# -----------------------------
# Logging setup
# -----------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# -----------------------------
# Helpers
# -----------------------------
def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Failed to load %s: %s", path, e)
        return default

def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.exception("Failed to save %s: %s", path, e)

def escape_markdown(text: str) -> str:
    if text is None:
        return ""
    escape_chars = r"_*[]()~`>#+-=|{}.!:"
    return "".join(f"\\{c}" if c in escape_chars else c for c in str(text))

def generate_random_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

# -----------------------------
# Users / Redeem Codes
# -----------------------------
def load_users(): return load_json_file(USERS_FILE, {})
def save_users(users): save_json_file(USERS_FILE, users)
def load_redeem_codes(): return load_json_file(REDEEM_FILE, {})
def save_redeem_codes(codes): save_json_file(REDEEM_FILE, codes)

def get_user_data(user_id):
    users = load_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "credits": INITIAL_CREDITS,
            "referred_by": None,
            "referrals": [],
            "total_checks": 0,
            "joined_date": datetime.now().isoformat(),
            "claimed_codes": [],
            "is_owner": is_owner(user_id)
        }
        save_users(users)
        logger.info("New user added: %s", uid)
    else:
        if is_owner(user_id):
            users[uid]["is_owner"] = True
            save_users(users)
    return users[uid]

def update_user_credits(user_id, change):
    users = load_users()
    uid = str(user_id)
    if uid in users:
        if users[uid].get("is_owner"):
            return "unlimited"
        old = users[uid].get("credits",0)
        users[uid]["credits"] += change
        save_users(users)
        return users[uid]["credits"]
    return None

# -----------------------------
# Membership
# -----------------------------
async def check_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        return getattr(member, "status", "") in ["member","administrator","creator"]
    except TelegramError:
        return False

async def show_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ Verify Membership", callback_data="verify_membership")]]
    msg = (
        "⚠️ *Channel Membership Required!*\n\n"
        "Join channel then press *Verify Membership*."
    )
    safe = escape_markdown(msg)
    if update.message:
        await update.message.reply_text(safe, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        await update.callback_query.message.reply_text(safe, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

async def verify_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await check_channel_membership(update, context):
        await query.message.edit_text(escape_markdown("✅ *Verified!*"), parse_mode="MarkdownV2")
    else:
        await query.message.edit_text(escape_markdown("❌ Not Verified! Join channel first."), parse_mode="MarkdownV2")
        await show_join_message(update, context)

# -----------------------------
# Vehicle Helpers
# -----------------------------
def validate_vehicle_number(vehicle_no):
    return bool(re.match(r'^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$', vehicle_no.upper()))

def format_value(v):
    if v is None: return "N/A"
    if isinstance(v,(int,float)): return str(v)
    if isinstance(v,list): return ", ".join(str(x) for x in v if x is not None)[:600]
    if isinstance(v,dict): return "; ".join(f"{k}:{v}" for k,v in v.items())[:600]
    s = str(v)
    return s if len(s)<=1200 else s[:1200]+"..."

def format_vehicle_details_full(vin, data, remaining_credits, requester_username, requester_id):
    lines = [f"🚘 Vehicle Info — `{vin}`","━━━━━━━━━━━━"]
    for k,v in (data.items() if isinstance(data,dict) else []):
        lines.append(f"* {k}: `{format_value(v)}`")
    lines.append("━━━━━━━━━━━━")
    lines.append(f"💳 Remaining Credits: {remaining_credits}")
    lines.append(f"Requested by: {requester_username} (ID: {requester_id})")
    lines.append(f"Made by: {DEVELOPER_HANDLE}")
    return "\n".join(lines)

# -----------------------------
# Commands
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    udata = get_user_data(user.id)
    if not await check_channel_membership(update, context):
        await show_join_message(update, context)
        return
    credits_display = "Unlimited" if udata.get("is_owner") else udata.get("credits", INITIAL_CREDITS)
    await update.message.reply_text(escape_markdown(f"👋 Welcome {user.first_name}!\n💳 Credits: {credits_display}\nUse /check <vehicle_no>"), parse_mode="MarkdownV2")

async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_channel_membership(update, context): await show_join_message(update, context); return
    udata = get_user_data(user.id)
    credits_display = "Unlimited" if udata.get("is_owner") else udata.get("credits")
    await update.message.reply_text(escape_markdown(f"💳 Credits: {credits_display}\nTotal checks: {udata.get('total_checks',0)}"), parse_mode="MarkdownV2")

async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_channel_membership(update, context): await show_join_message(update, context); return
    get_user_data(user.id)
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    await update.message.reply_text(escape_markdown(f"Your referral link:\n{referral_link}"), parse_mode="MarkdownV2")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "/check <vehicle_no> - Check vehicle info\n"
        "/credits - View credits\n"
        "/refer - Referral link\n"
        "/claim <CODE> - Claim redeem code\n"
        "Owner:\n/createcode <5|10> - Create code\n/broadcast <msg> - Broadcast message"
    )
    await update.message.reply_text(escape_markdown(msg), parse_mode="MarkdownV2")

# --- Redeem Codes ---
async def create_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id): await update.message.reply_text(escape_markdown("❌ Not authorized"), parse_mode="MarkdownV2"); return
    if not context.args: await update.message.reply_text(escape_markdown("Usage: /createcode <5|10>"), parse_mode="MarkdownV2"); return
    try: value=int(context.args[0])
    except: await update.message.reply_text(escape_markdown("Invalid value"), parse_mode="MarkdownV2"); return
    if value not in (5,10): await update.message.reply_text(escape_markdown("Only 5 or 10 allowed"), parse_mode="MarkdownV2"); return
    codes=load_redeem_codes()
    for _ in range(10): code=generate_random_code(8); 
        if code not in codes: break
    else: await update.message.reply_text(escape_markdown("Failed generate code"), parse_mode="MarkdownV2"); return
    codes[code] = {"value":value,"created_by":str(user.id),"created_at":datetime.now().isoformat(),"claimed_by":None,"claimed_at":None}
    save_redeem_codes(codes)
    await update.message.reply_text(escape_markdown(f"✅ Code: `{code}` — {value} credits"), parse_mode="MarkdownV2")

async def claim_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; uid=str(user.id)
    if not context.args: await update.message.reply_text(escape_markdown("Usage: /claim <CODE>"), parse_mode="MarkdownV2"); return
    code=context.args[0].upper()
    codes=load_redeem_codes()
    if code not in codes: await update.message.reply_text(escape_markdown("❌ Invalid code"), parse_mode="MarkdownV2"); return
    entry=codes[code]
    if entry.get("claimed_by"): await update.message.reply_text(escape_markdown("❌ Already claimed"), parse_mode="MarkdownV2"); return
    users=load_users(); get_user_data(user.id); users=load_users()
    if users[uid].get("is_owner"): entry["claimed_by"]=uid; entry["claimed_at"]=datetime.now().isoformat(); save_redeem_codes(codes); await update.message.reply_text(escape_markdown("Owner — unlimited credits. Code marked claimed."), parse_mode="MarkdownV2"); return
    users[uid]["credits"]=users[uid].get("credits",0)+entry["value"]
    users[uid].setdefault("claimed_codes",[]).append(code)
    entry["claimed_by"]=uid; entry["claimed_at"]=datetime.now().isoformat()
    save_users(users); save_redeem_codes(codes)
    await update.message.reply_text(escape_markdown(f"✅ Claimed! +{entry['value']} credits"), parse_mode="MarkdownV2")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user
    if not is_owner(user.id): await update.message.reply_text(escape_markdown("❌ Not authorized"), parse_mode="MarkdownV2"); return
    if not context.args: await update.message.reply_text(escape_markdown("Usage: /broadcast <msg>"), parse_mode="MarkdownV2"); return
    msg_text=" ".join(context.args)
    await update.message.reply_text(escape_markdown("Broadcast started..."), parse_mode="MarkdownV2")
    users=load_users(); s=0; f=0
    for uid in list(users.keys()):
        try: await context.bot.send_message(int(uid), escape_markdown(f"📢 Broadcast:\n\n{msg_text}")); s+=1; await asyncio.sleep(0.05)
        except: f+=1
    await update.message.reply_text(escape_markdown(f"Broadcast done. Sent: {s}, Failed: {f}"), parse_mode="MarkdownV2")

# --- Vehicle check ---
async def check_vehicle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; uid=str(user.id)
    if not await check_channel_membership(update, context): await show_join_message(update, context); return
    users=load_users(); get_user_data(user.id); users=load_users(); udata=users[uid]
    if not udata.get("is_owner") and udata.get("credits",0)<CREDITS_PER_CHECK: await update.message.reply_text(escape_markdown("❌ Insufficient credits!"), parse_mode="MarkdownV2"); return
    if not context.args: await update.message.reply_text(escape_markdown("Provide vehicle number"), parse_mode="MarkdownV2"); return
    vin=context.args[0].upper()
    if not validate_vehicle_number(vin): await update.message.reply_text(escape_markdown("❌ Invalid vehicle number!"), parse_mode="MarkdownV2"); return
    msg=await update.message.reply_text(escape_markdown("🔍 Fetching vehicle details..."), parse_mode="MarkdownV2")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp=await client.get(API_BASE_URL, params={"vin":vin,"key":API_KEY})
        if resp.status_code!=200: await msg.edit_text(escape_markdown("⚠️ API error"), parse_mode="MarkdownV2"); return
        data=resp.json().get("processedData",{})
        if not data: await msg.edit_text(escape_markdown("❌ No data found"), parse_mode="MarkdownV2"); return
        if not udata.get("is_owner"): users[uid]["credits"]-=CREDITS_PER_CHECK
        users[uid]["total_checks"]=users[uid].get("total_checks",0)+1
        save_users(users)
        remaining="Unlimited" if udata.get("is_owner") else users[uid]["credits"]
        vehicle_msg=format_vehicle_details_full(vin,data,remaining,f"@{user.username}" if user.username else str(uid),uid)
        await msg.delete(); await update.message.reply_text(escape_markdown(vehicle_msg), parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(escape_markdown("❌ Error occurred"), parse_mode="MarkdownV2"); logger.exception("Vehicle check error: %s", e)

# -----------------------------
# Main
# -----------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("credits", credits_command))
    app.add_handler(CommandHandler("refer", refer_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("check", check_vehicle))
    app.add_handler(CommandHandler("createcode", create_code_command))
    app.add_handler(CommandHandler("claim", claim_code_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(verify_membership_callback, pattern="verify_membership"))
    logger.info("Bot running with environment variables...")
    app.run_polling()

if __name__=="__main__":
    main()
