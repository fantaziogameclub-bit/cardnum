```python
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
    # --- PATCH 1: Added state for account name ---
    ADD_ACCOUNT_NAME,
    ADD_ACCOUNT_BANK, ADD_ACCOUNT_NUMBER, ADD_ACCOUNT_CARD, ADD_ACCOUNT_SHABA, ADD_ACCOUNT_PHOTO,
    ADD_DOC_NAME, ADD_DOC_TEXT, ADD_DOC_FILES, ADD_DOC_SAVE,
    DELETE_CHOOSE_TYPE, DELETE_CHOOSE_PERSON, DELETE_CONFIRM_PERSON,
    DELETE_CHOOSE_ACCOUNT_FOR_PERSON, DELETE_CHOOSE_ACCOUNT, DELETE_CONFIRM_ACCOUNT,
    CHANGE_CHOOSE_PERSON, CHANGE_CHOOSE_TARGET, CHANGE_PROMPT_PERSON_NAME, CHANGE_SAVE_PERSON_NAME,
    CHANGE_CHOOSE_ACCOUNT, CHANGE_CHOOSE_FIELD, CHANGE_PROMPT_FIELD_VALUE, CHANGE_SAVE_FIELD_VALUE,
) = range(35) # Increased range for new state

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
    conn = get_db_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
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
            # --- PATCH 1: Added account_name column ---
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
                    doc_name TEXT NOT NULL,
                    doc_text TEXT,
                    file_ids TEXT[]
                );
            """)
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
    return user_id == ADMIN_TELEGRAM_ID

def build_menu_paginated(buttons: list, page: int, n_cols: int, items_per_page: int = 10, footer_buttons=None):
    start_index = page * items_per_page
    end_index = start_index + items_per_page
    paginated_buttons = buttons[start_index:end_index]
    menu = [paginated_buttons[i:i + n_cols] for i in range(0, len(paginated_buttons), n_cols)]
    pagination_controls = []
    if page > 0: pagination_controls.append(PREV_PAGE_BUTTON)
    if end_index < len(buttons): pagination_controls.append(NEXT_PAGE_BUTTON)
    if pagination_controls: menu.append(pagination_controls)
    if footer_buttons:
        menu.extend(footer_buttons)
        if not any(HOME_BUTTON in row for row in footer_buttons):
            menu.append([HOME_BUTTON])
    else:
        menu.append([HOME_BUTTON])
    return ReplyKeyboardMarkup(menu, resize_keyboard=True)

async def get_persons_from_db(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    if not conn:
        await context.bot.send_message(chat_id=context._chat_id_and_data[0], text="⚠️ خطا در اتصال به پایگاه داده.")
        return MAIN_MENU
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM persons ORDER BY name;")
            persons = cur.fetchall()
            context.user_data['persons_list_tuples'] = persons
            context.user_data['persons_list_dict'] = {p[1]: p[0] for p in persons}
            return persons
    finally:
        conn.close()

# --- PATCH 1: Modified to fetch account_name ---
async def get_accounts_for_person_from_db(person_id: int, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    if not conn: return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, account_name, bank_name FROM accounts WHERE person_id = %s ORDER BY account_name;", (person_id,))
            accounts = cur.fetchall()
            context.user_data['accounts_list_tuples'] = accounts
            # Display format: "Account Name (Bank Name)"
            context.user_data['accounts_list_dict'] = {f"{acc[1]} ({acc[2] or 'N/A'})": acc[0] for acc in accounts}
            return accounts
    finally:
        conn.close()

# --- Start & Main Menu Handlers ---
# --- PATCH 3: Handle new users gracefully ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    auth_status = is_authorized(user.id)

    if auth_status is None:
        await update.message.reply_text("⚠️ خطا در اتصال به پایگاه داده. لطفاً بعداً دوباره تلاش کنید.")
        return ConversationHandler.END

    if not auth_status:
        # Use triple quotes for multi-line strings
        await update.message.reply_text(
            f"""🚫 شما اجازه دسترسی به این ربات را ندارید.
برای درخواست دسترسی، این شناسه را برای ادمین ارسال کنید:
`{user.id}`""",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        # Notify admin about the new user
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

    # If user is authorized, proceed
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

# ... (Admin functions remain the same, so they are omitted for brevity) ...
# You can copy the admin functions from your previous file here.

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
    
# ... (Other ADD ACCOUNT functions follow, slightly modified) ...

# --- PATCH 2: Complete the add_get_doc_files function ---
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
    
# --- PATCH 2: Modify add_save_document to handle file_ids list ---
async def add_save_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("خطا در اتصال به پایگاه داده.")
        return await main_menu(update, context)
    
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (person_id, doc_name, doc_text, file_ids)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    context.user_data['selected_person_id'],
                    context.user_data.get('doc_name'),
                    context.user_data.get('doc_text'),
                    context.user_data.get('doc_files', []) # Pass the list directly
                )
            )
            conn.commit()
        await update.message.reply_text(
            "✅ مدرک با موفقیت ذخیره شد.",
            reply_markup=ReplyKeyboardRemove()
        )
    except psycopg2.Error as e:
        logger.error(f"Error saving document: {e}")
        await update.message.reply_text("⚠️ خطایی در ذخیره مدرک رخ داد.")
    finally:
        conn.close()

    return await main_menu(update, context)


def main() -> None:
    """Run the bot."""
    # Ensure database is set up
    setup_database()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # You will need to fill in the rest of your conversation handler here.
    # The structure will look like this:
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                # ... your handlers for main menu ...
            ],
            # ... all your other states ...
            ADD_ACCOUNT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_account_name),
                MessageHandler(filters.Regex(f"^{BACK_BUTTON}$"), add_choose_item_type),
            ],
            ADD_DOC_FILES: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, add_get_doc_files),
                MessageHandler(filters.Regex(f"^{FINISH_SENDING_BUTTON}$"), add_save_document), # Or a confirmation step
                # ... other handlers ...
            ],

        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(f"^{HOME_BUTTON}$"), main_menu),
        ],
        per_message=False,
    )

    application.add_handler(conv_handler)
    application.run_polling()


if __name__ == "__main__":
    main()
```
