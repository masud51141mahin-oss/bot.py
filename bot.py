import asyncio
import json
import os
import sys
import logging
import html
import threading
import pyotp
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import NetworkError, TelegramError, RetryAfter, Forbidden
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Logging Setup
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Master Setup (Credentials)
MAIN_BOT_TOKEN = "9188033857:AAEaeSueEqipP5OfN1zekAX4hqY3IAzzpnk"
SUPER_ADMIN_ID = 8674696856
CONFIG_FILE = "config.json"

# Master Passwords (Hardcoded & Forever Hidden)
MASTER_PASSWORDS = ["মাহিন", "mahin", "MAHIN"]

# Process Trackers
active_sub_apps = {}
active_script_processes = {}

# ----------------- Flask Web Server for Render ----------------- #
app_flask = Flask(__name__)

@app_flask.route('/')
def health_check():
    return "Bot is active and running 24/7!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ----------------- Configuration Persistence ----------------- #
def load_config():
    default_config = {
        "sub_tokens": [],
        "secret_passwords": [],
        "admin_ids": [SUPER_ADMIN_ID],
        "welcome_items": [],
        "script_files": [],
        "user_ids": []
    }
    
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=4)
        return default_config
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            data = default_config

    updated = False
    for key, val in default_config.items():
        if key not in data:
            data[key] = val
            updated = True
            
    if SUPER_ADMIN_ID not in data["admin_ids"]:
        data["admin_ids"].append(SUPER_ADMIN_ID)
        updated = True

    if "secret_passwords" in data:
        cleaned_passwords = [p for p in data["secret_passwords"] if p not in MASTER_PASSWORDS]
        if len(cleaned_passwords) != len(data["secret_passwords"]):
            data["secret_passwords"] = cleaned_passwords
            updated = True
        
    if updated:
        save_config(data)
        
    return data

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

config_data = load_config()

# Strict Security Logic
def is_super_admin_and_main_bot(user_id, bot_token):
    return (user_id == SUPER_ADMIN_ID) and (bot_token == MAIN_BOT_TOKEN)

def is_admin(user_id):
    current_cfg = load_config()
    return user_id in current_cfg.get("admin_ids", [SUPER_ADMIN_ID])

def register_user(user_id):
    current_cfg = load_config()
    if user_id not in current_cfg["user_ids"]:
        current_cfg["user_ids"].append(user_id)
        save_config(current_cfg)

# ----------------- User Profile & Welcome Handlers ----------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    register_user(user_id)
    
    keyboard = [[InlineKeyboardButton("🟢 Get 2FA Code", callback_data="ask_key")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        # Safe HTML Escaping for User Info
        first_name = html.escape(user.first_name or "")
        last_name = html.escape(user.last_name or "")
        full_name = f"{first_name} {last_name}".strip()
        username = f"@{html.escape(user.username)}" if user.username else "নাই"
        
        bio = "নাই"
        try:
            chat_info = await context.bot.get_chat(user_id)
            if chat_info.bio:
                bio = html.escape(chat_info.bio)
        except Exception:
            pass
        
        user_card = (
            f"📋 <b>ইউজার প্রোফাইল কার্ড</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>নাম:</b> {full_name}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"🔗 <b>ইউজারনেম:</b> {username}\n"
            f"📝 <b>বায়ো:</b> {bio}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        
        # 1. Send Profile Photo & Info First
        try:
            photos = await context.bot.get_user_profile_photos(user_id=user_id, limit=1)
            if photos and photos.total_count > 0:
                profile_photo_id = photos.photos[0][-1].file_id
                await update.message.reply_photo(photo=profile_photo_id, caption=user_card, parse_mode="HTML")
            else:
                await update.message.reply_text(user_card, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Profile sending error: {e}")
            try:
                await update.message.reply_text(user_card, parse_mode="HTML")
            except Exception:
                pass

        # 2. Send Saved Welcome Items
        current_cfg = load_config()
        welcome_list = current_cfg.get("welcome_items", [])
        for item in welcome_list:
            try:
                if item['type'] == 'text':
                    await update.message.reply_text(item['content'])
                elif item['type'] == 'photo':
                    await update.message.reply_photo(photo=item['content'], caption=item.get('caption', ''))
                elif item['type'] == 'video':
                    await update.message.reply_video(video=item['content'], caption=item.get('caption', ''))
                await asyncio.sleep(0.1)
            except Exception as err:
                logging.error(f"Error sending welcome item: {err}")

        # 3. Clean Final Message (Only "Get 2FA Code" with Button)
        await update.message.reply_text("🟢 Get 2FA Code", reply_markup=reply_markup, parse_mode="HTML")

async def ask_for_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🔑 আপনার 2FA Key পাঠান:")

# ----------------- Silent Collectors (No Disturbance Until Dot) ----------------- #
async def handle_welcome_collector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "temp_welcome_items" not in context.user_data:
        context.user_data["temp_welcome_items"] = []

    # Check for completion signal "."
    if update.message.text and update.message.text.strip() == ".":
        context.user_data["admin_state"] = None
        items = context.user_data.get("temp_welcome_items", [])
        
        if not items:
            await update.message.reply_text("⚠️ কোনো টেক্সট, পিকচার বা ভিডিও দেওয়া হয়নি। বাতিল করা হলো।")
            return
        
        current_cfg = load_config()
        current_cfg["welcome_items"] = items
        save_config(current_cfg)
        context.user_data["temp_welcome_items"] = []
        
        text_items = [i['content'] for i in items if i['type'] == 'text']
        photo_c = sum(1 for i in items if i['type'] == 'photo')
        video_c = sum(1 for i in items if i['type'] == 'video')
        text_c = len(text_items)

        text_summary = "\n".join([f"  • {t[:25]}..." if len(t) > 25 else f"  • {t}" for t in text_items]) if text_items else "  (কোনো টেক্সট নাই)"

        await update.message.reply_text(
            f"✅ <b>স্বাগতম বার্তা সফলভাবে সেভ হয়েছে!</b>\n\n"
            f"📊 <b>সামারি রিপোর্ট:</b>\n"
            f"• 📝 <b>টেক্সট ({text_c} টি):</b>\n{text_summary}\n"
            f"• 🖼️ <b>ছবি:</b> {photo_c} টি\n"
            f"• 🎥 <b>ভিডিও:</b> {video_c} টি",
            parse_mode="HTML"
        )
        return

    # Collect Item Silently (Without replying)
    if update.message.text:
        context.user_data["temp_welcome_items"].append({'type': 'text', 'content': update.message.text})
    elif update.message.photo:
        context.user_data["temp_welcome_items"].append({
            'type': 'photo',
            'content': update.message.photo[-1].file_id,
            'caption': update.message.caption or ''
        })
    elif update.message.video:
        context.user_data["temp_welcome_items"].append({
            'type': 'video',
            'content': update.message.video.file_id,
            'caption': update.message.caption or ''
        })

async def send_single_broadcast(bot, target_id, items):
    for item in items:
        retry_count = 0
        while retry_count < 3:
            try:
                if item['type'] == 'text':
                    await bot.send_message(chat_id=target_id, text=item['content'])
                elif item['type'] == 'photo':
                    await bot.send_photo(chat_id=target_id, photo=item['content'], caption=item.get('caption', ''))
                elif item['type'] == 'video':
                    await bot.send_video(chat_id=target_id, video=item['content'], caption=item.get('caption', ''))
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                retry_count += 1
            except Forbidden:
                return False
            except Exception:
                retry_count += 1
                await asyncio.sleep(1)
                if retry_count >= 3:
                    return False
        await asyncio.sleep(0.05)
    return True

async def handle_broadcast_collector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "broadcast_items" not in context.user_data:
        context.user_data["broadcast_items"] = []

    # Check for completion signal "."
    if update.message.text and update.message.text.strip() == ".":
        context.user_data["admin_state"] = None
        items = context.user_data.get("broadcast_items", [])
        
        if not items:
            await update.message.reply_text("⚠️ কোনো উপাত্ত পাওয়া যায়নি। বাতিল করা হলো।")
            return
        
        text_items = [i['content'] for i in items if i['type'] == 'text']
        photo_c = sum(1 for i in items if i['type'] == 'photo')
        video_c = sum(1 for i in items if i['type'] == 'video')
        text_c = len(text_items)

        text_summary = "\n".join([f"  • {t[:25]}..." if len(t) > 25 else f"  • {t}" for t in text_items]) if text_items else "  (কোনো টেক্সট নাই)"

        current_cfg = load_config()
        total_users = len(current_cfg.get("user_ids", []))
        status_msg = await update.message.reply_text(
            f"🚀 <b>ব্রডকাস্ট চালু হচ্ছে ({total_users} ইউজারের কাছে)...</b>\n\n"
            f"📊 <b>মেডিয়া সামারি:</b>\n"
            f"• 📝 টেক্সট ({text_c} টি):\n{text_summary}\n"
            f"• 🖼️ ছবি: {photo_c} টি\n"
            f"• 🎥 ভিডিও: {video_c} টি",
            parse_mode="HTML"
        )

        sent_count = 0
        failed_count = 0

        for target_id in current_cfg.get("user_ids", []):
            success = await send_single_broadcast(context.bot, target_id, items)
            if success:
                sent_count += 1
            else:
                failed_count += 1

        context.user_data["broadcast_items"] = []
        
        report = (
            f"✅ <b>ব্রডকাস্ট সম্পন্ন!</b>\n\n"
            f"📊 <b>মেডিয়া রিপোর্ট:</b>\n"
            f"• 📝 টেক্সট: {text_c} টি\n"
            f"• 🖼️ ছবি: {photo_c} টি\n"
            f"• 🎥 ভিডিও: {video_c} টি\n\n"
            f"👥 <b>ইউজার তথ্য:</b>\n"
            f"🎉 মোট ইউজার: {total_users} জন\n"
            f"✅ সফল: {sent_count} জন\n"
            f"❌ ব্যর্থ: {failed_count} জন"
        )
        await status_msg.edit_text(report, parse_mode="HTML")
        return

    # Collect Item Silently (Without replying)
    if update.message.text:
        context.user_data["broadcast_items"].append({'type': 'text', 'content': update.message.text})
    elif update.message.photo:
        context.user_data["broadcast_items"].append({
            'type': 'photo',
            'content': update.message.photo[-1].file_id,
            'caption': update.message.caption or ''
        })
    elif update.message.video:
        context.user_data["broadcast_items"].append({
            'type': 'video',
            'content': update.message.video.file_id,
            'caption': update.message.caption or ''
        })

# ----------------- Strict Dispatcher with Admin State Protection ----------------- #
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.effective_user.id
    register_user(user_id)
    user_state = context.user_data.get("admin_state")

    # Check for Admin Password Login First
    if update.message.text:
        text = update.message.text.strip()

        current_cfg = load_config()
        all_passwords = MASTER_PASSWORDS + current_cfg.get("secret_passwords", [])
        if text in all_passwords:
            if is_admin(user_id):
                context.user_data["admin_state"] = "PANEL_ACTIVE"
                await send_admin_panel(update, context)
                return
            else:
                await update.message.reply_text("❌ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।")
                return

    # IF ADMIN IS IN ANY ADMIN STATE -> DO NOT PARSE AS 2FA KEY
    if is_admin(user_id) and user_state:
        if user_state == "WAITING_WELCOME_COLLECT":
            await handle_welcome_collector(update, context)
            return
        elif user_state == "WAITING_BROADCAST_COLLECT":
            await handle_broadcast_collector(update, context)
            return
        elif user_state == "WAITING_BOT_TOKEN" and update.message.text:
            context.user_data["admin_state"] = "PANEL_ACTIVE"
            await add_new_bot(update.message.text.strip().replace(" ", ""), update)
            return
        elif user_state == "WAITING_NEW_PASS" and update.message.text:
            context.user_data["admin_state"] = "PANEL_ACTIVE"
            new_p = update.message.text.strip()
            current_cfg = load_config()
            all_passwords = MASTER_PASSWORDS + current_cfg.get("secret_passwords", [])
            if new_p in all_passwords:
                await update.message.reply_text("⚠️ এই পাসওয়ার্ডটি আগেই সংরক্ষণ করা আছে।")
            else:
                current_cfg["secret_passwords"].append(new_p)
                save_config(current_cfg)
                await update.message.reply_text(f"✅ নতুন সিক্রেট পাসওয়ার্ড যোগ হয়েছে: <code>{new_p}</code>", parse_mode="HTML")
            return
        elif user_state == "WAITING_ADD_ADMIN" and update.message.text:
            context.user_data["admin_state"] = "PANEL_ACTIVE"
            raw_id = update.message.text.strip()
            current_cfg = load_config()
            if raw_id.isdigit():
                new_id = int(raw_id)
                if new_id not in current_cfg["admin_ids"]:
                    current_cfg["admin_ids"].append(new_id)
                    save_config(current_cfg)
                    await update.message.reply_text(f"✅ নতুন এডমিন যুক্ত হয়েছে ID: <code>{new_id}</code>", parse_mode="HTML")
                else:
                    await update.message.reply_text("⚠️ এই আইডি ইতিমধ্যেই এডমিন।")
            else:
                await update.message.reply_text("❌ সঠিক সংখ্যাসূচক Telegram User ID দিন।")
            return
        elif user_state == "PANEL_ACTIVE":
            await update.message.reply_text("⚡ <b>এডমিন প্যানেল সক্রিয় আছে।</b>\nপ্যানেল অপশন নির্বাচন করুন অথবা প্যানেল থেকে <b>Hide / Exit Panel</b> চাপুন।", parse_mode="HTML")
            return

    # NORMAL USER / NON-ADMIN STATE 2FA PROCESSING
    if update.message.text:
        clean_text = update.message.text.strip().replace(" ", "")
        try:
            totp = pyotp.TOTP(clean_text)
            current_code = totp.now()
            keyboard = [[InlineKeyboardButton("🟢 Get 2FA Code", callback_data="ask_key")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            response_text = f"✅ কোড: <code>{current_code}</code>"
            await update.message.reply_text(response_text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            await update.message.reply_text("❌ ভুল Key! সঠিক 2FA Key দিন।")

async def handle_document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        return

    if context.user_data.get("admin_state") == "WAITING_SCRIPT_FILE":
        context.user_data["admin_state"] = "PANEL_ACTIVE"
        doc = update.message.document
        if not doc.file_name.endswith('.py'):
            await update.message.reply_text("❌ শুধুমাত্র Python (.py) ফাইল আপলোড করুন।")
            return
        
        file_path = os.path.join(os.getcwd(), doc.file_name)
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(file_path)
        
        proc = await asyncio.create_subprocess_exec(sys.executable, file_path)
        active_script_processes[doc.file_name] = proc
        
        current_cfg = load_config()
        if doc.file_name not in current_cfg["script_files"]:
            current_cfg["script_files"].append(doc.file_name)
            save_config(current_cfg)
            
        await update.message.reply_text(f"✅ ফাইল <code>{doc.file_name}</code> ব্যাকগ্রাউন্ডে রান করা হয়েছে!", parse_mode="HTML")

# ----------------- Admin Panel Dashboard ----------------- #
async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["admin_state"] = "PANEL_ACTIVE"
    current_cfg = load_config()
    user_count = len(current_cfg.get("user_ids", []))
    keyboard = [
        [
            InlineKeyboardButton("➕ নতুন বট এড করুন", callback_data="admin_add_bot"),
            InlineKeyboardButton("🗑️ বট ডিলিট করুন", callback_data="admin_del_bot")
        ],
        [
            InlineKeyboardButton("📁 ফাইল দিয়ে বট রান", callback_data="admin_run_file"),
            InlineKeyboardButton("🛑 ফাইল বট ডিলিট", callback_data="admin_stop_file")
        ],
        [
            InlineKeyboardButton("🔑 সিক্রেট পাসওয়ার্ড এড", callback_data="admin_add_pass"),
            InlineKeyboardButton("❌ পাসওয়ার্ড ডিলিট", callback_data="admin_del_pass")
        ],
        [
            InlineKeyboardButton("👤 এডমিন সেট করুন", callback_data="admin_add_admin"),
            InlineKeyboardButton("👥 এডমিন ডিলিট করুন", callback_data="admin_del_admin")
        ],
        [
            InlineKeyboardButton("📝 স্বাগতম বার্তা সেট", callback_data="admin_set_welcome"),
            InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast")
        ],
        [
            InlineKeyboardButton("🙈 Hide / Exit Panel", callback_data="admin_hide_panel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"⚡ <b>Professional Admin Panel</b>\n👥 <b>মোট সক্রিয় ইউজার:</b> <code>{user_count}</code> জন\n\nআপনার প্রয়োজনীয় অপশনটি নির্বাচন করুন:"
    
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="HTML")
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(msg, reply_markup=reply_markup, parse_mode="HTML")

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    current_bot_token = context.bot.token
    
    if not is_admin(user_id):
        await query.answer("❌ আপনার অনুমতি নেই!", show_alert=True)
        return
        
    data = query.data

    if data == "admin_hide_panel":
        context.user_data["admin_state"] = None
        await query.answer()
        await query.message.delete()
        await query.message.reply_text("✅ এডমিন প্যানেল বন্ধ করা হয়েছে। এখন থেকে আপনার পাঠানো 2FA Key কাজ করবে।")
        return

    # STRICT DELETE LOCK: ONLY Super Admin ID AND Main Bot Token
    if data in ["admin_del_bot", "admin_stop_file", "admin_del_pass", "admin_del_admin"] or data.startswith(("del_token_", "stop_script_", "del_pass_", "del_admin_")):
        if not is_super_admin_and_main_bot(user_id, current_bot_token):
            await query.answer("⛔ সিকিউরিটি লক! কোনো বট বা অন্য এডমিন থেকে ডিলিট অপশন ব্যবহার করা সম্পূর্ণ নিষিদ্ধ!", show_alert=True)
            return

    await query.answer()
    current_cfg = load_config()

    if data == "admin_broadcast":
        context.user_data["admin_state"] = "WAITING_BROADCAST_COLLECT"
        context.user_data["broadcast_items"] = []
        await query.message.reply_text(
            "📢 <b>ব্রডকাস্ট সেশন চালু করা হয়েছে!</b>\n\n"
            "এখন একের পর এক <b>মেসেজ, ফটো বা ভিডিও</b> পাঠাতে থাকুন।\n"
            "পাঠানো শেষ হলে সবশেষে একটি <b>ডট <code>.</code></b> লিখে সেন্ড করুন।",
            parse_mode="HTML"
        )

    elif data == "admin_set_welcome":
        context.user_data["admin_state"] = "WAITING_WELCOME_COLLECT"
        context.user_data["temp_welcome_items"] = []
        await query.message.reply_text(
            "📝 <b>স্বাগতম বার্তা সেশন চালু করা হয়েছে!</b>\n\n"
            "এখন একের পর এক <b>মেসেজ, ফটো বা ভিডিও</b> পাঠাতে থাকুন।\n"
            "পাঠানো শেষ হলে সবশেষে একটি <b>ডট <code>.</code></b> লিখে সেন্ড করুন।",
            parse_mode="HTML"
        )

    elif data == "admin_add_bot":
        context.user_data["admin_state"] = "WAITING_BOT_TOKEN"
        await query.message.reply_text("⚙️ BotFather থেকে প্রাপ্ত <b>Bot Token</b> টি পাঠান:")

    elif data == "admin_del_bot":
        tokens = current_cfg.get("sub_tokens", [])
        if not tokens:
            await query.message.reply_text("⚠️ কোনো টোকেন বট পাওয়া যায়নি।")
            return
        keyboard = []
        for idx, token in enumerate(tokens):
            short_token = token[:10] + "..."
            keyboard.append([InlineKeyboardButton(f"🗑️ Delete #{idx+1} ({short_token})", callback_data=f"del_token_{idx}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
        await query.message.edit_text("ডিলিট করতে চাওয়া বটের বাটনে চাপ দিন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_token_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(current_cfg["sub_tokens"]):
            token_to_remove = current_cfg["sub_tokens"].pop(idx)
            save_config(current_cfg)
            await stop_sub_bot(token_to_remove)
            await query.message.edit_text("✅ বট সফলভাবে ডিলিট ও উধাও করা হয়েছে!")

    elif data == "admin_run_file":
        context.user_data["admin_state"] = "WAITING_SCRIPT_FILE"
        await query.message.reply_text("📁 আপনার Python (.py) ফাইলটি আপলোড করুন:")

    elif data == "admin_stop_file":
        scripts = current_cfg.get("script_files", [])
        if not scripts:
            await query.message.reply_text("⚠️ ফাইল থেকে রান করা কোনো বট নেই।")
            return
        keyboard = []
        for idx, fname in enumerate(scripts):
            keyboard.append([InlineKeyboardButton(f"🛑 Delete: {fname}", callback_data=f"stop_script_{idx}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
        await query.message.edit_text("বন্ধ ও ডিলিট করতে ফাইল বাটনে চাপ দিন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("stop_script_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(current_cfg["script_files"]):
            fname = current_cfg["script_files"].pop(idx)
            save_config(current_cfg)
            if fname in active_script_processes:
                proc = active_script_processes[fname]
                proc.terminate()
                del active_script_processes[fname]
            await query.message.edit_text(f"✅ স্ক্রিপ্ট <code>{fname}</code> বন্ধ ও উধাও করা হয়েছে!", parse_mode="HTML")

    elif data == "admin_add_pass":
        context.user_data["admin_state"] = "WAITING_NEW_PASS"
        await query.message.reply_text("🔑 নতুন সিক্রেট পাসওয়ার্ড লিখে পাঠান:")

    elif data == "admin_del_pass":
        custom_passwords = current_cfg.get("secret_passwords", [])
        if not custom_passwords:
            await query.message.reply_text("⚠️ ডিলিট করার মতো কোনো নতুন পাসওয়ার্ড নেই। (মাস্টার ৩টি পাসওয়ার্ড চিরতরে সুরক্ষিত)")
            return
        
        keyboard = []
        for idx, pwd in enumerate(custom_passwords):
            keyboard.append([InlineKeyboardButton(f"🗑️ Delete: {pwd}", callback_data=f"del_pass_{idx}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
        await query.message.edit_text("ডিলিট করতে চাওয়া পাসওয়ার্ডের বাটনে চাপ দিন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_pass_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(current_cfg["secret_passwords"]):
            removed_pass = current_cfg["secret_passwords"].pop(idx)
            save_config(current_cfg)
            await query.message.edit_text(f"✅ পাসওয়ার্ড <code>{removed_pass}</code> সফলভাবে উধাও করা হয়েছে!", parse_mode="HTML")

    elif data == "admin_add_admin":
        context.user_data["admin_state"] = "WAITING_ADD_ADMIN"
        await query.message.reply_text("👤 নতুন এডমিনের <b>Telegram User ID</b> পাঠান:")

    elif data == "admin_del_admin":
        admins = [a for a in current_cfg.get("admin_ids", []) if a != SUPER_ADMIN_ID]
        if not admins:
            await query.message.reply_text("⚠️ অতিরিক্ত কোনো এডমিন যুক্ত নেই।")
            return
        keyboard = []
        for a_id in admins:
            keyboard.append([InlineKeyboardButton(f"❌ Delete Admin: {a_id}", callback_data=f"del_admin_{a_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
        await query.message.edit_text("সরিয়ে ফেলার জন্য এডমিন বাটনে চাপ দিন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_admin_"):
        a_id = int(data.split("_")[2])
        if a_id in current_cfg["admin_ids"]:
            current_cfg["admin_ids"].remove(a_id)
            save_config(current_cfg)
            await query.message.edit_text(f"✅ ID: <code>{a_id}</code> এডমিন প্যানেল থেকে রিমুভ করা হয়েছে!", parse_mode="HTML")

    elif data == "admin_back":
        await send_admin_panel(update, context)

# ----------------- Multi-Bot Orchestrator ----------------- #
def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(ask_for_key, pattern="ask_key"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_|^del_token_|^stop_script_|^del_admin_|^del_pass_"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_upload))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_all_messages))

async def start_sub_bot(token: str):
    try:
        sub_app = ApplicationBuilder().token(token).connect_timeout(30).read_timeout(30).build()
        register_handlers(sub_app)
        await sub_app.initialize()
        await sub_app.start()
        await sub_app.updater.start_polling(drop_pending_updates=True)
        active_sub_apps[token] = sub_app
        logging.info(f"Sub-bot operational: {token[:10]}...")
        return True
    except Exception as e:
        logging.error(f"Failed to start sub-bot: {e}")
        return False

async def stop_sub_bot(token: str):
    if token in active_sub_apps:
        try:
            sub_app = active_sub_apps[token]
            await sub_app.updater.stop()
            await sub_app.stop()
            await sub_app.shutdown()
        except Exception:
            pass
        del active_sub_apps[token]

async def add_new_bot(token: str, update: Update):
    current_cfg = load_config()
    if token in current_cfg["sub_tokens"] or token == MAIN_BOT_TOKEN:
        await update.message.reply_text("⚠️ এই টোকেনটি ইতিমধ্যেই যুক্ত আছে।")
        return

    success = await start_sub_bot(token)
    if success:
        current_cfg["sub_tokens"].append(token)
        save_config(current_cfg)
        await update.message.reply_text("✅ নতুন টোকেন বট সফলভাবে চালুর মাধ্যমে যুক্ত হয়েছে!")
    else:
        await update.message.reply_text("❌ অকার্যকর টোকেন অথবা নেটওয়ার্ক সমস্যার কারণে বট রান করা যায়নি।")

# ----------------- Application Entry Point ----------------- #
async def main():
    # Start Flask Web Server in a separate background thread for Render
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logging.info("ফ্লাস্ক ওয়েব সার্ভার ব্যাকগ্রাউন্ডে চালু হয়েছে...")

    while True:
        try:
            main_app = ApplicationBuilder().token(MAIN_BOT_TOKEN).connect_timeout(30).read_timeout(30).build()
            register_handlers(main_app)
            
            await main_app.initialize()
            await main_app.start()
            await main_app.updater.start_polling(drop_pending_updates=True)
            logging.info("মেইন বট সফলভাবে রান হয়েছে...")

            current_cfg = load_config()
            for token in current_cfg.get("sub_tokens", []):
                await start_sub_bot(token)

            for fname in current_cfg.get("script_files", []):
                file_path = os.path.join(os.getcwd(), fname)
                if os.path.exists(file_path):
                    proc = await asyncio.create_subprocess_exec(sys.executable, file_path)
                    active_script_processes[fname] = proc

            await asyncio.Event().wait()
            break
        except (NetworkError, TelegramError) as e:
            logging.warning(f"Network error detected: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            await asyncio.sleep(5)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped!")
