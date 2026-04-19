import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    BotCommand,
    BotCommandScopeDefault,
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

PROFILES_FILE = "user_profiles.json"
DB_FILE = "orders.db"
MIN_ORDER_QTY = 6

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


def load_profiles():
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_profiles(data):
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


user_profiles = load_profiles()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            full_name TEXT,
            username TEXT,
            organization TEXT NOT NULL,
            phone TEXT NOT NULL,
            items_json TEXT NOT NULL,
            total_amount REAL NOT NULL,
            total_qty INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_order_to_db(user_id, full_name, username, organization, phone, items, total_amount, total_qty):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO orders (
            user_id, full_name, username, organization, phone,
            items_json, total_amount, total_qty, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(user_id),
        full_name,
        username,
        organization,
        phone,
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


def update_order_status(order_id, status):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()


def get_report(days=7):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        SELECT organization, status, total_amount, total_qty
        FROM orders
        WHERE created_at >= ?
        ORDER BY created_at DESC
    """, (since_date,))

    rows = cursor.fetchall()
    conn.close()

    total_orders = len(rows)
    paid_orders = sum(1 for r in rows if r[1] == "paid")
    unpaid_orders = sum(1 for r in rows if r[1] == "unpaid")
    new_orders = sum(1 for r in rows if r[1] == "new")
    paid_revenue = sum(r[2] for r in rows if r[1] == "paid")

    org_stats = {}
    for organization, status, amount, qty in rows:
        if organization not in org_stats:
            org_stats[organization] = {
                "orders": 0,
                "qty": 0,
                "paid_orders": 0,
                "unpaid_orders": 0,
                "new_orders": 0,
                "paid_amount": 0,
            }

        org_stats[organization]["orders"] += 1
        org_stats[organization]["qty"] += qty

        if status == "paid":
            org_stats[organization]["paid_orders"] += 1
            org_stats[organization]["paid_amount"] += amount
        elif status == "unpaid":
            org_stats[organization]["unpaid_orders"] += 1
        elif status == "new":
            org_stats[organization]["new_orders"] += 1

    return {
        "total_orders": total_orders,
        "paid_orders": paid_orders,
        "unpaid_orders": unpaid_orders,
        "new_orders": new_orders,
        "paid_revenue": paid_revenue,
        "org_stats": org_stats,
    }


def is_valid_phone(phone: str) -> bool:
    cleaned = phone.strip()
    return bool(re.fullmatch(r"[\d\+\-\(\)\s]{6,20}", cleaned))


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


async def show_main_menu_message(message):
    await message.reply_text("Выберите категорию:", reply_markup=main_menu_keyboard())


async def show_main_menu_callback(query):
    await query.edit_message_text("Выберите категорию:", reply_markup=main_menu_keyboard())


async def set_commands(application):
    await application.bot.set_my_commands(
        commands=[
            BotCommand("start", "Открыть меню"),
        ],
        scope=BotCommandScopeDefault()
    )

    await application.bot.set_my_commands(
        commands=[
            BotCommand("start", "Открыть меню"),
            BotCommand("week", "Отчёт за 7 дней"),
            BotCommand("month", "Отчёт за 30 дней"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_USER_ID)
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    profile = user_profiles.get(user_id)

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
    text = update.message.text.strip()

    if mode == "phone":
        if not is_valid_phone(text):
            await update.message.reply_text(
                "Номер телефона введён некорректно.\nВведите номер ещё раз:"
            )
            return

        context.user_data["phone"] = text
        context.user_data["reg_step"] = "organization"
        await update.message.reply_text("Теперь введите организацию:")
        return

    if mode == "organization":
        if not text:
            await update.message.reply_text("Организация не может быть пустой. Введите организацию:")
            return

        user_profiles[user_id] = {
            "phone": context.user_data.get("phone", ""),
            "organization": text,
        }
        save_profiles(user_profiles)

        context.user_data.pop("reg_step", None)
        context.user_data.pop("phone", None)

        await update.message.reply_text("Регистрация завершена ✅")
        await show_main_menu_message(update.message)
        return

    if mode == "edit_phone":
        if not is_valid_phone(text):
            await update.message.reply_text(
                "Номер телефона введён некорректно.\nВведите номер ещё раз:"
            )
            return

        user_profiles.setdefault(user_id, {})
        user_profiles[user_id]["phone"] = text
        save_profiles(user_profiles)

        context.user_data.pop("reg_step", None)

        await update.message.reply_text("Телефон обновлён ✅")
        await show_main_menu_message(update.message)
        return

    if mode == "edit_organization":
        if not text:
            await update.message.reply_text("Организация не может быть пустой. Введите организацию:")
            return

        user_profiles.setdefault(user_id, {})
        user_profiles[user_id]["organization"] = text
        save_profiles(user_profiles)

        context.user_data.pop("reg_step", None)

        await update.message.reply_text("Организация обновлена ✅")
        await show_main_menu_message(update.message)
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
    profile = user_profiles.get(user_id)
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
        f"Организация: {profile.get('organization', '-')}\n"
        f"Телефон: {profile.get('phone', '-')}\n\n"
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
    profile = user_profiles.get(user_id)

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
        organization=profile.get("organization", "-"),
        phone=profile.get("phone", "-"),
        items=items,
        total_amount=total_amount,
        total_qty=total_qty,
    )

    admin_text = (
        f"НОВЫЙ ЗАКАЗ #{order_id}\n\n"
        f"Пользователь: {full_name}"
        f"{' (@' + username + ')' if username else ''}\n"
        f"Организация: {profile.get('organization', '-')}\n"
        f"Телефон: {profile.get('phone', '-')}\n\n"
        f"{cart_text}\n"
        f"Всего штук: {total_qty}\n"
        f"Статус: NEW"
    )

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_text,
        reply_markup=admin_order_keyboard(order_id)
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
    profile_data = user_profiles.get(user_id)

    if not profile_data:
        await query.edit_message_text(
            "Профиль не найден.\nНажмите /start для регистрации."
        )
        return

    text = (
        "Ваш профиль:\n\n"
        f"Телефон: {profile_data.get('phone', '-')}\n"
        f"Организация: {profile_data.get('organization', '-')}"
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
        update_order_status(order_id, "paid")
        new_status = "PAID"
    else:
        update_order_status(order_id, "unpaid")
        new_status = "UNPAID"

    text = query.message.text
    lines = text.splitlines()

    updated_lines = []
    status_found = False

    for line in lines:
        if line.startswith("Статус:"):
            updated_lines.append(f"Статус: {new_status}")
            status_found = True
        else:
            updated_lines.append(line)

    if not status_found:
        updated_lines.append(f"Статус: {new_status}")

    await query.edit_message_text(
        "\n".join(updated_lines),
        reply_markup=admin_order_keyboard(order_id)
    )


async def report_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return

    report = get_report(days=7)

    text = (
        f"ОТЧЁТ ЗА 7 ДНЕЙ\n\n"
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

    await update.message.reply_text(text)


async def report_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет доступа к этой команде.")
        return

    report = get_report(days=30)

    text = (
        f"ОТЧЁТ ЗА 30 ДНЕЙ\n\n"
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

    await update.message.reply_text(text)


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
    init_db()

    app = ApplicationBuilder().token(TOKEN).post_init(set_commands).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("week", report_week))
    app.add_handler(CommandHandler("month", report_month))

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
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(cancel, pattern=r"^cancel$"))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()