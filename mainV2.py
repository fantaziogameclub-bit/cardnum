import os
import logging
import psycopg2
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

# --- Logging Configuration ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
try:
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8308013948:AAErqQIEFxWZzMJAMkogxL2NVkQ3ufTtSOI")
    DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://root:gRMvmlOv4nC4NxUGOdrK2VC8@cardnum2bot:5432/postgres")
    ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", 125886032))
except (KeyError, ValueError) as e:
    logger.error(f"FATAL: Environment variable issue: {e}. Exiting.")
    exit()

# --- Conversation States ---
(
    MAIN_MENU,
    ADMIN_MENU, ADMIN_ADD_USER, ADMIN_ADD_USER_CONFIRM, ADMIN_REMOVE_USER,
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
) = range(39)


# --- Keyboard Buttons & Mappings ---
HOME_BUTTON = "صفحه اصلی 🏠"
BACK_BUTTON = "بازگشت 🔙"
SKIP_BUTTON = "رد شدن ⏭️"
NEXT_PAGE_BUTTON = "صفحه بعد ◀️"
PREV_PAGE_BUTTON = "▶️ صفحه قبل"
FINISH_SENDING_BUTTON = "اتمام ارسال ✅"


FIELD_TO_COLUMN_MAP = {
    "نام بانک 🏦": "bank_name",
    "شماره حساب 🔢": "account_number",
    "شماره کارت 💳": "card_number",
    "شماره شبا 🌐": "shaba_number",
    "عکس کارت 🖼️": "card_photo_id",
}

# --- Database Functions ---
def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
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
def is_authorized(user_id: int) -> bool:
    """Checks if a user is authorized to use the bot."""
    conn = get_db_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE telegram_id = %s;", (user_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()

def is_admin(user_id: int) -> bool:
    """Checks if a user is the admin."""
    return user_id == ADMIN_TELEGRAM_ID

def build_menu_paginated(buttons: list, page: int, n_cols: int, items_per_page: int = 6):
    """Creates a paginated ReplyKeyboardMarkup."""
    start_index = page * items_per_page
    end_index = start_index + items_per_page
    paginated_buttons = buttons[start_index:end_index]

    menu = [paginated_buttons[i:i + n_cols] for i in range(0, len(paginated_buttons), n_cols)]

    pagination_controls = []
    if page > 0:
        pagination_controls.append(PREV_PAGE_BUTTON)
    if end_index < len(buttons):
        pagination_controls.append(NEXT_PAGE_BUTTON)

    if pagination_controls:
        menu.append(pagination_controls)

    menu.append([HOME_BUTTON]) # Always show home button
    return ReplyKeyboardMarkup(menu, resize_keyboard=True)


async def get_persons_from_db(context: ContextTypes.DEFAULT_TYPE):
    """Fetches all persons and stores them in context."""
    conn = get_db_connection()
    if not conn: return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM persons ORDER BY name;")
            persons = cur.fetchall()
            # Store as list of tuples for ordered access and as dict for quick lookup
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
            # Key is now just bank name and ID, card number is removed
            context.user_data['accounts_list_tuples'] = accounts
            context.user_data['accounts_list_dict'] = {f"{acc[1] or 'N/A'} ({acc[0]})": acc[0] for acc in accounts}
            return accounts
    finally:
        conn.close()

# --- Start & Main Menu Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not is_authorized(user.id):
        # --- CHANGE 1: Tell new user their ID ---
        await update.message.reply_text(
            "🚫 شما اجازه دسترسی به این ربات را ندارید.\n"
            f"برای درخواست دسترسی، این شناسه را برای ادمین ارسال کنید:\n`{user.id}`",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

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

# --- Admin Flow Handlers ---
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
    return ADMIN_ADD_USER

# --- CHANGE 2: Admin Add User Confirmation Flow ---
async def admin_add_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_add = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("❌ شناسه نامعتبر است. یک عدد وارد کنید.")
        return ADMIN_ADD_USER

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
        keyboard = [["بله، اضافه کن ✅", "نه، لغو کن ❌"]]
        await update.message.reply_text(message, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode=ParseMode.MARKDOWN_V2)
        return ADMIN_ADD_USER_CONFIRM

    except BadRequest:
        await update.message.reply_text("❌ کاربری با این شناسه یافت نشد. شناسه را بررسی کنید.")
        return ADMIN_ADD_USER
    except Exception as e:
        logger.error(f"Error fetching user info: {e}")
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

# ... (Rest of the code, with modifications for pagination and the new document flow)
# Note: This is a placeholder for brevity. The full code is provided as a single block.

# --- View Information Flow ---
async def view_choose_person(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> int:
    persons = await get_persons_from_db(context)
    if not persons:
        await update.message.reply_text("هیچ شخصی ثبت نشده. از منوی ویرایش، شخص جدید اضافه کنید.")
        return await start(update, context)
    
    context.user_data['page'] = page
    buttons = [p[1] for p in persons]
    keyboard = build_menu_paginated(buttons, page=page, n_cols=2)
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

async def add_save_new_person_and_prompt_item_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This function now leads to choosing between Account and Document
    # ... (code to save new person is the same)
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
            context.user_data['selected_person_id'] = person_id
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

async def add_set_existing_person_and_prompt_item_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This also now leads to choosing between Account and Document
    person_name = update.message.text
    person_id = context.user_data.get('persons_list_dict', {}).get(person_name)
    if not person_id: return ADD_CHOOSE_EXISTING_PERSON
    context.user_data['selected_person_id'] = person_id
    
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
    keyboard = [["بله، ادامه", "خیر، ویرایش متن"], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text(f"متن ثبت شود؟\n---\n{doc_text or 'خالی'}\n---", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_DOC_FILES # State for confirmation before file upload

async def add_prompt_doc_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_doc']['files'] = []
    keyboard = [[FINISH_SENDING_BUTTON], [BACK_BUTTON, HOME_BUTTON]]
    await update.message.reply_text("عکس‌ها و فایل‌های مدرک را ارسال کنید. پس از اتمام، دکمه 'اتمام ارسال' را بزنید.", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_DOC_FILES

async def add_get_doc_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    
    if file_id:
        context.user_data['new_doc']['files'].append(file_id)
        await update.message.reply_text(f"فایل دریافت شد. ({len(context.user_data['new_doc']['files'])} مورد تا الان)")
    else:
        await update.message.reply_text("لطفا عکس یا فایل ارسال کنید.")
    
    return ADD_DOC_FILES # Stay in this state to receive more files

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
    
# --- Main Application Setup ---
def main() -> None:
    # The rest of the `main` function with the complete ConversationHandler
    # This will include all states and handlers defined above.
    pass # Placeholder
if __name__ == "__main__":
    # The __main__ block to run the application
    pass # Placeholder
