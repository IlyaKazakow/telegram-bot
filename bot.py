import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, date

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "8633256261:AAHBNFW5BzGsLLAHHRhy4I1HJJixD5759cM"
ADMIN_CHAT_ID = 80263589
ADMIN_USER_ID = 80263589

DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_FILE = os.path.join(DATA_DIR, "bot.db")
MIN_ORDER_QTY = 6

CANONICAL_ORGANIZATIONS = [
    "Севен роадс",
    "Гибрид",
    "Ред дор",
    "Сей ес",
]

MENU = {
    "Мак н чиз": {
        "Курица": 5,
        "Бекон": 7,
        "Рваная свинина": 7,
        "Чили конкарнэ": 11,
        "С сыром": 5,
    },
    "Сырники": {
        "Со сметаной": 5,
        "Черника": 6,
        "Малина": 6,
    },
    "Супы": {
        "Сырный": 6,
        "Бульон куриный": 5,
        "Борщ": 6,
    },
}

user_cart_store = {}


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def get_connection():
    ensure_data_dir()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if phone.startswith("+"):
        return "+" + re.sub(r"\D", "", phone[1:])
    return re.sub(r"\D", "", phone)


def is_valid_phone(phone: str) -> bool:
    normalized = normalize_phone(phone)
    digits_only = normalized[1:] if normalized.startswith("+") else normalized
    return 6 <= len(digits_only) <= 20


def normalize_org_text(text: str) -> str:
    value = text.strip().lower()
    value = value.replace("ё", "е")
    value = value.replace("-", " ")
    value = re.sub(r"[\"'`.,;:!?(){}\[\]/\\]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def get_total_qty(items):
    return sum(item["qty"] for item in items)


def format_cart(items):
    if not items:
        return "Корзина пуста", 0

    lines = []
    total = 0

    for item in items:
        subtotal = item["qty"] * item["price"]
        total += subtotal
        lines.append(f'{item["item"]} x{item["qty"]} = {subtotal}₾')

    text = "\n".join(lines) + f"\n\nИТОГО: {total}₾"
    return text, total


def main_menu_keyboard():
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in MENU.keys()]
    keyboard.append([InlineKeyboardButton("🛒 Корзина", callback_data="cart")])
    keyboard.append([InlineKeyboardButton("⚙️ Профиль", callback_data="profile")])
    return InlineKeyboardMarkup(keyboard)


def profile_keyboard():
    keyboard = [
        [InlineKeyboardButton("📞 Изменить телефон", callback_data="edit_phone")],
        [InlineKeyboardButton("🏢 Изменить организацию", callback_data="edit_organization")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_order_keyboard(order_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Оплачен", callback_data=f"mark_paid:{order_id}"),
            InlineKeyboardButton("❌ Не оплачен", callback_data=f"mark_unpaid:{order_id}"),
        ]
    ])


def admin_profile_confirm_keyboard(user_id):
    rows = []
    for org in CANONICAL_ORGANIZATIONS:
        rows.append([InlineKeyboardButton(org, callback_data=f"confirm_profile_org:{user_id}:{org}")])
    rows.append([InlineKeyboardButton("⏳ Оставить без подтверждения", callback_data=f"keep_profile_pending:{user_id}")])
    return InlineKeyboardMarkup(rows)


def admin_order_confirm_keyboard(order_id):
    rows = []
    for org in CANONICAL_ORGANIZATIONS:
        rows.append([InlineKeyboardButton(org, callback_data=f"confirm_order_org:{order_id}:{org}")])
    rows.append([InlineKeyboardButton("⏳ Оставить без подтверждения", callback_data=f"keep_order_pending:{order_id}")])
    return InlineKeyboardMarkup(rows)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            user_id TEXT PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            phone_original TEXT NOT NULL,
            phone_normalized TEXT NOT NULL,
            organization_original TEXT NOT NULL,
            organization_canonical TEXT,
            organization_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            full_name TEXT,
            username TEXT,
            phone_original TEXT NOT NULL,
            phone_normalized TEXT NOT NULL,
            organization_original TEXT NOT NULL,
            organization_canonical TEXT,
            organization_status TEXT NOT NULL DEFAULT 'pending',
            items_json TEXT NOT NULL,
            total_amount REAL NOT NULL,
            total_qty INTEGER NOT NULL,
            payment_status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_profile(user_id, full_name, username, phone_original, organization_original):
    conn = get_connection()
    cursor = conn.cursor()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    phone_normalized = normalize_phone(phone_original)

    existing = get_profile(user_id)

    if existing:
        organization_canonical = existing["organization_canonical"]
        organization_status = existing["organization_status"]
    else:
        organization_canonical = None
        organization_status = "pending"

    cursor.execute("""
        INSERT INTO profiles (
            user_id, full_name, username,
            phone_original, phone_normalized,
            organization_original, organization_canonical, organization_status,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            full_name = excluded.full_name,
            username = excluded.username,
            phone_original = excluded.phone_original,
            phone_normalized = excluded.phone_normalized,
            organization_original = excluded.organization_original,
            updated_at = excluded.updated_at
    """, (
        str(user_id),
        full_name,
        username,
        phone_original,
        phone_normalized,
        organization_original,
        organization_canonical,
        organization_status,
        existing["created_at"] if existing else now,
        now,
    ))

    conn.commit()
    conn.close()


def get_profile(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM profiles WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_duplicate_profiles_by_phone(phone_normalized, exclude_user_id=None):
    conn = get_connection()
    cursor = conn.cursor()

    if exclude_user_id:
        cursor.execute("""
            SELECT * FROM profiles
            WHERE phone_normalized = ? AND user_id != ?
            ORDER BY created_at DESC
        """, (phone_normalized, str(exclude_user_id)))
    else:
        cursor.execute("""
            SELECT * FROM profiles
            WHERE phone_normalized = ?
            ORDER BY created_at DESC
        """, (phone_normalized,))

    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_profile_canonical_org(user_id, canonical_org):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE profiles
        SET organization_canonical = ?, organization_status = 'confirmed', updated_at = ?
        WHERE user_id = ?
    """, (
        canonical_org,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        str(user_id),
    ))

    conn.commit()
    conn.close()


def save_order_to_db(
    user_id,
    full_name,
    username,
    phone_original,
    phone_normalized,
    organization_original,
    organization_canonical,
    organization_status,
    items,
    total_amount,
    total_qty,
):
    conn = get_connection()
    cursor = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO orders (
            user_id, full_name, username,
            phone_original, phone_normalized,
            organization_original, organization_canonical, organization_status,
            items_json, total_amount, total_qty, payment_status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(user_id),
        full_name,
        username,
        phone_original,
        phone_normalized,
        organization_original,
        organization_canonical,
        organization_status,
        json.dumps(items, ensure_ascii=False),
        total_amount,
        total_qty,
        "new",
        created_at,
    ))

    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id


def set_order_canonical_org(order_id, canonical_org):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE orders
        SET organization_canonical = ?, organization_status = 'confirmed'
        WHERE id = ?
    """, (canonical_org, int(order_id)))

    conn.commit()
    conn.close()


def update_order_payment_status(order_id, status):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET payment_status = ? WHERE id = ?", (status, int(order_id)))
    conn.commit()
    conn.close()


def get_profiles(limit=500):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM profiles
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_profiles(limit=200):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM profiles
        WHERE organization_status = 'pending'
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_period_range_last_month():
    today = date.today()
    first_day_current_month = date(today.year, today.month, 1)
    last_day_previous_month = first_day_current_month - timedelta(days=1)
    first_day_previous_month = date(last_day_previous_month.year, last_day_previous_month.month, 1)

    start_dt = datetime.combine(first_day_previous_month, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
    end_dt = datetime.combine(first_day_current_month, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")

    return start_dt, end_dt, first_day_previous_month, last_day_previous_month


def get_report_by_range(start_date_str, end_date_str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT organization_canonical, organization_status, payment_status, total_amount, total_qty
        FROM orders
        WHERE created_at >= ? AND created_at < ?
        ORDER BY created_at DESC
    """, (start_date_str, end_date_str))

    rows = cursor.fetchall()
    conn.close()

    total_orders = len(rows)
    paid_orders = sum(1 for r in rows if r["payment_status"] == "paid")
    unpaid_orders = sum(1 for r in rows if r["payment_status"] == "unpaid")
    new_orders = sum(1 for r in rows if r["payment_status"] == "new")
    paid_revenue = sum(r["total_amount"] for r in rows if r["payment_status"] == "paid")

    org_stats = {}
    for r in rows:
        org = r["organization_canonical"] if r["organization_canonical"] else "⏳ Неподтверждённые"
        if org not in org_stats:
            org_stats[org] = {
                "orders": 0,
                "qty": 0,
                "paid_orders": 0,
                "unpaid_orders": 0,
                "new_orders": 0,
                "paid_amount": 0,
            }

        org_stats[org]["orders"] += 1
        org_stats[org]["qty"] += r["total_qty"]

        if r["payment_status"] == "paid":
            org_stats[org]["paid_orders"] += 1
            org_stats[org]["paid_amount"] += r["total_amount"]
        elif r["payment_status"] == "unpaid":
            org_stats[org]["unpaid_orders"] += 1
        elif r["payment_status"] == "new":
            org_stats[org]["new_orders"] += 1

    return {
        "total_orders": total_orders,
        "paid_orders": paid_orders,
        "unpaid_orders": unpaid_orders,
        "new_orders": new_orders,
        "paid_revenue": paid_revenue,
        "org_stats": org_stats,
    }


def get_report_last_n_days(days=7):
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    return get_report_by_range(
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S"),
    )


async def show_main_menu_message(message):
    await message.reply_text("Выберите категорию:", reply_markup=main_menu_keyboard())


async def show_main_menu_callback(query):
    await query.edit_message_text("Выберите категорию:", reply_markup=main_menu_keyboard())


async def set_commands(application):
    await application.bot.set_my_commands(
        commands=[
            BotCommand("start", "Открыть меню"),
        ],
        scope=BotCommandScopeAllPrivateChats()
    )

    await application.bot.set_my_commands(
        commands=[
            BotCommand("start", "Открыть меню"),
            BotCommand("week", "Отчёт за 7 дней"),
            BotCommand("month", "Отчёт за 30 дней"),
            BotCommand("last_month", "Отчёт за прошлый месяц"),
            BotCommand("profiles", "Все профили"),
            BotCommand("pending_profiles", "Профили без подтверждения"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_USER_ID)
    )


async def notify_admin_about_profile(profile, duplicates, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "НОВЫЙ / ОБНОВЛЁННЫЙ ПРОФИЛЬ\n\n"
        f"Пользователь: {profile.get('full_name') or '-'}"
        f"{' (@' + profile.get('username') + ')' if profile.get('username') else ''}\n"
        f"User ID: {profile['user_id']}\n"
        f"Телефон: {profile['phone_original']}\n"
        f"Телефон normalized: {profile['phone_normalized']}\n"
        f"Организация original: {profile['organization_original']}\n"
        f"Организация canonical: {profile['organization_canonical'] or '-'}\n"
        f"Статус организации: {profile['organization_status']}\n"
    )

    if duplicates:
        text += "\n⚠️ Найдены дубли по номеру:\n"
        for d in duplicates[:10]:
            text += (
                f"- {d.get('full_name') or '-'}"
                f"{' (@' + d.get('username') + ')' if d.get('username') else ''}, "
                f"user_id={d['user_id']}, org={d['organization_original']}\n"
            )

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=text,
        reply_markup=admin_profile_confirm_keyboard(profile["user_id"])
    )


async def notify_admin_about_pending_order(order_id, full_name, username, phone_original, phone_normalized, organization_original, context):
    text = (
        f"ЗАКАЗ #{order_id} ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ ОРГАНИЗАЦИИ\n\n"
        f"Пользователь: {full_name}"
        f"{' (@' + username + ')' if username else ''}\n"
        f"Телефон: {phone_original}\n"
        f"Телефон normalized: {phone_normalized}\n"
        f"Организация original: {organization_original}\n"
        f"Статус организации: pending"
    )

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=text,
        reply_markup=admin_order_confirm_keyboard(order_id)
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    profile = get_profile(user_id)

    if not profile:
        context.user_data["reg_step"] = "phone"
        await update.message.reply_text(
            "Добро пожаловать.\n"
            "Для регистрации введите номер телефона:"
        )
        return

    await show_main_menu_message(update.message)


async def registration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("reg_step")
    if not mode:
        return

    user_id = str(update.effective_user.id)
    full_name = update.effective_user.full_name
    username = update.effective_user.username or ""
    text = update.message.text.strip()

    if mode == "phone":
        if not is_valid_phone(text):
            await update.message.reply_text(
                "Номер телефона введён некорректно.\nВведите номер ещё раз:"
            )
            return

        context.user_data["phone_original"] = text
        context.user_data["reg_step"] = "organization"
        await update.message.reply_text("Теперь введите организацию:")
        return

    if mode == "organization":
        if not text:
            await update.message.reply_text("Организация не может быть пустой. Введите организацию:")
            return

        save_profile(
            user_id=user_id,
            full_name=full_name,
            username=username,
            phone_original=context.user_data.get("phone_original", ""),
            organization_original=text,
        )

        profile = get_profile(user_id)
        duplicates = get_duplicate_profiles_by_phone(profile["phone_normalized"], exclude_user_id=user_id)

        context.user_data.pop("reg_step", None)
        context.user_data.pop("phone_original", None)

        await update.message.reply_text("Регистрация завершена ✅")
        await show_main_menu_message(update.message)

        await notify_admin_about_profile(profile, duplicates, context)
        return

    if mode == "edit_phone":
        if not is_valid_phone(text):
            await update.message.reply_text(
                "Номер телефона введён некорректно.\nВведите номер ещё раз:"
            )
            return

        current_profile = get_profile(user_id)
        organization_original = current_profile["organization_original"] if current_profile else "-"

        save_profile(
            user_id=user_id,
            full_name=full_name,
            username=username,
            phone_original=text,
            organization_original=organization_original,
        )

        profile = get_profile(user_id)
        duplicates = get_duplicate_profiles_by_phone(profile["phone_normalized"], exclude_user_id=user_id)

        context.user_data.pop("reg_step", None)

        await update.message.reply_text("Телефон обновлён ✅")
        await show_main_menu_message(update.message)

        await notify_admin_about_profile(profile, duplicates, context)
        return

    if mode == "edit_organization":
        if not text:
            await update.message.reply_text("Организация не может быть пустой. Введите организацию:")
            return

        current_profile = get_profile(user_id)
        phone_original = current_profile["phone_original"] if current_profile else "-"

        save_profile(
            user_id=user_id,
            full_name=full_name,
            username=username,
            phone_original=phone_original,
            organization_original=text,
        )

        profile = get_profile(user_id)
        duplicates = get_duplicate_profiles_by_phone(profile["phone_normalized"], exclude_user_id=user_id)

        context.user_data.pop("reg_step", None)

        await update.message.reply_text("Организация обновлена ✅")
        await show_main_menu_message(update.message)

        await notify_admin_about_profile(profile, duplicates, context)
        return


async def category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat = query.data.split(":", 1)[1]

    keyboard = [
        [InlineKeyboardButton(f"{item} — {price}₾", callback_data=f"item:{cat}:{item}")]
        for item, price in MENU[cat].items()
    ]
    keyboard.append([InlineKeyboardButton("🛒 Корзина", callback_data="cart")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])

    await query.edit_message_text(cat, reply_markup=InlineKeyboardMarkup(keyboard))


async def item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, cat, item_name = query.data.split(":", 2)
    price = MENU[cat][item_name]

    quantity_options = [3, 6, 12]

    keyboard = [
        [InlineKeyboardButton(f"{qty} шт", callback_data=f"add:{cat}:{item_name}:{qty}")]
        for qty in quantity_options
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"cat:{cat}")])

    await query.edit_message_text(
        f"{item_name} — {price}₾\nВыбери количество:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, cat, item_name, qty = query.data.split(":", 3)

    qty = int(qty)
    price = MENU[cat][item_name]
    user_id = str(query.from_user.id)

    user_cart_store.setdefault(user_id, [])
    user_cart_store[user_id].append({
        "item": item_name,
        "qty": qty,
        "price": price,
    })

    keyboard = [
        [InlineKeyboardButton("➕ Добавить ещё", callback_data=f"cat:{cat}")],
        [InlineKeyboardButton("🛒 Корзина", callback_data="cart")],
        [InlineKeyboardButton("🏠 В меню", callback_data="back_main")],
    ]

    await query.edit_message_text(
        f"Добавлено: {item_name} x{qty}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    items = user_cart_store.get(user_id, [])

    text, total = format_cart(items)
    total_qty = get_total_qty(items)

    if items:
        text += f"\nВсего штук: {total_qty}"
        if total_qty < MIN_ORDER_QTY:
            text += f"\n\n⚠️ Минимальный заказ — от {MIN_ORDER_QTY} шт суммарно по корзине."

    if total > 0:
        keyboard = [
            [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")],
            [InlineKeyboardButton("❌ Очистить", callback_data="clear")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🏠 В меню", callback_data="back_main")],
        ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    profile = get_profile(user_id)
    items = user_cart_store.get(user_id, [])

    if not profile:
        await query.edit_message_text(
            "Профиль не найден. Нажмите /start и пройдите регистрацию заново."
        )
        return

    if not items:
        await query.edit_message_text(
            "Корзина пуста.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 В меню", callback_data="back_main")]
            ])
        )
        return

    total_qty = get_total_qty(items)
    if total_qty < MIN_ORDER_QTY:
        await query.edit_message_text(
            f"Минимальный заказ — от {MIN_ORDER_QTY} шт суммарно по корзине.\n"
            f"Сейчас в корзине: {total_qty} шт.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Вернуться в корзину", callback_data="cart")],
                [InlineKeyboardButton("🏠 В меню", callback_data="back_main")]
            ])
        )
        return

    cart_text, _ = format_cart(items)

    text = (
        f"Подтверждение заказа\n\n"
        f"Организация: {profile.get('organization_original', '-')}\n"
        f"Телефон: {profile.get('phone_original', '-')}\n\n"
        f"{cart_text}\n"
        f"Всего штук: {total_qty}"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="final")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    items = user_cart_store.get(user_id, [])
    profile = get_profile(user_id)

    if not profile:
        await query.edit_message_text("Профиль не найден. Нажмите /start и зарегистрируйтесь снова.")
        return

    if not items:
        await query.edit_message_text(
            "Корзина пуста.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 В меню", callback_data="back_main")]
            ])
        )
        return

    total_qty = get_total_qty(items)
    if total_qty < MIN_ORDER_QTY:
        await query.edit_message_text(
            f"Минимальный заказ — от {MIN_ORDER_QTY} шт суммарно по корзине.\n"
            f"Сейчас в корзине: {total_qty} шт.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Вернуться в корзину", callback_data="cart")],
                [InlineKeyboardButton("🏠 В меню", callback_data="back_main")]
            ])
        )
        return

    cart_text, total_amount = format_cart(items)

    username = query.from_user.username or ""
    full_name = query.from_user.full_name

    order_id = save_order_to_db(
        user_id=user_id,
        full_name=full_name,
        username=username,
        phone_original=profile["phone_original"],
        phone_normalized=profile["phone_normalized"],
        organization_original=profile["organization_original"],
        organization_canonical=profile["organization_canonical"],
        organization_status=profile["organization_status"],
        items=items,
        total_amount=total_amount,
        total_qty=total_qty,
    )

    admin_text = (
        f"НОВЫЙ ЗАКАЗ #{order_id}\n\n"
        f"Пользователь: {full_name}"
        f"{' (@' + username + ')' if username else ''}\n"
        f"Телефон: {profile['phone_original']}\n"
        f"Телефон normalized: {profile['phone_normalized']}\n"
        f"Организация original: {profile['organization_original']}\n"
        f"Организация canonical: {profile['organization_canonical'] or '-'}\n"
        f"Статус организации: {profile['organization_status']}\n\n"
        f"{cart_text}\n"
        f"Всего штук: {total_qty}\n"
        f"Статус оплаты: NEW"
    )

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_text,
        reply_markup=admin_order_keyboard(order_id)
    )

    if profile["organization_status"] != "confirmed":
        await notify_admin_about_pending_order(
            order_id=order_id,
            full_name=full_name,
            username=username,
            phone_original=profile["phone_original"],
            phone_normalized=profile["phone_normalized"],
            organization_original=profile["organization_original"],
            context=context
        )

    user_cart_store[user_id] = []

    await query.edit_message_text(
        f"Заказ отправлен ✅\nНомер заказа: #{order_id}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 В меню", callback_data="back_main")]
        ])
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    user_cart_store[user_id] = []

    await query.edit_message_text(
        "Корзина очищена ✅",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 В меню", callback_data="back_main")],
            [InlineKeyboardButton("⚙️ Профиль", callback_data="profile")],
        ])
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    profile_data = get_profile(user_id)

    if not profile_data:
        await query.edit_message_text(
            "Профиль не найден.\nНажмите /start для регистрации."
        )
        return

    text = (
        "Ваш профиль:\n\n"
        f"Телефон: {profile_data.get('phone_original', '-')}\n"
        f"Организация: {profile_data.get('organization_original', '-')}\n"
        f"Статус организации: {profile_data.get('organization_status', '-')}\n"
        f"Подтверждённая организация: {profile_data.get('organization_canonical') or '-'}"
    )

    await query.edit_message_text(text, reply_markup=profile_keyboard())


async def edit_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["reg_step"] = "edit_phone"
    await query.edit_message_text("Введите новый номер телефона:")


async def edit_organization(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["reg_step"] = "edit_organization"
    await query.edit_message_text("Введите новую организацию:")


async def mark_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_USER_ID:
        await query.answer("Недостаточно прав", show_alert=True)
        return

    await query.answer()

    action, order_id = query.data.split(":")
    order_id = int(order_id)

    if action == "mark_paid":
        update_order_payment_status(order_id, "paid")
        new_status = "PAID"
    else:
        update_order_payment_status(order_id, "unpaid")
        new_status = "UNPAID"

    text = query.message.text
    lines = text.splitlines()

    updated_lines = []
    status_found = False

    for line in lines:
        if line.startswith("Статус оплаты:"):
            updated_lines.append(f"Статус оплаты: {new_status}")
            status_found = True
        else:
            updated_lines.append(line)

    if not status_found:
        updated_lines.append(f"Статус оплаты: {new_status}")

    await query.edit_message_text(
        "\n".join(updated_lines),
        reply_markup=admin_order_keyboard(order_id)
    )


async def confirm_profile_org(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_USER_ID:
        await query.answer("Недостаточно прав", show_alert=True)
        return

    await query.answer()

    _, user_id, canonical_org = query.data.split(":", 2)
    set_profile_canonical_org(user_id, canonical_org)

    profile = get_profile(user_id)
    text = (
        "ПРОФИЛЬ ПОДТВЕРЖДЁН\n\n"
        f"Пользователь: {profile.get('full_name') or '-'}"
        f"{' (@' + profile.get('username') + ')' if profile.get('username') else ''}\n"
        f"User ID: {profile['user_id']}\n"
        f"Телефон: {profile['phone_original']}\n"
        f"Телефон normalized: {profile['phone_normalized']}\n"
        f"Организация original: {profile['organization_original']}\n"
        f"Организация canonical: {profile['organization_canonical'] or '-'}\n"
        f"Статус организации: {profile['organization_status']}\n"
    )
    await query.edit_message_text(text)


async def keep_profile_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_USER_ID:
        await query.answer("Недостаточно прав", show_alert=True)
        return

    await query.answer("Оставлено без подтверждения")


async def confirm_order_org(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_USER_ID:
        await query.answer("Недостаточно прав", show_alert=True)
        return

    await query.answer()

    _, order_id, canonical_org = query.data.split(":", 2)
    set_order_canonical_org(order_id, canonical_org)

    text = query.message.text
    lines = text.splitlines()

    updated_lines = []
    canonical_replaced = False
    status_replaced = False

    for line in lines:
        if line.startswith("Организация canonical:"):
            updated_lines.append(f"Организация canonical: {canonical_org}")
            canonical_replaced = True
        elif line.startswith("Статус организации:"):
            updated_lines.append("Статус организации: confirmed")
            status_replaced = True
        else:
            updated_lines.append(line)

    if not canonical_replaced:
        updated_lines.append(f"Организация canonical: {canonical_org}")
    if not status_replaced:
        updated_lines.append("Статус организации: confirmed")

    await query.edit_message_text("\n".join(updated_lines))


async def keep_order_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_USER_ID:
        await query.answer("Недостаточно прав", show_alert=True)
        return

    await query.answer("Оставлено без подтверждения")


def format_report_text(title, report):
    text = (
        f"{title}\n\n"
        f"Всего заказов: {report['total_orders']}\n"
        f"Новых: {report['new_orders']}\n"
        f"Оплачено: {report['paid_orders']}\n"
        f"Не оплачено: {report['unpaid_orders']}\n"
        f"Оплаченная выручка: {report['paid_revenue']}₾\n\n"
        f"ПО ОРГАНИЗАЦИЯМ:\n"
    )

    if not report["org_stats"]:
        text += "\nНет заказов за этот период."
    else:
        for org, data in report["org_stats"].items():
            text += (
                f"\n— {org}\n"
                f"Заказов: {data['orders']}\n"
                f"Штук: {data['qty']}\n"
                f"Новых: {data['new_orders']}\n"
                f"Оплачено: {data['paid_orders']}\n"
                f"Не оплачено: {data['unpaid_orders']}\n"
                f"Оплаченная сумма: {data['paid_amount']}₾\n"
            )

    return text


async def report_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return

    report = get_report_last_n_days(days=7)
    await update.message.reply_text(format_report_text("ОТЧЁТ ЗА 7 ДНЕЙ", report))


async def report_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return

    report = get_report_last_n_days(days=30)
    await update.message.reply_text(format_report_text("ОТЧЁТ ЗА 30 ДНЕЙ", report))


async def report_last_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return

    start_str, end_str, first_day_prev, last_day_prev = get_period_range_last_month()
    report = get_report_by_range(start_str, end_str)
    title = f"ОТЧЁТ ЗА ПРОШЛЫЙ МЕСЯЦ ({first_day_prev.strftime('%d.%m.%Y')} - {last_day_prev.strftime('%d.%m.%Y')})"
    await update.message.reply_text(format_report_text(title, report))


async def profiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return

    profiles = get_profiles()

    if not profiles:
        await update.message.reply_text("Профилей пока нет.")
        return

    chunks = []
    current = "ЗАРЕГИСТРИРОВАННЫЕ ПРОФИЛИ\n\n"

    for i, p in enumerate(profiles, start=1):
        block = (
            f"{i}) {p.get('full_name') or '-'}"
            f"{' (@' + p.get('username') + ')' if p.get('username') else ''}\n"
            f"User ID: {p['user_id']}\n"
            f"Телефон: {p['phone_original']}\n"
            f"Телефон normalized: {p['phone_normalized']}\n"
            f"Организация original: {p['organization_original']}\n"
            f"Организация canonical: {p['organization_canonical'] or '-'}\n"
            f"Статус организации: {p['organization_status']}\n"
            f"Создан: {p['created_at']}\n\n"
        )

        if len(current) + len(block) > 3500:
            chunks.append(current)
            current = block
        else:
            current += block

    if current:
        chunks.append(current)

    for chunk in chunks:
        await update.message.reply_text(chunk)


async def pending_profiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return

    profiles = get_pending_profiles()

    if not profiles:
        await update.message.reply_text("Нет профилей с неподтверждённой организацией.")
        return

    chunks = []
    current = "ПРОФИЛИ БЕЗ ПОДТВЕРЖДЕНИЯ\n\n"

    for i, p in enumerate(profiles, start=1):
        block = (
            f"{i}) {p.get('full_name') or '-'}"
            f"{' (@' + p.get('username') + ')' if p.get('username') else ''}\n"
            f"User ID: {p['user_id']}\n"
            f"Телефон: {p['phone_original']}\n"
            f"Телефон normalized: {p['phone_normalized']}\n"
            f"Организация original: {p['organization_original']}\n"
            f"Организация canonical: {p['organization_canonical'] or '-'}\n"
            f"Статус организации: {p['organization_status']}\n"
            f"Создан: {p['created_at']}\n\n"
        )

        if len(current) + len(block) > 3500:
            chunks.append(current)
            current = block
        else:
            current += block

    if current:
        chunks.append(current)

    for chunk in chunks:
        await update.message.reply_text(chunk)


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_main_menu_callback(query)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Отменено",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 В меню", callback_data="back_main")]
        ])
    )


def main():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    init_db()

    app = ApplicationBuilder().token(TOKEN).post_init(set_commands).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("week", report_week))
    app.add_handler(CommandHandler("month", report_month))
    app.add_handler(CommandHandler("last_month", report_last_month))
    app.add_handler(CommandHandler("profiles", profiles_command))
    app.add_handler(CommandHandler("pending_profiles", pending_profiles_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration_handler))

    app.add_handler(CallbackQueryHandler(category, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(item, pattern=r"^item:"))
    app.add_handler(CallbackQueryHandler(add, pattern=r"^add:"))
    app.add_handler(CallbackQueryHandler(cart, pattern=r"^cart$"))
    app.add_handler(CallbackQueryHandler(checkout, pattern=r"^checkout$"))
    app.add_handler(CallbackQueryHandler(final, pattern=r"^final$"))
    app.add_handler(CallbackQueryHandler(clear, pattern=r"^clear$"))
    app.add_handler(CallbackQueryHandler(profile, pattern=r"^profile$"))
    app.add_handler(CallbackQueryHandler(edit_phone, pattern=r"^edit_phone$"))
    app.add_handler(CallbackQueryHandler(edit_organization, pattern=r"^edit_organization$"))
    app.add_handler(CallbackQueryHandler(mark_order_status, pattern=r"^(mark_paid|mark_unpaid):"))
    app.add_handler(CallbackQueryHandler(confirm_profile_org, pattern=r"^confirm_profile_org:"))
    app.add_handler(CallbackQueryHandler(keep_profile_pending, pattern=r"^keep_profile_pending:"))
    app.add_handler(CallbackQueryHandler(confirm_order_org, pattern=r"^confirm_order_org:"))
    app.add_handler(CallbackQueryHandler(keep_order_pending, pattern=r"^keep_order_pending:"))
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(cancel, pattern=r"^cancel$"))

    print(f"Bot DB path: {DB_FILE}")
    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()