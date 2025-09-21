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
# from telegram.constants import ParseMode
from telegram.error import BadRequest
from typing import Optional
from telegram.helpers import escape_markdown
from html import escape
# from telegram import ParseMode

try:
    from telegram.constants import ParseMode
except ImportError:
    from telegram import ParseMode

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
    VIEW_CHOOSE_PERSON, VIEW_CHOOSE_ACCOUNT,VIEW_ACCOUNT_DETAILS, VIEW_DISPLAY_ACCOUNT_DETAILS, VIEW_CHOOSE_DOCUMENT , VIEW_DISPLAY_DOCUMENT ,
    EDIT_MENU,
    ADD_CHOOSE_PERSON_TYPE, ADD_NEW_PERSON_NAME, ADD_CHOOSE_EXISTING_PERSON,
    ADD_CHOOSE_ITEM_TYPE,
    ADD_ACCOUNT_BANK, ADD_ACCOUNT_NAME ,ADD_ACCOUNT_NUMBER, ADD_ACCOUNT_CARD, ADD_ACCOUNT_SHABA, ADD_ACCOUNT_PHOTO,
    ADD_DOC_NAME, ADD_DOC_TEXT, ADD_DOC_FILES, ADD_DOC_SAVE,
    DELETE_CHOOSE_TYPE, DELETE_CHOOSE_PERSON, DELETE_CONFIRM_PERSON,
    DELETE_CHOOSE_ACCOUNT_FOR_PERSON, DELETE_CHOOSE_ACCOUNT, DELETE_CONFIRM_ACCOUNT,
    CHANGE_CHOOSE_PERSON, CHANGE_CHOOSE_TARGET, CHANGE_PROMPT_PERSON_NAME, CHANGE_SAVE_PERSON_NAME,
    CHANGE_CHOOSE_ACCOUNT, CHANGE_CHOOSE_FIELD, CHANGE_PROMPT_FIELD_VALUE, CHANGE_SAVE_FIELD_VALUE,
     
) = range(39)

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
DOCUMENTS_BUTTON = "مدارک 📑"

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
            context.user_data['accounts_list_dict'] = {
                f"{acc[1] or 'N/A'}": acc[0]
                for acc in accounts
            }

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
    
    # from telegram.helpers import escape_markdown

# --- پیام برای کاربر غیرمجاز + اطلاع به ادمین ---
    if auth_status is False:

        # پیام به خود کاربر
        user_msg= (
            f"🚫 شما اجازه دسترسی به این ربات را ندارید.\n"
            f"برای درخواست دسترسی، این شناسه را برای ادمین ارسال کنید:\n{user.id}"
        )
        user_safe_msg=escape_markdown(user_msg, version=2)
        await context.bot.send_message(
            chat_id=user.id,
            text=user_safe_msg,
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # پیام به ادمین
        try:
            admin_msg = (
                f"📥 درخواست دسترسی جدید!\n\n"
                f"👤 کاربر: {user.first_name or ''}\n"
                f"🔖 نام کاربری: @{user.username}" if user.username else "ندارد" + "\n"
                f"🆔 شناسه: {user.id}\n\n"
                "برای افزودن این کاربر، به بخش ادمین رفته و شناسه بالا را وارد کنید."
            )
            admin_msg_safe = escape_markdown(admin_msg, version=2)
            await context.bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=admin_msg_safe,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Failed to send new user notification to admin: {e}")
            await update.message.reply_text(
                "⚠️ خطا در ارسال درخواست به مدیر. لطفاً بعداً دوباره تلاش کنید."
            )

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

    # first_name_safe = escape_markdown(user.first_name or "کاربر", version=2)
    keyboard = [["مشاهده اطلاعات 📄"], ["ویرایش ✏️", "ادمین 🛠️"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    safe_text = escape_markdown(f"سلام {user.first_name or 'کاربر'}! به دفترچه بانکی خوش آمدید.", version=2)
    await update.message.reply_text(
        safe_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # await update.message.reply_text(f"سلام `{first_name_safe}`! به دفترچه بانکی خوش آمدید.", reply_markup=reply_markup ,parse_mode=ParseMode.MARKDOWN_V2)
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
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 این بخش فقط برای ادمین است.")
        return MAIN_MENU
    
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("خطا در اتصال به پایگاه داده.")
        return ADMIN_MENU
    
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id, first_name FROM users ORDER BY first_name;")
            users = cur.fetchall()
        
        if not users:
            await update.message.reply_text("هیچ کاربری ثبت نشده است.")
            return ADMIN_MENU

        users_lines = []
        for tid, fn in users:
            users_lines.append(f"👤 {fn or 'بدون‌نام'}\n🆔 {tid}")
        
        message = "لیست کاربران مجاز:\n\n" + "\n\n".join(users_lines)
        message_safe = escape_markdown(message, version=2)
        await update.message.reply_text(message_safe, parse_mode=ParseMode.MARKDOWN_V2)

            # tid_safe = escape_markdown(str(tid), version=2)
            # fn_safe = escape_markdown(fn or "بدون‌نام", version=2)
            # users_lines.append(f"👤 {fn_safe}\n🆔 `{tid_safe}`")
        # message = "لیست کاربران مجاز:\n\n" + "\n\n".join(users_lines)
        # await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Error in admin_view_users: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در دریافت لیست کاربران رخ داد.")

    finally:
        conn.close()

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
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 این بخش فقط برای ادمین است.")
        return MAIN_MENU
    
    try:
        user_id_to_remove = int(update.message.text.split('(')[-1].strip(')'))
    except (ValueError, TypeError, IndexError):
        await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
        return ADMIN_REMOVE_USER

    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("⚠️ خطا در اتصال به پایگاه داده.")
        return await admin_menu(update, context)

    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE telegram_id = %s;", (user_id_to_remove,))
            conn.commit()

            if cur.rowcount > 0:
                msg = f"✅ کاربر {user_id_to_remove} حذف شد."
                msg_safe = escape_markdown(msg, version=2)
                await update.message.reply_text(msg_safe, parse_mode=ParseMode.MARKDOWN_V2)
                # user_id_safe = escape_markdown(str(user_id_to_remove), version=2)
                # await update.message.reply_text(
                    # f"✅ کاربر `{user_id_safe}` حذف شد.",
                    # parse_mode=ParseMode.MARKDOWN_V2
                # )

                logger.info(f"User {user_id_to_remove} removed by admin {update.effective_user.id}")

                try:
                    await context.bot.send_message(
                        chat_id=user_id_to_remove,
                        text="🚫 دسترسی شما به ربات لغو شد."
                    )
                except Exception as e:
                    logger.warning(f"Failed to send access removal message to {user_id_to_remove}: {e}")
            else:
                await update.message.reply_text("⚠️ کاربر مورد نظر یافت نشد.")

    except Exception as e:
        logger.error(f"Error in admin_remove_user: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در حذف کاربر رخ داد.")
    finally:
        conn.close()

    return await admin_menu(update, context)
# --- Admin Add User Confirmation Flow ---

async def admin_add_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 این بخش فقط برای ادمین است.")
        return MAIN_MENU
    
    try:
        user_id_to_add = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("❌ شناسه نامعتبر است. لطفاً یک عدد وارد کنید.")
        return ADMIN_ADD_USER_CONFIRM

    # بررسی اینکه کاربر از قبل وجود نداشته باشد
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("⚠️ خطا در اتصال به پایگاه داده.")
        return ADMIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM users WHERE telegram_id = %s;",
                (user_id_to_add,)
            )
            if cur.fetchone():
                await update.message.reply_text("⚠️ این کاربر از قبل وجود دارد.")
                return await admin_menu(update, context)
    finally:
        conn.close()

    try:
        chat = await context.bot.get_chat(user_id_to_add)

        user_info = {
            'id': str(chat.id), 
            'first_name': chat.first_name or "بدون‌نام",
            'username': f"@{chat.username}" if chat.username else "ندارد"
        }
        context.user_data['user_to_add'] = user_info

        message_raw = (
            f"اطلاعات کاربر:\n"
            f"👤 نام: {user_info['first_name']}\n"
            f"🆔 شناسه: {user_info['id']}\n"
            f"🔖 نام کاربری: {user_info['username']}\n\n"
            "آیا این کاربر را اضافه می‌کنید؟"
        )
        message_safe = escape_markdown(message_raw, version=2)

        keyboard = [[YES_BUTTON , NO_BUTTON], [BACK_BUTTON , HOME_BUTTON ]]
        await update.message.reply_text(
            message_safe,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN_V2
        )
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
            message_raw =  f"✅ کاربر `{user_to_add['id']}` اضافه شد و به او اطلاع داده شد."
            await update.message.reply_text(escape_markdown(message_raw, version=2), parse_mode=ParseMode.MARKDOWN_V2)

        except Exception:
            message_raw = f"✅ کاربر `{user_to_add['id']}` اضافه شد، اما ارسال پیام به او ناموفق بود."
            await update.message.reply_text(escape_markdown(message_raw, version=2), parse_mode=ParseMode.MARKDOWN_V2)

    except psycopg2.Error:
        await update.message.reply_text("❌ خطایی در افزودن کاربر رخ داد.")
    finally:
        conn.close()
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
#---------
# async def view_choose_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # person_name = update.message.text
    # person_id = context.user_data.get('persons_list_dict', {}).get(person_name)
    # if not person_id:
    #     await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
    #     return VIEW_CHOOSE_PERSON
    # context.user_data['selected_person_id'] = person_id
    # context.user_data['selected_person_name'] = person_name
    
    # # accounts = await get_accounts_for_person_from_db(person_id, context)
    # buttons = list(context.user_data['accounts_list_dict'].keys())
    # if not buttons:
    #     await update.message.reply_text(f"هیچ حسابی برای '{person_name}' ثبت نشده.")
    #     return VIEW_CHOOSE_PERSON
    #     # # Re-display person list
    #     # persons = await get_persons_from_db(context)
    #     # buttons = [p[1] for p in persons]
    #     # keyboard = build_menu_paginated(buttons, 2, n_cols=2, footer_buttons=[[HOME_BUTTON]])
    #     # await update.message.reply_text("شخص دیگری را انتخاب کنید:", reply_markup=keyboard)
    #     # return VIEW_CHOOSE_PERSON
    
    
    # keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    # await update.message.reply_text(f"حساب‌های '{person_name}'. کدام حساب؟", reply_markup=keyboard)
    # return VIEW_CHOOSE_ACCOUNT
#---------

# یک تابع کمکی برای گرفتن مدارک از دیتابیس (مثل تابع حساب‌ها)
async def get_documents_for_person_from_db(person_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Fetches all documents for a person and stores them in context."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            # فقط نام و آیدی مدارک رو برای ساختن دکمه‌ها می‌گیریم
            cur.execute("SELECT id, doc_name FROM documents WHERE person_id = %s ORDER BY doc_name;", (person_id,))
            documents = cur.fetchall()
            # دیکشنری مدارک رو در user_data ذخیره می‌کنیم برای استفاده در مرحله بعد
            context.user_data['documents_list_dict'] = {doc[1]: doc[0] for doc in documents}
            return documents
    finally:
        conn.close()

async def view_choose_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    person_name = update.message.text
    # اگر کاربر دکمه "بازگشت" از صفحه مدارک رو زده باشه، دوباره لیست اشخاص رو نشون میدیم
    if person_name == BACK_BUTTON:
        return await view_choose_person(update, context)
        
    person_id = context.user_data.get('persons_list_dict', {}).get(person_name)

    if not person_id:
        # این حالت برای زمانیه که کاربر از صفحه لیست مدارک برگشته و دوباره شخص انتخاب کرده
        if 'selected_person_id' in context.user_data:
            person_id = context.user_data['selected_person_id']
        else:
            await update.message.reply_text("❌ انتخاب نامعتبر. از دکمه‌ها استفاده کنید.")
            return VIEW_CHOOSE_PERSON

    context.user_data['selected_person_id'] = person_id
    context.user_data['selected_person_name'] = person_name

    # گرفتن لیست حساب‌ها و مدارک
    # accounts = await get_accounts_for_person_from_db(person_id, context)
    documents = await get_documents_for_person_from_db(person_id, context)
    account_buttons = list(context.user_data.get('accounts_list_dict', {}).keys())
    buttons = account_buttons.copy()
    # buttons = account_buttons

    # اگر مدرکی وجود داشت، دکمه "مدارک" رو اضافه کن
    if documents:
        buttons.append(DOCUMENTS_BUTTON)

    if not buttons:
        await update.message.reply_text(f"هیچ حساب یا مدرکی برای '{person_name}' ثبت نشده.")
        return await view_choose_person(update, context) # برگشت به لیست اشخاص

    keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text(f"اطلاعات '{person_name}' \nکدام مورد را می‌خواهید مشاهده کنید؟", reply_markup=keyboard)
    
    # اینجا state رو به VIEW_DISPLAY_ACCOUNT_DETAILS تغییر می‌دیم تا منتظر انتخاب کاربر بمونه
    return VIEW_DISPLAY_ACCOUNT_DETAILS

##

async def view_choose_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays a paginated list of documents for the selected person."""
    person_name = context.user_data.get('selected_person_name', 'شخص انتخاب شده')
    
    # دیکشنری مدارک از مرحله قبل در user_data موجوده
    doc_buttons = list(context.user_data.get('documents_list_dict', {}).keys())

    if not doc_buttons:
        await update.message.reply_text("هیچ مدرکی برای این شخص یافت نشد.")
        # برمیگردیم به صفحه انتخاب حساب/مدرک
        return await view_choose_account(update, context)

    keyboard = build_menu_paginated(doc_buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text(f"مدارک '{person_name}'. کدام مدرک؟", reply_markup=keyboard)
    
    return VIEW_CHOOSE_DOCUMENT


async def view_display_document_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fetches and displays the details of a selected document."""
    doc_name = update.message.text
    doc_id = context.user_data.get('documents_list_dict', {}).get(doc_name)

    if not doc_id:
        await update.message.reply_text("❌ انتخاب نامعتبر. لطفاً از دکمه‌ها استفاده کنید.")
        return VIEW_CHOOSE_DOCUMENT

    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("خطا در اتصال به پایگاه داده.")
        return MAIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT doc_name, doc_text, file_ids FROM documents WHERE id = %s;", (doc_id,))
            doc = cur.fetchone()
            if not doc:
                await update.message.reply_text("خطا: مدرک یافت نشد.")
                return await view_choose_document(update, context)

            doc_name, doc_text, file_ids = doc
            
            # ساخت پیام اصلی
            message_raw = f"📄 مدرک: {doc_name}\n\n"
            if doc_text:
                message_raw += f"📝 متن:\n{doc_text}\n"
            
            message_safe = escape_markdown(message_raw, version=2)
            await update.message.reply_text(
                message_safe,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True)
            )

            # ارسال فایل‌ها در صورت وجود
            if file_ids:
                await update.message.reply_text("فایل‌های ضمیمه:")
                for file_id in file_ids:
                    try:
                        # ربات تلگرام خودش نوع فایل رو تشخیص میده، پس با send_document میفرستیم
                        await context.bot.send_document(chat_id=update.effective_chat.id, document=file_id)
                    except Exception as e:
                        logger.error(f"Failed to send file with ID {file_id}: {e}")
                        await update.message.reply_text(f"⚠️ فایل با شناسه `{file_id}` قابل بارگذاری نبود.")

    finally:
        conn.close()

    # در همین state می‌مانیم تا کاربر بتواند مدرک دیگری را ببیند یا به عقب برگردد
    return VIEW_CHOOSE_DOCUMENT


#---------
PERSIAN_TO_ENGLISH_DIGITS = str.maketrans({
    "۰":"0","۱":"1","۲":"2","۳":"3","۴":"4","۵":"5","۶":"6","۷":"7","۸":"8","۹":"9",
    "٠":"0","١":"1","٢":"2","٣":"3","٤":"4","٥":"5","٦":"6","٧":"7","٨":"8","٩":"9"
})
def persian_to_english_digits(s: str) -> str:
    if not isinstance(s, str):
        return s
    return s.translate(PERSIAN_TO_ENGLISH_DIGITS)

##----

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
            
            # Escape text parts for HTML safety
            person_name_safe = escape(str(person_name))
            bank_safe = escape(str(bank or 'N/A'))
            # person_name_safe = escape_markdown(person_name, version=2)
            # bank_safe = escape_markdown(bank or 'N/A', version=2)
            # acc_num_safe = escape_markdown(acc_num, version=2) if acc_num else None
            # card_num_safe = escape_markdown(card_num, version=2) if card_num else None
            # shaba_safe = escape_markdown(shaba, version=2) if shaba else None
            # Prepare numbers in monospace; convert Persian digits to English if needed
            def mono(value):
                if value is None:
                    return None
                s = str(value)
                s = persian_to_english_digits(s)  # optional; keeps Latin digits for easier copy
                return f"<code>{escape(s)}</code>"
            
            acc_num_mono = mono(acc_num)
            card_num_mono = mono(card_num)
            shaba_mono = mono(shaba)

            # Build the HTML message
            msg_lines = []
            msg_lines.append(f"👤 اطلاعات حساب ({person_name_safe})")
            msg_lines.append(f"🏦 {bank_safe}")
            if acc_num_mono:
                msg_lines.append(f"🔢 {acc_num_mono}")
            if card_num_mono:
                msg_lines.append(f"💳 {card_num_mono}")
            if shaba_mono:
                msg_lines.append(f"🌐 {shaba_mono}")

            message_html = "\n".join(msg_lines)

            # message_raw  = f"👤 اطلاعات حساب ({person_name})\n🏦 {bank or 'N/A'}\n"
            # if acc_num:
            #     message_raw += f"🔢 {acc_num}\n"
            # if card_num:
            #     message_raw += f"💳 {card_num}\n"
            # if shaba:
            #     message_raw += f"🌐 {shaba}\n"

            # message_safe = escape_markdown(message_raw, version=2)

            await update.message.reply_text(
                # message_safe, 
                message_html,
                # parse_mode=ParseMode.MARKDOWN_V2, 
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardMarkup([[BACK_BUTTON, HOME_BUTTON]], resize_keyboard=True)
            )
            if photo_id:
                try:
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id, caption="🖼️ تصویر کارت", parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.exception("Failed to send card photo:")
                    await update.message.reply_text("⚠️ تصویر کارت قابل بارگذاری نبود.")
    finally:
        conn.close()
    return VIEW_ACCOUNT_DETAILS  # Stay in the same state to allow viewing another account

async def view_back_to_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the 'Back' button press after viewing account details."""
    person_id = context.user_data.get('selected_person_id')
    person_name = context.user_data.get('selected_person_name', 'شخص')

    if not person_id:
        # Failsafe: if we lost context, go back to person selection
        return await view_choose_person(update, context)

    # Re-display the accounts list for the same person
    accounts = await get_accounts_for_person_from_db(person_id, context)
    buttons = list(context.user_data.get('accounts_list_dict', {}).keys())
    
    if not buttons:
        await update.message.reply_text(f"هیچ حسابی برای '{person_name}' ثبت نشده.")
        return await view_choose_person(update, context)

    keyboard = build_menu_paginated(buttons, 0, n_cols=2, footer_buttons=[[BACK_BUTTON, HOME_BUTTON]])
    await update.message.reply_text(f"حساب‌های '{person_name}'. کدام حساب؟", reply_markup=keyboard)
    
    return VIEW_CHOOSE_ACCOUNT


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
    return ADD_ACCOUNT_BANK

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
    if not person_id:
        return ADD_CHOOSE_EXISTING_PERSON
    context.user_data['selected_person_id'] = person_id
    context.user_data['new_account_person_id'] = person_id
    context.user_data['new_account'] = {}
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
    context.user_data['new_doc']['doc_files'] = []
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
    new_doc = context.user_data.setdefault('new_doc', {})
    # new_doc = context.user_data.get('new_doc', {})
    # انتقال فایل‌ها از doc_files به داخل new_doc
    if 'doc_files' in context.user_data:
        new_doc['files'] = context.user_data['doc_files']
    message = (
        f"آیا این مدرک ثبت شود؟\n\n"
        f"📄 نام: {new_doc.get('name', 'N/A')}\n"
        f"📝 متن: {new_doc.get('text', 'ندارد')}\n"
        f"📎 تعداد فایل: {len(new_doc.get('files', []))}"
    )

    keyboard = [[YES_CONTINUE, NO_EDIT], [HOME_BUTTON]]
    await update.message.reply_text(message, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_DOC_SAVE

async def add_save_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_doc = context.user_data.get('new_doc',  {})
    person_id = context.user_data.get('selected_person_id')

    if not person_id or not new_doc or not new_doc.get('name'):
        await update.message.reply_text("خطای داخلی: اطلاعات مدرک یا شخص کامل نیست. لطفاً دوباره از منوی اصلی شروع کنید.")
        # پاک کردن اطلاعات ناقص برای جلوگیری از خطای مجدد
        context.user_data.pop('new_doc', None)
        context.user_data.pop('doc_files', None)
        context.user_data.pop('selected_person_id', None)
        return await main_menu(update, context)
    
    # person_id = new_doc.get('person_id')
    # doc_nameA = new_doc.get('name')
    # doc_textA = new_doc.get('text')
    # doc_filesA = new_doc.get('files', [])
    
    # if not new_doc or not person_id:
    #     await update.message.reply_text("خطای داخلی، لطفا دوباره تلاش کنید.")
    #     return await edit_menu(update, context)

    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("اتصال به دیتابیس برقرار نیست. لطفاً بعداً تلاش کنید.")
        return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """ INSERT INTO documents (person_id, doc_name, doc_text, file_ids)
                VALUES (%s, %s, %s, %s);""",
                # (person_id, new_doc.get('name'), new_doc.get('text'), new_doc.get('files', []))
                (
                    # context.user_data['selected_person_id'],
                    person_id,
                    new_doc.get('name'),
                    new_doc.get('text'),
                    new_doc.get('files', []) # پاس دادن لیست فایل‌ها
                )
            )
            conn.commit()
        await update.message.reply_text("✅ مدرک جدید با موفقیت ثبت شد.", reply_markup=ReplyKeyboardRemove())
            
    except psycopg2.Error as e:
        logger.error(f"Error saving document: {e}")
        await update.message.reply_text("❌ خطایی در ذخیره مدرک رخ داد.")
        return await edit_menu(update, context)
    finally:
        if conn:
            conn.close()

    # Cleanup
    # پاکسازی نهایی در صورت موفقیت
    context.user_data.pop('new_doc', None)
    context.user_data.pop('selected_person_id', None)
    context.user_data.pop('doc_files', None) # لیست موقت فایل‌ها هم پاک می‌شود
    return await main_menu(update, context)
    
async def add_account_get_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault('new_account', {})['bank_name'] = None if update.message.text == SKIP_BUTTON else update.message.text
    # context.user_data['new_account'] = {}
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
    person_id = context.user_data.get('selected_person_id') or context.user_data.get('new_account_person_id')
    
    if update.message.photo:
        new_account['card_photo_id'] = update.message.photo[-1].file_id
    elif update.message.text == SKIP_BUTTON:
        new_account['card_photo_id'] = None
    else:
        await update.message.reply_text("لطفاً عکس بفرستید یا رد شوید.")
        return ADD_ACCOUNT_PHOTO
    # بررسی می‌کنیم که اطلاعات حیاتی مثل آیدی شخص و نام حساب وجود داشته باشند
    if not person_id or not new_account.get('account_name'):
        # logger.error(f"Missing data for account save. Person ID: {person_id}, New Account: {new_account}")
        await update.message.reply_text("❌ یک خطای داخلی رخ داد (اطلاعات شخص یا نام حساب یافت نشد). لطفاً دوباره تلاش کنید.")
        # پاک کردن اطلاعات ناقص برای جلوگیری از خطای مجدد
        context.user_data.pop('new_account', None)
        return await edit_menu(update, context)
    
    acc_nameA = new_account.get('account_name')
    # bank_name = TEST 
    bank_nameA = new_account.get('bank_name')
    acc_numA = new_account.get('account_number')
    card_numA = new_account.get('card_number')
    shabaA = new_account.get('shaba_number')
    acc_photo_idA = new_account.get('card_photo_id')

    # if not person_id:
        # return await start(update, context)
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("اتصال به دیتابیس برقرار نیست.")
        return await edit_menu(update, context)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (person_id, account_name, bank_name, account_number, card_number, shaba_number, card_photo_id) VALUES (%s, %s, %s, %s, %s, %s, %s);",
                (
                    person_id,
                    acc_nameA,
                    bank_nameA,
                    acc_numA,
                    card_numA,
                    shabaA,
                    acc_photo_idA
                 )
            )
            conn.commit()
            await update.message.reply_text("✅ حساب جدید با موفقیت ثبت شد.")
    except psycopg2.Error as e:
        logger.error(f"Error saving account: {e}")
        await update.message.reply_text("❌ خطایی در ذخیره حساب رخ داد.")
    finally:
        if conn:
            conn.close()

    context.user_data.pop('new_account', None)
    context.user_data.pop('selected_person_id', None)
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
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.Regex(f"^{YES_BUTTON}$"), admin_add_user_execute),
                MessageHandler(filters.Regex(f"^{NO_BUTTON}$"), admin_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_user_confirm)
            ],
            ADMIN_REMOVE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_user),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), admin_menu),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu)
            ],
            VIEW_CHOOSE_PERSON: [
                # MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                # MessageHandler(filters.TEXT & ~filters.COMMAND, view_choose_account)
                MessageHandler(filters.Text([NEXT_PAGE_BUTTON, PREV_PAGE_BUTTON]), view_person_paginator),
                MessageHandler(filters.TEXT & ~filters.COMMAND, view_choose_account),
            ],
            VIEW_CHOOSE_ACCOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, view_display_account_details),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), view_choose_person),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu)
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_save_new_person_and_prompt_item_type),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu)
            ],
            ADD_CHOOSE_EXISTING_PERSON: [
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_set_existing_person_and_prompt_item_type)
            ],
            ADD_ACCOUNT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_prompt_account_name),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_item_type),
            ],
            ADD_ACCOUNT_BANK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_get_bank),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu)
            ],
            ADD_ACCOUNT_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_get_number),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_person_type),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu)
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
                MessageHandler(filters.Regex("^حساب بانکی 💳$"), add_prompt_account_name),
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_doc_text),
                MessageHandler(filters.Regex(f"^{YES_CONTINUE}$"), add_prompt_doc_files),  # هدایت به مرحله ارسال فایل
                MessageHandler(filters.Regex(f"^{NO_EDIT}$"), add_get_doc_text),  # برگشت به مرحله وارد کردن متن
                MessageHandler(filters.Regex(f"^{SKIP_BUTTON}$"), add_get_doc_text),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_get_doc_name),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
            ],
            ADD_DOC_FILES: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, add_get_doc_files),
                # MessageHandler(filters.Regex(f"^{FINISH_SENDING_BUTTON}$"), add_confirm_doc_save),add_save_document
                MessageHandler(filters.Regex(f"^{FINISH_SENDING_BUTTON}$"), add_confirm_doc_save),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_get_doc_text),
                MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
                MessageHandler(filters.Regex(f"^{YES_CONTINUE}$"), add_prompt_doc_files),
                MessageHandler(filters.Regex(f"^{NO_EDIT}$"), add_get_doc_text)
            ],

            ADD_DOC_SAVE: [
                MessageHandler(filters.Regex(f"^{YES_CONTINUE}$"), add_save_document),
                MessageHandler(filters.Regex(f"^{NO_EDIT}$"), add_prompt_doc_name),
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
            
            VIEW_DISPLAY_ACCOUNT_DETAILS: [
                # اگر کاربر دکمه "مدارک" رو زد، به تابع نمایش لیست مدارک میره
                MessageHandler(filters.Regex(f'^{DOCUMENTS_BUTTON}$'), view_choose_document),
                # در غیر این صورت، فرض می‌کنیم یک حساب انتخاب کرده
                MessageHandler(filters.TEXT & ~filters.COMMAND, view_display_account_details),
            ],
             # <<< State جدید برای نمایش لیست مدارک >>>
            VIEW_CHOOSE_DOCUMENT: [
                MessageHandler(filters.Regex(f'^{BACK_BUTTON}$'), view_choose_account), # دکمه بازگشت به صفحه قبلی
                MessageHandler(filters.TEXT & ~filters.COMMAND, view_display_document_details)
            ],
            VIEW_ACCOUNT_DETAILS: [
                MessageHandler(filters.Text([BACK_BUTTON]), view_back_to_accounts),
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
