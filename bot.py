import json
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "8633256261:AAHBNFW5BzGsLLAHHRhy4I1HJJixD5759cM"
ADMIN_CHAT_ID = 80263589  # @Alexandr_en

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

PROFILES_FILE = "user_profiles.json"

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


def is_valid_phone(phone: str) -> bool:
    cleaned = phone.strip()
    return bool(re.fullmatch(r"[\d\+\-\(\)\s]{6,20}", cleaned))


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


async def show_main_menu_message(message):
    await message.reply_text("Выберите категорию:", reply_markup=main_menu_keyboard())


async def show_main_menu_callback(query):
    await query.edit_message_text("Выберите категорию:", reply_markup=main_menu_keyboard())


async def set_commands(application):
    commands = [
        BotCommand("start", "Открыть меню"),
    ]
    await application.bot.set_my_commands(commands)


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

    cart_text, _ = format_cart(items)

    text = (
        f"Подтверждение заказа\n\n"
        f"Организация: {profile.get('organization', '-')}\n"
        f"Телефон: {profile.get('phone', '-')}\n\n"
        f"{cart_text}"
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

    cart_text, _ = format_cart(items)

    username = query.from_user.username
    full_name = query.from_user.full_name

    text = (
        f"НОВЫЙ ЗАКАЗ\n\n"
        f"Пользователь: {full_name}"
        f"{' (@' + username + ')' if username else ''}\n"
        f"Организация: {profile.get('organization', '-')}\n"
        f"Телефон: {profile.get('phone', '-')}\n\n"
        f"{cart_text}"
    )

    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

    user_cart_store[user_id] = []

    await query.edit_message_text(
        "Заказ отправлен ✅",
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
    app = ApplicationBuilder().token(TOKEN).post_init(set_commands).build()

    app.add_handler(CommandHandler("start", start))
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
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(cancel, pattern=r"^cancel$"))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()