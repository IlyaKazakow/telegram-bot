from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "8633256261:AAHBNFW5BzGsLLAHHRhy4I1HJJixD5759cM"

ADMIN_CHAT_ID = 80263589  # @Alexandr_en

menu = {
    "Мак н чиз": {
        "Курица": 5,
        "Бекон": 7,
        "Рваная свинина": 7,
        "Чили конкарнэ": 11,
        "С сыром": 5
    },
    "Сырники": {
        "Со сметаной": 5,
        "Черника": 6,
        "Малина": 6
    },
    "Супы": {
        "Сырный": 6,
        "Бульон куриный": 5,
        "Борщ": 6
    }
}

clients = ["Севен роадс", "Рэд дор", "Гибрид"]

user_data_store = {}

# старт / new_order
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in menu.keys()]
    await update.message.reply_text("New_Order:", reply_markup=InlineKeyboardMarkup(keyboard))

# категории
async def category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat = query.data.split(":")[1]

    keyboard = [
        [InlineKeyboardButton(f"{item} — {price}₾", callback_data=f"item:{cat}:{item}")]
        for item, price in menu[cat].items()
    ]

    keyboard.append([InlineKeyboardButton("🛒 Корзина", callback_data="cart")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])

    await query.edit_message_text(cat, reply_markup=InlineKeyboardMarkup(keyboard))

# выбор количества
async def item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, cat, item = query.data.split(":")
    price = menu[cat][item]

    keyboard = [
        [InlineKeyboardButton(f"{i} шт", callback_data=f"add:{cat}:{item}:{i}")]
        for i in range(1, 6)
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"cat:{cat}")])

    await query.edit_message_text(f"{item} — {price}₾\nВыбери количество:", reply_markup=InlineKeyboardMarkup(keyboard))

# добавить
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, cat, item, qty = query.data.split(":")
    qty = int(qty)
    price = menu[cat][item]

    user_id = query.from_user.id
    user_data_store.setdefault(user_id, [])

    user_data_store[user_id].append({
        "item": item,
        "qty": qty,
        "price": price
    })

    await query.answer(f"Добавлено: {item} x{qty}")

# корзина
async def cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    items = user_data_store.get(user_id, [])

    if not items:
        text = "Корзина пуста"
        total = 0
    else:
        text = ""
        total = 0
        for i in items:
            subtotal = i["qty"] * i["price"]
            total += subtotal
            text += f'{i["item"]} x{i["qty"]} = {subtotal}₾\n'

    text += f"\nИТОГО: {total}₾"

    keyboard = [
        [InlineKeyboardButton("Выбрать заказчика", callback_data="client")],
        [InlineKeyboardButton("❌ Очистить", callback_data="clear")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# выбор клиента
async def choose_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton(c, callback_data=f"client:{c}")] for c in clients]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="cart")])

    await query.edit_message_text("Выбери заказчика:", reply_markup=InlineKeyboardMarkup(keyboard))

# подтверждение
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    client = query.data.split(":")[1]
    user_id = query.from_user.id
    items = user_data_store.get(user_id, [])

    total = 0
    text = f"Клиент: {client}\n\n"

    for i in items:
        subtotal = i["qty"] * i["price"]
        total += subtotal
        text += f'{i["item"]} x{i["qty"]} = {subtotal}₾\n'

    text += f"\nИТОГО: {total}₾"

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"final:{client}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# финал
async def final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    client = query.data.split(":")[1]
    user_id = query.from_user.id
    items = user_data_store.get(user_id, [])

    total = 0
    text = f"НОВЫЙ ЗАКАЗ\nКлиент: {client}\n\n"

    for i in items:
        subtotal = i["qty"] * i["price"]
        total += subtotal
        text += f'{i["item"]} x{i["qty"]} = {subtotal}₾\n'

    text += f"\nИТОГО: {total}₾"

    # 🔥 ОТПРАВКА АЛЕКСАНДРУ
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

    user_data_store[user_id] = []

    await query.edit_message_text("Заказ отправлен ✅")

# очистка
async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.callback_query.from_user.id] = []
    await update.callback_query.edit_message_text("Корзина очищена")

# назад
async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in menu.keys()]
    await update.callback_query.edit_message_text("New_Order:", reply_markup=InlineKeyboardMarkup(keyboard))

# отмена
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Отменено")

# запуск
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("new_order", start))
app.add_handler(CallbackQueryHandler(category, pattern="^cat:"))
app.add_handler(CallbackQueryHandler(item, pattern="^item:"))
app.add_handler(CallbackQueryHandler(add, pattern="^add:"))
app.add_handler(CallbackQueryHandler(cart, pattern="^cart$"))
app.add_handler(CallbackQueryHandler(choose_client, pattern="^client$"))
app.add_handler(CallbackQueryHandler(confirm, pattern="^client:"))
app.add_handler(CallbackQueryHandler(final, pattern="^final:"))
app.add_handler(CallbackQueryHandler(clear, pattern="^clear$"))
app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main$"))
app.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))

print("Bot started...")
app.run_polling()