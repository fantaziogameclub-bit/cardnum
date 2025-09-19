import os
import logging
import psycopg2
import re
from urllib.parse import urlparse
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from typing import Optional

# --- Logging Configuration ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
try:
    TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    DATABASE_URL = os.environ["DATABASE_URL"]
    ADMIN_TELEGRAM_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
except KeyError as e:
    logger.error(f"FATAL: Environment variable {e} not set. Exiting.")
    exit()

# --- Conversation States ---
(
    MAIN_MENU,
    ADMIN_MENU, ADMIN_ADD_USER_CONFIRM, ADMIN_REMOVE_USER,
    VIEW_CHOOSE_PERSON, VIEW_CHOOSE_ACCOUNT,
    EDIT_MENU,
    ADD_CHOOSE_PERSON_TYPE, ADD_NEW_PERSON_NAME, ADD_CHOOSE_EXISTING_PERSON,
    ADD_CHOOSE_ITEM_TYPE,
    ADD_ACCOUNT_BANK, ADD_ACCOUNT_NUMBER, ADD_ACCOUNT_CARD, ADD_ACCOUNT_SHABA, ADD_ACCOUNT_PHOTO,
    ADD_DOC_NAME, ADD_DOC_TEXT, ADD_DOC_FILES, ADD_DOC_SAVE,
    DELETE_CHOOSE_TYPE, DELETE_CHOOSE_PERSON, DELETE_CONFIRM_PERSON,
    DELETE_CHOOSE_ACCOUNT_FOR_PERSON, DELETE_CHOOSE_ACCOUNT, DELETE_CONFIRM_ACCOUNT,
    CHANGE_CHOOSE_PERSON, CHANGE_CHOOSE_TARGET, CHANGE_PROMPT_PERSON_NAME, CHANGE_SAVE_PERSON_NAME,
    CHANGE_CHOOSE_ACCOUNT, CHANGE_CHOOSE_FIELD, CHANGE_PROMPT_FIELD_VALUE, CHANGE_SAVE_FIELD_VALUE,
) = range(34)

# --- Keyboard Buttons & Mappings ---
HOME_BUTTON = "صفحه اصلی 🏠"
BACK_BUTTON = "بازگشت 🔙"
SKIP_BUTTON = "رد شدن ⏭️"
NEXT_PAGE_BUTTON = "صفحه بعد ◀️"
PREV_PAGE_BUTTON = "▶️ صفحه قبل"
FINISH_SENDING_BUTTON = "اتمام ارسال ✅"
NO_BUTTON = "نه ❌"
YES_BUTTON = "بله ✅"
DELETE_BUTTON = "حذف کردن 🗑️"
YES_CONTINUE = "بله، ادامه✅"
NO_EDIT = "خیر، ویرایش متن✏️"

FIELD_TO_COLUMN_MAP = {
    "نام حساب 🧾": "account_name", # PATCH 1: Added account_name
    "نام بانک 🏦": "bank_name",
    "شماره حساب 🔢": "account_number",
    "شماره کارت 💳": "card_number",
    "شماره شبا 🌐": "shaba_number",
    "عکس کارت 🖼️": "card_photo_id",
}

# --- Database Functions ---
def get_db_connection():
    try:
        result = urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            dbname=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port
        )
        return conn
    except (psycopg2.OperationalError, ValueError) as e:
        logger.error(f"Could not connect to database: {e}")
        return None

def setup_database():
    """Initializes database tables if they don't exist."""
    conn = get_db_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
            # --- Standard Tables ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    first_name TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS persons (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
                    account_name TEXT NOT NULL,
                    bank_name TEXT,
                    account_number TEXT,
                    card_number TEXT,
                    shaba_number TEXT,
                    card_photo_id TEXT
                );
            """)
            # --- New Table for Documents ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
                    doc_name TEXT NOT NULL,
                    doc_text TEXT,
                    file_ids TEXT[]
                );
            """)
            # --- Ensure Admin Exists ---
            cur.execute(
                "INSERT INTO users (telegram_id, first_name) VALUES (%s, %s) ON CONFLICT (telegram_id) DO NOTHING;",
                (ADMIN_TELEGRAM_ID, 'Admin')
            )
            conn.commit()
    except psycopg2.Error as e:
        logger.error(f"Database setup error: {e}")
    finally:
        conn.close()

# --- Helper Functions ---

def is_authorized(user_id: int) -> Optional[bool]:
    conn = get_db_connection()
    if not conn: return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE telegram_id = %s;", (user_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def is_admin(user_id: int) -> bool:
    """Checks if a user is the admin."""
    return user_id == ADMIN_TELEGRAM_ID

def build_menu_paginated(buttons: list, page: int, n_cols: int, items_per_page: int = 10, footer_buttons=None):

    """Creates a paginated ReplyKeyboardMarkup."""
    start_index = page * items_per_page
    end_index = start_index + items_per_page
    paginated_buttons = buttons[start_index:end_index]

    menu = [paginated_buttons[i:i + n_cols] for i in range(0, len(paginated_buttons), n_cols)]

    # کنترل صفحه‌بندی
    pagination_controls = []
    if page > 0:
        pagination_controls.append(PREV_PAGE_BUTTON)
    if end_index < len(buttons):
        pagination_controls.append(NEXT_PAGE_BUTTON)

    if pagination_controls:
        menu.append(pagination_controls)
     # اضافه کردن دکمه‌های پایینی اگر وجود داشته باشند
    if footer_buttons:
        menu.extend(footer_buttons)
        # اگر دکمه "صفحه اصلی" در footer_buttons نیست، اضافه کن
        if not any(HOME_BUTTON in row for row in footer_buttons):
            menu.append([HOME_BUTTON])
    else:
        menu.append([HOME_BUTTON])
    return ReplyKeyboardMarkup(menu, resize_keyboard=True)


async def get_persons_from_db(context: ContextTypes.DEFAULT_TYPE):
    """Fetches all persons and stores them in context."""
    conn = get_db_connection()
    if not conn:
        await context.bot.send_message(
        chat_id=context._chat_id_and_data[0],
        text="⚠️ خطا در اتصال به پایگاه داده. لطفاً بعداً دوباره تلاش کنید."
        )
        return MAIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM persons ORDER BY name;")
            persons = cur.fetchall()
            # Store as list of tuples for ordered access and as dict for quick lookup
            # context.user_data['persons_list'] = {p[1]: p[0] for p in persons}
            context.user_data['persons_list_tuples'] = persons
            context.user_data['persons_list_dict'] = {p[1]: p[0] for p in persons}
            return persons
    finally:
        conn.close()

async def get_accounts_for_person_from_db(person_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Fetches all accounts for a person and stores them in context."""
    conn = get_db_connection()
    if not conn: return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, bank_name FROM accounts WHERE person_id = %s ORDER BY bank_name;", (person_id,))
            accounts = cur.fetchall()
            # Use a more robust key, e.g., combining bank, card, and id
            # context.user_data['accounts_list'] = {f"{acc[1] or 'N/A'} - {acc[2] or 'N/A'} ({acc[0]})": acc[0] for acc in accounts}
            context.user_data['accounts_list_tuples'] = accounts
            context.user_data['accounts_list_dict'] = {f"{acc[1]} ({acc[2] or 'N/A'})": acc[0] for acc in accounts}
            return accounts
    finally:
        conn.close()

# --- Start & Main Menu Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    auth_status = is_authorized(user.id)
    if auth_status is None:
        await update.message.reply_text("⚠️ خطا در اتصال به پایگاه داده. لطفاً بعداً دوباره تلاش کنید.")
        return ConversationHandler.END

    if auth_status is False:
        await update.message.reply_text(
          "🚫 شما اجازه دسترسی به این ربات را ندارید.\n"
          f"برای درخواست دسترسی، این شناسه را برای ادمین ارسال کنید:\n`{user.id}`",
         parse_mode=ParseMode.MARKDOWN_V2
        )
        try:
            await context.bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=f"""📥 درخواست دسترسی جدید!
                
👤 کاربر: {user.first_name}
🆔 شناسه: `{user.id}`
                
برای افزودن این کاربر، به بخش ادمین رفته و شناسه بالا را وارد کنید.""",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Failed to send new user notification to admin: {e}")
        return ConversationHandler.END

# ادامه کار وقتی کاربر مجاز است...


    conn = get_db_connection()
    if conn:
      try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (telegram_id, first_name) VALUES (%s, %s) ON CONFLICT (telegram_id) DO UPDATE SET first_name = EXCLUDED.first_name;",
                (user.id, user.first_name)
            )
            conn.commit()
      finally:
        conn.close()

    keyboard = [["مشاهده اطلاعات 📄"], ["ویرایش ✏️", "ادمین 🛠️"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"سلام {user.first_name}! به دفترچه بانکی خوش آمدید.", reply_markup=reply_markup)
    return MAIN_MENU

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    return await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
    return await main_menu(update, context)
    
# --- Admin Flow Handlers (Copied from previous version, unchanged) ---
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 این بخش فقط برای ادمین است.")
        return MAIN_MENU
    keyboard = [["مشاهده کاربران مجاز 👁️"], ["افزودن کاربر ➕", "حذف کاربر ➖"], [HOME_BUTTON]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("منوی ادمین:", reply_markup=reply_markup)
    return ADMIN_MENU

async def admin_view_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This can also be paginated if the user list grows large, but for now it's simple.
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("خطا در اتصال به پایگاه داده.")
        return ADMIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id, first_name FROM users ORDER BY first_name;")
            users = cur.fetchall()
            message = "لیست کاربران مجاز:\n\n" + "\n".join([f"👤 {fn}\n🆔 `{tid}`" for tid, fn in users]) if users else "هیچ کاربری ثبت نشده."
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
    finally: conn.close()
    return ADMIN_MENU

async def admin_prompt_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("شناسه عددی تلگرام کاربر جدید را وارد کنید:", reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON]], resize_keyboard=True))
    return ADMIN_ADD_USER_CONFIRM

# --- Admin Add User Confirmation Flow ---


# async def admin_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     try: user_id_to_add = int(update.message.text)
#     except (ValueError, TypeError):
#         await update.message.reply_text("❌ شناسه نامعتبر است. یک عدد وارد کنید.")
#         return ADMIN_ADD_USER
#     conn = get_db_connection()
#     if not conn:
#         await update.message.reply_text("خطا در اتصال به پایگاه داده.")
#         return await admin_menu(update, context)
#     try:
#         with conn.cursor() as cur:
#             cur.execute("SELECT 1 FROM users WHERE telegram_id = %s;", (user_id_to_add,))
#             if cur.fetchone():
#                 await update.message.reply_text("⚠️ این کاربر از قبل وجود دارد.")
#                 return await admin_menu(update, context)
#             cur.execute("INSERT INTO users (telegram_id, first_name) VALUES (%s, %s);", (user_id_to_add, 'N/A'))
#             conn.commit()
#         try:
#             await context.bot.send_message(chat_id=user_id_to_add, text="🎉 دسترسی شما به ربات فعال شد. /start را بزنید.")
#             await update.message.reply_text(f"✅ کاربر `{user_id_to_add}` اضافه شد و به او اطلاع داده شد.", parse_mode=ParseMode.MARKDOWN_V2)
#         except Exception as e:
#             await update.message.reply_text(f"✅ کاربر `{user_id_to_add}` اضافه شد، اما ارسال پیام به او ناموفق بود.", parse_mode=ParseMode.MARKDOWN_V2)
#     except psycopg2.Error as e: await update.message.reply_text("❌ خطایی در افزودن کاربر رخ داد.")
#     finally: conn.close()
#     return await admin_menu(update, context)

async def admin_prompt_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    conn = get_db_connection()
    if not conn: return await admin_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id, first_name FROM users WHERE telegram_id != %s;", (ADMIN_TELEGRAM_ID,))
            users = cur.fetchall()
            if not users:
                await update.message.reply_text("هیچ کاربری برای حذف وجود ندارد.")
                return await admin_menu(update, context)
            buttons = [f"{fn} ({tid})" for tid, fn in users]
            keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON]])
            await update.message.reply_text("کدام کاربر را حذف می‌کنید؟", reply_markup=keyboard)
            return ADMIN_REMOVE_USER
    finally: conn.close()

async def admin_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try: user_id_to_remove = int(update.message.text.split('(')[-1].strip(')'))
    except (ValueError, TypeError, IndexError):
        await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
        return ADMIN_REMOVE_USER
    conn = get_db_connection()
    if not conn: return await admin_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE telegram_id = %s;", (user_id_to_remove,))
            conn.commit()
            if cur.rowcount > 0:
                await update.message.reply_text(f"✅ کاربر `{user_id_to_remove}` حذف شد.\\", parse_mode=ParseMode.MARKDOWN_V2)
                try: await context.bot.send_message(chat_id=user_id_to_remove, text="🚫 دسترسی شما به ربات لغو شد.")
                except Exception: pass
            else: await update.message.reply_text("کاربر یافت نشد.")
    except psycopg2.Error: await update.message.reply_text("❌ خطایی در حذف رخ داد.")
    finally: conn.close()
    return await admin_menu(update, context)

# --- Admin Add User Confirmation Flow ---

async def admin_add_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_add = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("❌ شناسه نامعتبر است. یک عدد وارد کنید.")
        return ADMIN_ADD_USER_CONFIRM

    # Check if user already exists
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE telegram_id = %s;", (user_id_to_add,))
            if cur.fetchone():
                await update.message.reply_text("⚠️ این کاربر از قبل وجود دارد.")
                return await admin_menu(update, context)
        conn.close()

    try:
        chat = await context.bot.get_chat(user_id_to_add)
        user_info = {
            'id': chat.id,
            'first_name': chat.first_name,
            'username': f"@{chat.username}" if chat.username else "ندارد"
        }
        context.user_data['user_to_add'] = user_info
        message = (
            f"اطلاعات کاربر:\n"
            f"👤 نام: {user_info['first_name']}\n"
            f"🆔 شناسه: `{user_info['id']}`\n"
            f"🔖 نام کاربری: {user_info['username']}\n\n"
            "آیا این کاربر را اضافه می‌کنید؟"
        )
        keyboard = [[YES_BUTTON], [NO_BUTTON]]
        await update.message.reply_text(message, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode=ParseMode.MARKDOWN_V2)
        return ADMIN_ADD_USER_CONFIRM

    except BadRequest:
        await update.message.reply_text("❌ کاربری با این شناسه یافت نشد. شناسه را بررسی کنید.")
        return ADMIN_ADD_USER_CONFIRM
    except Exception as e:
        logger.error(f"Error in admin_add_user_confirm for ID {user_id_to_add}: {e}", exc_info=True)
        await update.message.reply_text("خطایی در دریافت اطلاعات کاربر رخ داد.")
        return await admin_menu(update, context)

async def admin_add_user_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_to_add = context.user_data.get('user_to_add')
    if not user_to_add:
        return await admin_menu(update, context)

    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("خطا در اتصال به پایگاه داده.")
        return await admin_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (telegram_id, first_name) VALUES (%s, %s);", (user_to_add['id'], user_to_add['first_name']))
            conn.commit()
        try:
            await context.bot.send_message(chat_id=user_to_add['id'], text="🎉 دسترسی شما به ربات فعال شد. /start را بزنید.")
            await update.message.reply_text(f"✅ کاربر `{user_to_add['id']}` اضافه شد و به او اطلاع داده شد.", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception:
            await update.message.reply_text(f"✅ کاربر `{user_to_add['id']}` اضافه شد، اما ارسال پیام به او ناموفق بود.", parse_mode=ParseMode.MARKDOWN_V2)
    except psycopg2.Error: await update.message.reply_text("❌ خطایی در افزودن کاربر رخ داد.")
    finally: conn.close()
    context.user_data.pop('user_to_add', None)
    return await admin_menu(update, context)


# --- View Information Flow ---
async def view_choose_person(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> int:
    persons = await get_persons_from_db(context)
    if persons is None:
        await update.message.reply_text("⚠️ خطا در اتصال به پایگاه داده. لطفاً بعداً دوباره تلاش کنید.")
        return MAIN_MENU

    if not persons:
        await update.message.reply_text("هیچ شخصی ثبت نشده. از منوی ویرایش، شخص جدید اضافه کنید.")
        return await start(update, context)
    
    page = context.user_data.get('page', page)
    context.user_data['page'] = page
    buttons = [p[1] for p in persons]
    keyboard = build_menu_paginated(buttons, page=page, n_cols=2, footer_buttons=[[HOME_BUTTON]])
    await update.message.reply_text("اطلاعات کدام شخص را می‌خواهید؟", reply_markup=keyboard)
    return VIEW_CHOOSE_PERSON

async def view_person_paginator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles next/previous page buttons for person list."""
    current_page = context.user_data.get('page', 0)
    if update.message.text == NEXT_PAGE_BUTTON:
        return await view_choose_person(update, context, page=current_page + 1)
    elif update.message.text == PREV_PAGE_BUTTON:
        return await view_choose_person(update, context, page=current_page - 1)
    # If neither, it's a person selection, so fall through to the next handler
    return await view_choose_account(update, context)

# --- Add Flow (Major Changes) ---
async def add_choose_person_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [["شخص جدید 👤", "شخص موجود 👥"], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text("برای چه کسی اطلاعات اضافه می‌کنید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_CHOOSE_PERSON_TYPE
#
async def view_choose_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = update.message.text
    person_id = context.user_data.get('persons_list_dict', {}).get(person_name)
    if not person_id:
        await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
        return VIEW_CHOOSE_PERSON
    context.user_data['selected_person_id'] = person_id
    context.user_data['selected_person_name'] = person_name
    
    accounts = await get_accounts_for_person_from_db(person_id, context)
    buttons = list(context.user_data['accounts_list_dict'].keys())
    if not buttons:
        await update.message.reply_text(f"هیچ حسابی برای '{person_name}' ثبت نشده.")
        return VIEW_CHOOSE_PERSON
        # # Re-display person list
        # persons = await get_persons_from_db(context)
        # buttons = [p[1] for p in persons]
        # keyboard = build_menu_paginated(buttons, 2, n_cols=2, footer_buttons=[[HOME_BUTTON]])
        # await update.message.reply_text("شخص دیگری را انتخاب کنید:", reply_markup=keyboard)
        # return VIEW_CHOOSE_PERSON
    
    
    keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text(f"حساب‌های '{person_name}'. کدام حساب؟", reply_markup=keyboard)
    return VIEW_CHOOSE_ACCOUNT

async def view_display_account_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    account_key = update.message.text
    account_id = context.user_data.get('accounts_list_dict', {}).get(account_key)
    if not account_id:
        await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
        return VIEW_CHOOSE_ACCOUNT
    
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("خطا در اتصال به پایگاه داده.")
        return MAIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bank_name, account_number, card_number, shaba_number, card_photo_id FROM accounts WHERE id = %s;", (account_id,))
            account = cur.fetchone()
            if not account:
                await update.message.reply_text("خطا: حساب یافت نشد.")
                return await view_choose_account(update, context) # Reshow accounts
            
            bank, acc_num, card_num, shaba, photo_id = account
            person_name = context.user_data.get('selected_person_name', 'N/A')
            message = f"📄 *اطلاعات حساب*\n\n👤 *صاحب:* {person_name}\n🏦 *بانک:* {bank or 'N/A'}\n"
            if acc_num: message += f"🔢 *حساب:*\n`{acc_num}`\n"
            if card_num: message += f"💳 *کارت:*\n`{card_num}`\n"
            if shaba: message += f"🌐 *شبا:*\n`{shaba}`\n"
            
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
            if photo_id:
                try: await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id, caption="🖼️ تصویر کارت")
                except: await update.message.reply_text("⚠️ تصویر کارت قابل بارگذاری نبود.")
    finally: conn.close()
    return VIEW_CHOOSE_ACCOUNT # Stay in the same state to allow viewing another account
#_____________________----$$$$$$$$$$$$$$------_______
# --- ADD FLOW ---
async def add_choose_item_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This function is reached after a person is selected or created
    keyboard = [["حساب بانکی 💳"], ["مدرک 📑"], [BACK_BUTTON, HOME_BUTTON]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("چه نوع اطلاعاتی می‌خواهید اضافه کنید؟", reply_markup=reply_markup)
    return ADD_CHOOSE_ITEM_TYPE

# --- PATCH 1: New functions for adding account name ---
async def add_prompt_account_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for a custom name for the bank account."""
    context.user_data['new_account'] = {}
    await update.message.reply_text(
        "یک نام برای این حساب انتخاب کنید (مثال: حساب حقوق، حساب شخصی).",
        reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON]], resize_keyboard=True)
    )
    return ADD_ACCOUNT_NAME

async def add_get_account_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the account name and proceeds to ask for the bank name."""
    account_name = update.message.text
    if not account_name or len(account_name.strip()) == 0:
        await update.message.reply_text("لطفاً یک نام معتبر وارد کنید.")
        return ADD_ACCOUNT_NAME
    
    context.user_data['new_account']['account_name'] = account_name.strip()
    
    await update.message.reply_text(
        "نام بانک را وارد کنید:",
        reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON, SKIP_BUTTON]], resize_keyboard=True)
    )
    return ADD_ACCOUNT_BANK
    
#_____________________====$$$$$$$$$$=====________
# --- Edit Menu ---
async def edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 این بخش فقط برای ادمین است.")
        return MAIN_MENU
    
    keyboard = [["اضافه کردن ➕"], ["تغییر دادن 📝", DELETE_BUTTON], [HOME_BUTTON]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("منوی ویرایش:", reply_markup=reply_markup)
    context.user_data.clear() # Clear previous edit data
    return EDIT_MENU

# --- Add Flow (Unchanged) ---
# ... (Functions from previous response: add_choose_person_type, ..., add_account_get_photo_and_save)
# For brevity, these functions are not repeated here but are assumed to be present in the final file.
# I will write them out again to be complete as requested.

async def add_choose_person_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [["شخص جدید 👤", "شخص موجود 👥"], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text("برای چه کسی حساب اضافه می‌کنید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_CHOOSE_PERSON_TYPE

async def add_prompt_new_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("نام کامل شخص جدید را وارد کنید:", reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return ADD_NEW_PERSON_NAME

async def add_save_new_person_and_prompt_item_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = update.message.text.strip()
    if not person_name:
        await update.message.reply_text("نام نمی‌تواند خالی باشد.")
        return ADD_NEW_PERSON_NAME
    conn = get_db_connection()
    if not conn: return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO persons (name) VALUES (%s) RETURNING id;", (person_name,))
            person_id = cur.fetchone()[0]
            conn.commit()
            context.user_data['new_account'] = {}
            context.user_data['selected_person_id'] = person_id
            context.user_data['new_account_person_id'] = person_id
            await update.message.reply_text(f"✅ شخص '{person_name}' اضافه شد.")
    except psycopg2.IntegrityError:
        await update.message.reply_text("❌ شخصی با این نام وجود دارد.")
        return ADD_NEW_PERSON_NAME
    except psycopg2.Error:
        await update.message.reply_text("❌ خطایی در افزودن شخص رخ داد.")
        return await edit_menu(update, context)
    finally: conn.close()
    
    keyboard = [["حساب بانکی 💳", "مدرک 📄"], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text("چه نوع اطلاعاتی ثبت می‌کنید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_CHOOSE_ITEM_TYPE

    
    # await update.message.reply_text("۱/۵ - نام بانک:", reply_markup=ReplyKeyboardMarkup([[SKIP_BUTTON], [BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    # return ADD_ACCOUNT_BANK

async def add_choose_existing_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    persons = await get_persons_from_db(context)
    if not persons:
        await update.message.reply_text("هیچ شخصی نیست. ابتدا 'شخص جدید' اضافه کنید.")
        return await add_choose_person_type(update, context)
    buttons = [p[1] for p in persons]
    keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text("برای کدام شخص حساب اضافه می‌کنید؟", reply_markup=keyboard)
    return ADD_CHOOSE_EXISTING_PERSON

async def add_set_existing_person_and_prompt_item_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = update.message.text
    person_id = context.user_data.get('persons_list_dict', {}).get(person_name)
    if not person_id: return ADD_CHOOSE_EXISTING_PERSON
    context.user_data['selected_person_id'] = person_id
    context.user_data['new_account_person_id'] = person_id
    # context.user_data['new_account'] = {}
    keyboard = [["حساب بانکی 💳", "مدرک 📄"], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text("چه نوع اطلاعاتی ثبت می‌کنید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_CHOOSE_ITEM_TYPE

# --- New Document Add Flow Handlers ---
async def add_prompt_doc_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_doc'] = {}
    await update.message.reply_text("نام مدرک را وارد کنید (مثلا: شناسنامه، پاسپورت):", reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return ADD_DOC_NAME

async def add_get_doc_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_doc']['name'] = update.message.text
    await update.message.reply_text("متن مربوط به مدرک را وارد کنید:", reply_markup=ReplyKeyboardMarkup([[SKIP_BUTTON], [BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return ADD_DOC_TEXT

async def add_get_doc_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc_text = None if update.message.text == SKIP_BUTTON else update.message.text
    context.user_data['new_doc']['text'] = doc_text
    # Simple confirmation for text
    keyboard = [[YES_CONTINUE, NO_EDIT], [BACK_BUTTON, HOME_BUTTON]]

    await update.message.reply_text(f"متن ثبت شود؟\n---\n{doc_text or 'خالی'}\n---", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_DOC_FILES # State for confirmation before file upload

async def add_prompt_doc_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_doc']['files'] = []
    keyboard = [[FINISH_SENDING_BUTTON], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text("عکس‌ها و فایل‌های مدرک را ارسال کنید. پس از اتمام، دکمه 'اتمام ارسال' را بزنید.", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_DOC_FILES
#______÷÷÷÷÷÷÷÷÷÷÷÷_______________÷÷÷÷÷÷÷____

async def add_get_doc_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives photos or documents and stores their file_ids."""
    if 'doc_files' not in context.user_data:
        context.user_data['doc_files'] = []

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id # Get highest quality
    elif update.message.document:
        file_id = update.message.document.file_id

    if file_id:
        context.user_data['doc_files'].append(file_id)
        await update.message.reply_text(
            f"✅ فایل دریافت شد. تاکنون {len(context.user_data['doc_files'])} فایل ارسال کرده‌اید.\n"
            "می‌توانید فایل‌های بیشتری بفرستید یا دکمه 'اتمام' را بزنید."
        )
    else:
        await update.message.reply_text("لطفاً یک عکس یا فایل معتبر ارسال کنید.")
        
    return ADD_DOC_FILES
#______÷÷÷÷÷÷÷÷÷÷÷÷_______________÷÷÷÷÷÷÷____
async def add_confirm_doc_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_doc = context.user_data.get('new_doc', {})
    message = (
        f"آیا این مدرک ثبت شود؟\n\n"
        f"📄 نام: {new_doc.get('name', 'N/A')}\n"
        f"📝 متن: {new_doc.get('text', 'ندارد')}\n"
        f"🖼️ تعداد فایل: {len(new_doc.get('files', []))}"
    )
    keyboard = [["بله، ثبت کن ✅", "نه، از اول ❌"], [HOME_BUTTON]]
    await update.message.reply_text(message, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_DOC_SAVE

async def add_save_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_doc = context.user_data.get('new_doc')
    person_id = context.user_data.get('selected_person_id')
    if not new_doc or not person_id:
        await update.message.reply_text("خطای داخلی، لطفا دوباره تلاش کنید.")
        return await edit_menu(update, context)

    conn = get_db_connection()
    if not conn: return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (person_id, doc_name, doc_text, file_ids) VALUES (%s, %s, %s, %s);",
                (person_id, new_doc.get('name'), new_doc.get('text'), new_doc.get('files', []))
            )
            conn.commit()
            await update.message.reply_text("✅ مدرک جدید با موفقیت ثبت شد.")
    except psycopg2.Error as e:
        logger.error(f"Error saving document: {e}")
        await update.message.reply_text("❌ خطایی در ذخیره مدرک رخ داد.")
    finally:
        conn.close()

    # Cleanup
    context.user_data.pop('new_doc', None)
    context.user_data.pop('selected_person_id', None)
    return await edit_menu(update, context)
    
async def add_account_get_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_account']['bank_name'] = None if update.message.text == SKIP_BUTTON else update.message.text
    # await update.message.reply_text("۲/۵ - شماره حساب:", reply_markup=update.message.reply_keyboard)
    await update.message.reply_text("۲/۵ - شماره حساب:", reply_markup=ReplyKeyboardMarkup([[SKIP_BUTTON], [BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return ADD_ACCOUNT_NUMBER

async def add_account_get_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_account']['account_number'] = None if update.message.text == SKIP_BUTTON else update.message.text
    # await update.message.reply_text("۳/۵ - شماره کارت:", reply_markup=update.message.reply_keyboard)
    await update.message.reply_text("۳/۵ - شماره کارت:", reply_markup=ReplyKeyboardMarkup([[SKIP_BUTTON], [BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return ADD_ACCOUNT_CARD

async def add_account_get_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    card_number = None if update.message.text == SKIP_BUTTON else update.message.text.strip()
    # اعتبارسنجی شماره کارت (16 رقم و شروع با 6 یا 5 یا 4)
    if card_number and not re.fullmatch(r"(4|5|6)\d{15}", card_number):
        await update.message.reply_text("⚠️ شماره کارت معتبر نیست. شماره کارت باید ۱۶ رقم و با 4 یا 5 یا 6 شروع شود.")
        return ADD_ACCOUNT_CARD
    context.user_data['new_account']['card_number'] = card_number
    await update.message.reply_text("۴/۵ - شماره شبا (بدون IR):", reply_markup=ReplyKeyboardMarkup([[SKIP_BUTTON], [BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return ADD_ACCOUNT_SHABA

async def add_account_get_shaba(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    shaba_number = None if update.message.text == SKIP_BUTTON else update.message.text.strip()
    # اعتبارسنجی شماره شبا (24 رقم و فقط عدد، بدون IR)
    if shaba_number and not re.fullmatch(r"\d{24}", shaba_number):
        await update.message.reply_text("⚠️ شماره شبا معتبر نیست. باید ۲۴ رقم و فقط عدد باشد (بدون IR).")
        return ADD_ACCOUNT_SHABA
    context.user_data['new_account']['shaba_number'] = shaba_number
    await update.message.reply_text("۵/۵ - تصویر کارت:", reply_markup=ReplyKeyboardMarkup([[SKIP_BUTTON], [BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return ADD_ACCOUNT_PHOTO

async def add_account_get_photo_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_account = context.user_data.get('new_account', {})
    person_id = context.user_data.get('new_account_person_id')
    if update.message.photo: new_account['card_photo_id'] = update.message.photo[-1].file_id
    elif update.message.text == SKIP_BUTTON: new_account['card_photo_id'] = None
    else:
        await update.message.reply_text("لطفاً عکس بفرستید یا رد شوید.")
        return ADD_ACCOUNT_PHOTO
    if not person_id: return await start(update, context)
    conn = get_db_connection()
    if not conn: return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (person_id, bank_name, account_number, card_number, shaba_number, card_photo_id) VALUES (%s, %s, %s, %s, %s, %s);",
                (person_id, new_account.get('bank_name'), new_account.get('account_number'), new_account.get('card_number'), new_account.get('shaba_number'), new_account.get('card_photo_id'))
            )
            conn.commit()
            await update.message.reply_text("✅ حساب جدید با موفقیت ثبت شد.")
    except psycopg2.Error as e: await update.message.reply_text("❌ خطایی در ذخیره حساب رخ داد.")
    finally: conn.close()
    context.user_data.pop('new_account', None)
    context.user_data.pop('new_account_person_id', None)
    return await edit_menu(update, context)

# --- Delete Flow (Unchanged) ---
# ... (Functions from previous response: delete_choose_type, ..., delete_execute_account_deletion)
# I will write them out again to be complete as requested.
async def delete_choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [["حذف شخص 👤", "حذف حساب 💳"], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text("قصد حذف چه چیزی را دارید؟\n\n⚠️ *توجه:* با حذف شخص، تمام حساب‌هایش نیز حذف می‌شود\\.", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode=ParseMode.MARKDOWN_V2)
    return DELETE_CHOOSE_TYPE

async def delete_choose_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    persons = await get_persons_from_db(context)
    if not persons:
        await update.message.reply_text("هیچ شخصی برای حذف نیست.")
        return await edit_menu(update, context)
    buttons = [p[1] for p in persons]
    keyboard = build_menu_paginated(buttons, 0,  n_cols=2,footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text("کدام شخص را حذف می‌کنید؟", reply_markup=keyboard)
    return DELETE_CHOOSE_PERSON

async def delete_confirm_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = update.message.text
    person_id = context.user_data.get('persons_list_dict', {}).get(person_name)
    if not person_id: return DELETE_CHOOSE_PERSON
    context.user_data['person_to_delete'] = {'id': person_id, 'name': person_name}
    keyboard = [[YES_BUTTON , NO_BUTTON], [HOME_BUTTON]]
    await update.message.reply_text(f"‼️ *اخطار نهایی*\nآیا از حذف '{person_name}' و تمام حساب‌هایش مطمئنید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode=ParseMode.MARKDOWN_V2)
    return DELETE_CONFIRM_PERSON

async def delete_execute_person_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_to_delete = context.user_data.get('person_to_delete')
    if not person_to_delete: return await edit_menu(update, context)
    conn = get_db_connection()
    if not conn: return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM persons WHERE id = %s;", (person_to_delete['id'],))
            conn.commit()
            await update.message.reply_text(f"✅ شخص '{person_to_delete['name']}' حذف شد.")
    except psycopg2.Error: await update.message.reply_text("❌ خطایی در حذف رخ داد.")
    finally: conn.close()
    context.user_data.pop('person_to_delete', None)
    return await edit_menu(update, context)

async def delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("عملیات حذف لغو شد.")
    context.user_data.pop('person_to_delete', None)
    context.user_data.pop('account_to_delete', None)
    return await edit_menu(update, context)

async def delete_choose_account_for_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    persons = await get_persons_from_db(context)
    if not persons:
        await update.message.reply_text("هیچ شخصی نیست.")
        return await edit_menu(update, context)
    buttons = [p[1] for p in persons]
    keyboard = build_menu_paginated(buttons, 0,  n_cols=2,footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text("حساب مورد نظر برای کدام شخص است؟", reply_markup=keyboard)
    return DELETE_CHOOSE_ACCOUNT_FOR_PERSON

async def delete_choose_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = update.message.text
    person_id = context.user_data.get('persons_list_dict', {}).get(person_name)
    if not person_id: return DELETE_CHOOSE_ACCOUNT_FOR_PERSON
    accounts = await get_accounts_for_person_from_db(person_id, context)
    if not accounts:
        await update.message.reply_text(f"هیچ حسابی برای '{person_name}' نیست.")
        return await delete_choose_account_for_person(update, context)
    buttons = list(context.user_data['accounts_list_dict'].keys())
    keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text(f"کدام حساب '{person_name}' را حذف می‌کنید؟", reply_markup=keyboard)
    return DELETE_CHOOSE_ACCOUNT

async def delete_confirm_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    account_key = update.message.text
    account_id = context.user_data.get('accounts_list_dict', {}).get(account_key)
    if not account_id: return DELETE_CHOOSE_ACCOUNT
    context.user_data['account_to_delete'] = {'id': account_id, 'key': account_key}
    # keyboard = [["بله، حذف کن ✅", "نه، لغو کن ❌"], [HOME_BUTTON]]
    keyboard = [[YES_BUTTON , NO_BUTTON], [HOME_BUTTON]]

    await update.message.reply_text(f"‼️ *اخطار نهایی*\nآیا از حذف حساب '{account_key}' مطمئنید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode=ParseMode.MARKDOWN_V2)
    return DELETE_CONFIRM_ACCOUNT

async def delete_execute_account_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    account_to_delete = context.user_data.get('account_to_delete')
    if not account_to_delete: return await edit_menu(update, context)
    conn = get_db_connection()
    if not conn: return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id = %s;", (account_to_delete['id'],))
            conn.commit()
            await update.message.reply_text(f"✅ حساب '{account_to_delete['key']}' حذف شد.")
    except psycopg2.Error: await update.message.reply_text("❌ خطایی در حذف رخ داد.")
    finally: conn.close()
    context.user_data.pop('account_to_delete', None)
    return await edit_menu(update, context)

# --- NEW: Change/Update Flow ---
async def change_choose_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    persons = await get_persons_from_db(context)
    if not persons:
        await update.message.reply_text("هیچ شخصی برای ویرایش وجود ندارد.")
        return await edit_menu(update, context)
    buttons = [p[1] for p in persons]
    keyboard = build_menu_paginated(buttons, 0,  n_cols=2,footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text("اطلاعات کدام شخص را می‌خواهید تغییر دهید؟", reply_markup=keyboard)
    return CHANGE_CHOOSE_PERSON

async def change_choose_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = update.message.text
    person_id = context.user_data.get('persons_list_dict', {}).get(person_name)
    if not person_id:
        await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
        return CHANGE_CHOOSE_PERSON
    context.user_data['change_person'] = {'id': person_id, 'name': person_name}
    keyboard = [["تغییر نام شخص 👤", "ویرایش یک حساب 💳"], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text(f"چه تغییری برای '{person_name}' ایجاد می‌کنید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return CHANGE_CHOOSE_TARGET

async def change_update_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ذخیره نام جدید شخص انتخاب‌شده."""
    new_name = context.user_data.get('person_new_name')
    person_id = context.user_data.get('person_id')
    if not (new_name and person_id):
        await update.message.reply_text("⚠️ اطلاعات کافی برای ذخیره تغییرات موجود نیست.", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
        return MAIN_MENU
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("⚠️ خطا در اتصال به پایگاه داده.", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
        return MAIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE persons SET name = %s WHERE id = %s;", (new_name, person_id))
            conn.commit()
        await update.message.reply_text("✅ نام شخص با موفقیت تغییر یافت.", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در تغییر نام: {e}", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
    finally:
        conn.close()
    return MAIN_MENU

async def change_prompt_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = context.user_data.get('change_person', {}).get('name', 'این شخص')
    await update.message.reply_text(f"نام جدید را برای '{person_name}' وارد کنید:", reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return CHANGE_PROMPT_PERSON_NAME

async def change_save_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name = update.message.text.strip()
    person_info = context.user_data.get('change_person')
    if not new_name or not person_info:
        await update.message.reply_text("نام نمی‌تواند خالی باشد.")
        return CHANGE_PROMPT_PERSON_NAME
    conn = get_db_connection()
    if not conn: return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE persons SET name = %s WHERE id = %s;", (new_name, person_info['id']))
            conn.commit()
            await update.message.reply_text(f"✅ نام شخص با موفقیت به '{new_name}' تغییر یافت.")
    except psycopg2.IntegrityError: await update.message.reply_text("❌ شخصی با این نام از قبل وجود دارد.")
    except psycopg2.Error: await update.message.reply_text("❌ خطایی در تغییر نام رخ داد.")
    finally: conn.close()
    return await edit_menu(update, context)

async def change_choose_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_id = context.user_data.get('change_person', {}).get('id')
    accounts = await get_accounts_for_person_from_db(person_id, context)
    if not accounts:
        await update.message.reply_text("هیچ حسابی برای ویرایش وجود ندارد.")
        return await change_choose_target(update, context)
    buttons = list(context.user_data['accounts_list_dict'].keys())
    keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text("کدام حساب را ویرایش می‌کنید؟", reply_markup=keyboard)
    return CHANGE_CHOOSE_ACCOUNT

async def change_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    account_key = update.message.text
    account_id = context.user_data.get('accounts_list_dict', {}).get(account_key)
    if not account_id:
        await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
        return CHANGE_CHOOSE_ACCOUNT
    context.user_data['change_account_id'] = account_id
    buttons = list(FIELD_TO_COLUMN_MAP.keys())
    keyboard = build_menu_paginated(buttons, 0,  n_cols=2,footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text("کدام فیلد را تغییر می‌دهید؟", reply_markup=keyboard)
    return CHANGE_CHOOSE_FIELD

async def change_update_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ذخیره مقدار جدید فیلد انتخاب‌شده در دیتابیس."""
    user_input = context.user_data.get('field_value')
    field_column = context.user_data.get('field_column')
    account_id = context.user_data.get('account_id')
    if not (field_column and account_id):
        await update.message.reply_text("⚠️ اطلاعات کافی برای ذخیره تغییرات موجود نیست.", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
        return MAIN_MENU
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("⚠️ خطا در اتصال به پایگاه داده.", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
        return MAIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE accounts SET {field_column} = %s WHERE id = %s;", (user_input, account_id))
            conn.commit()
        await update.message.reply_text("✅ تغییرات با موفقیت ذخیره شد.", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ذخیره تغییرات: {e}", reply_markup=ReplyKeyboardMarkup([[HOME_BUTTON]], resize_keyboard=True))
    finally:
        conn.close()
    return MAIN_MENU

async def change_prompt_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field_name = update.message.text
    if field_name not in FIELD_TO_COLUMN_MAP:
        await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
        return CHANGE_CHOOSE_FIELD
    context.user_data['change_field'] = field_name
    prompt = f"مقدار جدید را برای '{field_name}' وارد کنید (یا عکس بفرستید):"
    if field_name != "عکس کارت 🖼️":
        prompt = f"مقدار جدید را برای '{field_name}' وارد کنید:"
    await update.message.reply_text(prompt, reply_markup=ReplyKeyboardMarkup([[SKIP_BUTTON], [BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True))
    return CHANGE_PROMPT_FIELD_VALUE

async def change_save_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field_name = context.user_data.get('change_field')
    account_id = context.user_data.get('change_account_id')
    column_name = FIELD_TO_COLUMN_MAP.get(field_name)
    if not all([field_name, account_id, column_name]):
        await update.message.reply_text("خطای داخلی. لطفاً دوباره تلاش کنید.")
        return await edit_menu(update, context)
    new_value = None
    if update.message.text == SKIP_BUTTON:
        new_value = None
    elif column_name == 'card_photo_id':
        if update.message.photo: new_value = update.message.photo[-1].file_id
        else:
            await update.message.reply_text("لطفاً یک عکس ارسال کنید، رد شوید یا بازگردید.")
            return CHANGE_PROMPT_FIELD_VALUE
    else: # Text field
        if update.message.text: new_value = update.message.text
        else:
            await update.message.reply_text("لطفاً یک مقدار متنی وارد کنید، رد شوید یا بازگردید.")
            return CHANGE_PROMPT_FIELD_VALUE
    conn = get_db_connection()
    if not conn: return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            # Using f-string for column name is generally unsafe, but here it's
            # controlled by our internal FIELD_TO_COLUMN_MAP, so it's safe.
            query = f"UPDATE accounts SET {column_name} = %s WHERE id = %s;"
            cur.execute(query, (new_value, account_id))
            conn.commit()
            await update.message.reply_text(f"✅ فیلد '{field_name}' با موفقیت به‌روزرسانی شد.")
    except psycopg2.Error as e:
        await update.message.reply_text(f"❌ خطایی در به‌روزرسانی فیلد رخ داد: {e}")
    finally: conn.close()
    # Cleanup and return
    for key in ['change_person', 'change_account_id', 'change_field']:
        context.user_data.pop(key, None)
    return await edit_menu(update, context)


# --- Fallback & Cancel ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return await start(update, context)

# --- Main Application Setup ---
# imports, logging config, env vars, state definitions, keyboards, db functions
# ...
# تمام هندلرهای start, main_menu, admin, view, edit, add, delete, change بدون تغییر

def main() -> None:
    setup_database()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^مشاهده اطلاعات 📄$"), view_choose_person),
                MessageHandler(filters.Regex("^ویرایش ✏️$"), edit_menu),
                MessageHandler(filters.Regex("^ادمین 🛠️$"), admin_menu),
            ],
            ADMIN_MENU: [
                MessageHandler(filters.Regex("^مشاهده کاربران مجاز 👁️$"), admin_view_users),
                MessageHandler(filters.Regex("^افزودن کاربر ➕$"), admin_prompt_add_user),
                MessageHandler(filters.Regex("^حذف کاربر ➖$"), admin_prompt_remove_user),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            ADMIN_ADD_USER_CONFIRM: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), admin_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_user_confirm)
            ],
            ADMIN_REMOVE_USER: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), admin_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_user)
            ],
            VIEW_CHOOSE_PERSON: [
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, view_choose_account)
            ],
            VIEW_CHOOSE_ACCOUNT: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), view_choose_person),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, view_display_account_details)
            ],
            EDIT_MENU: [
                MessageHandler(filters.Regex("^اضافه کردن ➕$"), add_choose_person_type),
                MessageHandler(filters.Regex("^تغییر دادن 📝$"), change_choose_person),
                MessageHandler(filters.Regex(f"^{DELETE_BUTTON}$"), delete_choose_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            ADD_CHOOSE_PERSON_TYPE: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), edit_menu),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.Regex("^شخص جدید 👤$"), add_prompt_new_person_name),
                MessageHandler(filters.Regex("^شخص موجود 👥$"), add_choose_existing_person)
            ],
            ADD_NEW_PERSON_NAME: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_save_new_person_and_prompt_item_type)
            ],
            ADD_CHOOSE_EXISTING_PERSON: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_set_existing_person_and_prompt_item_type)
            ],
            ADD_ACCOUNT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_account_name),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_item_type),
            ],
            ADD_ACCOUNT_BANK: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_get_bank)
            ],
            ADD_ACCOUNT_NUMBER: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_get_number)
            ],
            ADD_ACCOUNT_CARD: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_get_card)
            ],
            ADD_ACCOUNT_SHABA: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_get_shaba)
            ],
            ADD_ACCOUNT_PHOTO: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.PHOTO | filters.TEXT, add_account_get_photo_and_save)
            ],
            ADD_CHOOSE_ITEM_TYPE: [
                MessageHandler(filters.Regex("^حساب بانکی 💳$"), add_account_get_bank),
                MessageHandler(filters.Regex("^مدرک 📄$"), add_prompt_doc_name),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            DELETE_CHOOSE_TYPE: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), edit_menu),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.Regex("^حذف شخص 👤$"), delete_choose_person),
                MessageHandler(filters.Regex("^حذف حساب 💳$"), delete_choose_account_for_person)
            ],
            DELETE_CHOOSE_PERSON: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), delete_choose_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm_person)
            ],
            DELETE_CONFIRM_PERSON: [
                MessageHandler(filters.Regex(f"^{YES_BUTTON}$"), delete_execute_person_deletion),
                MessageHandler(filters.Regex(f"^{NO_BUTTON}$"), delete_cancel),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            DELETE_CHOOSE_ACCOUNT_FOR_PERSON: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), delete_choose_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_choose_account)
            ],
            DELETE_CHOOSE_ACCOUNT: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), delete_choose_account_for_person),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm_account)
            ],
            DELETE_CONFIRM_ACCOUNT: [
                MessageHandler(filters.Regex(f"^{YES_BUTTON}$"), delete_execute_account_deletion),
                MessageHandler(filters.Regex(f"^{NO_BUTTON}$"), delete_cancel),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            CHANGE_CHOOSE_PERSON: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), edit_menu),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, change_choose_target)
            ],
            CHANGE_CHOOSE_TARGET: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), change_choose_person),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.Regex("^تغییر نام شخص 👤$"), change_prompt_person_name),
                MessageHandler(filters.Regex("^ویرایش یک حساب 💳$"), change_choose_account)
            ],
            CHANGE_PROMPT_PERSON_NAME: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), change_choose_target),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, change_save_person_name)
            ],
            CHANGE_CHOOSE_ACCOUNT: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), change_choose_target),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, change_choose_field)
            ],
            CHANGE_CHOOSE_FIELD: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), change_choose_account),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, change_prompt_field_value)
            ],
            CHANGE_PROMPT_FIELD_VALUE: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), change_choose_field),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT | filters.PHOTO, change_save_field_value)
            ],
            ADD_DOC_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_doc_name),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            ADD_DOC_TEXT: [
                MessageHandler(filters.Regex(f"^{YES_CONTINUE}$"), add_prompt_doc_files),  # هدایت به مرحله ارسال فایل
                MessageHandler(filters.Regex(f"^{NO_EDIT}$"), add_get_doc_text),  # برگشت به مرحله وارد کردن متن
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_doc_text),
                MessageHandler(filters.Regex(f"^{SKIP_BUTTON}$"), add_get_doc_text),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_get_doc_name),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            ADD_DOC_FILES: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, add_get_doc_files),
                # MessageHandler(filters.Regex(f"^{FINISH_SENDING_BUTTON}$"), add_confirm_doc_save),
                MessageHandler(filters.Regex(f"^{FINISH_SENDING_BUTTON}$"), add_save_document),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_get_doc_text),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],

            ADD_DOC_SAVE: [
                MessageHandler(filters.Regex(f"^{YES_BUTTON}$"), add_save_document),
                MessageHandler(filters.Regex(f"^{NO_BUTTON}$"), add_prompt_doc_name),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],

            CHANGE_SAVE_FIELD_VALUE: [
                MessageHandler(filters.Regex(f"^{YES_BUTTON}$"), change_update_field_value),
                MessageHandler(filters.Regex(f"^{NO_BUTTON}$"), change_prompt_field_value),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), change_choose_field),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],

            CHANGE_SAVE_PERSON_NAME: [
                MessageHandler(filters.Regex(f"^{YES_BUTTON}$"), change_update_person_name),
                MessageHandler(filters.Regex(f"^{NO_BUTTON}$"), change_prompt_person_name),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), change_choose_target),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],


        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            MessageHandler(filters.ALL, start)
        ],
        per_message=False,
    )
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == "__main__":
    main()
