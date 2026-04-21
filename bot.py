import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, date
from html import escape

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand,
    BotCommandScopeAllPrivateChats, BotCommandScopeChat,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_USER_IDS = {80263589, 374698952}
ALEXANDER_ADMIN_ID = 80263589
DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_FILE = os.path.join(DATA_DIR, "bot.db")
MIN_ORDER_QTY = 6

CANONICAL_ORGANIZATIONS = ["Севен роадс", "Гибрид", "Ред дор", "Сей ес"]

MENU = {
    "Мак н чиз": {"Курица": 5, "Бекон": 7, "Рваная свинина": 7, "Чили конкарнэ": 11, "С сыром": 5},
    "Сырники": {"Со сметаной": 5, "Черника": 6, "Малина": 6},
    "Супы": {"Сырный": 6, "Бульон куриный": 5, "Борщ": 6},
}

user_cart_store = {}

# ─── DB ───────────────────────────────────────────────────────────────────────

def get_connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def db_execute(sql, params=()):
    with get_connection() as conn:
        conn.execute(sql, params)

def db_fetchone(sql, params=()):
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None

def db_fetchall(sql, params=()):
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY, full_name TEXT, username TEXT,
                phone_original TEXT NOT NULL, phone_normalized TEXT NOT NULL,
                organization_original TEXT NOT NULL,
                organization_canonical TEXT,
                organization_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL, full_name TEXT, username TEXT,
                phone_original TEXT NOT NULL, phone_normalized TEXT NOT NULL,
                organization_original TEXT NOT NULL,
                organization_canonical TEXT,
                organization_status TEXT NOT NULL DEFAULT 'pending',
                items_json TEXT NOT NULL,
                total_amount REAL NOT NULL, total_qty INTEGER NOT NULL,
                payment_status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL
            );
        """)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_admin(uid):
    return uid in ADMIN_USER_IDS

def is_alexander(uid):
    return uid == ALEXANDER_ADMIN_ID

def normalize_phone(phone):
    phone = phone.strip()
    prefix, body = ("+", phone[1:]) if phone.startswith("+") else ("", phone)
    return prefix + re.sub(r"\D", "", body)

def is_valid_phone(phone):
    n = normalize_phone(phone)
    digits = n[1:] if n.startswith("+") else n
    return 6 <= len(digits) <= 20

def normalize_org_text(text):
    v = text.strip().lower().replace("ё", "е").replace("-", " ")
    v = re.sub(r'[\"\'`.,;:!?(){}\[\]/\\]+', " ", v)
    return re.sub(r"\s+", " ", v).strip()

def get_effective_org_name(canonical, status):
    return canonical if status == "confirmed" and canonical else "⏳ Неподтверждённые"

def is_profile_confirmed(profile_data):
    return bool(
        profile_data
        and profile_data.get("organization_status") == "confirmed"
        and profile_data.get("organization_canonical")
    )

def get_total_qty(items):
    return sum(i["qty"] for i in items)

def format_cart(items):
    if not items:
        return "Корзина пуста", 0
    lines, total = [], 0
    for i in items:
        sub = i["qty"] * i["price"]
        total += sub
        lines.append(f'{i["item"]} x{i["qty"]} = {sub}₾')
    return "\n".join(lines) + f"\n\nИТОГО: {total}₾", total

def user_link(uid, name):
    return f'<a href="tg://user?id={uid}">{escape(name or "Пользователь")}</a>'

def fmt_username(u):
    return f"@{u}" if u else "-"

def profile_button_label(profile_data):
    name = profile_data.get("full_name") or "Без имени"
    status_icon = "🟢" if profile_data.get("organization_status") == "confirmed" else "🟡"
    return f"{status_icon} {name}"

# ─── Keyboards ────────────────────────────────────────────────────────────────

def kb(*rows):
    return InlineKeyboardMarkup(rows)

def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)

def main_menu_keyboard():
    rows = [[btn(cat, f"cat:{cat}")] for cat in MENU]
    rows += [[btn("🛒 Корзина", "cart")], [btn("⚙️ Профиль", "profile")]]
    return kb(*rows)

def profile_keyboard():
    return kb(
        [btn("📞 Изменить телефон", "edit_phone")],
        [btn("🏢 Изменить организацию", "edit_organization")],
        [btn("🔙 Назад", "back_main")],
    )

def contact_request_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )

def admin_order_keyboard(order_id):
    return kb([btn("✅ Оплачен", f"mark_paid:{order_id}"), btn("❌ Не оплачен", f"mark_unpaid:{order_id}")])

def profile_actions_keyboard(profile_user_id, viewer_user_id, back_target="profiles_list"):
    rows = [[btn(org, f"confirm_profile_org:{profile_user_id}:{org}")] for org in CANONICAL_ORGANIZATIONS]
    rows.append([btn("⏳ Оставить без подтверждения", f"keep_profile_pending:{profile_user_id}")])

    if is_alexander(viewer_user_id):
        rows.append([btn("🗑 Удалить профиль", f"delete_profile_confirm:{profile_user_id}")])

    rows.append([btn("🔙 Назад к списку", back_target)])
    return InlineKeyboardMarkup(rows)

def org_confirm_keyboard(prefix, entity_id):
    rows = [[btn(org, f"{prefix}:{entity_id}:{org}")] for org in CANONICAL_ORGANIZATIONS]
    rows.append([
        btn(
            "⏳ Оставить без подтверждения",
            f"keep_profile_pending:{entity_id}" if "profile" in prefix else f"keep_order_pending:{entity_id}"
        )
    ])
    return InlineKeyboardMarkup(rows)

def delete_profile_confirm_keyboard(uid):
    return kb(
        [btn("✅ Да, удалить", f"delete_profile_execute:{uid}")],
        [btn("❌ Отмена", f"delete_profile_cancel:{uid}")],
    )

def pending_org_block_keyboard():
    return kb(
        [btn("⚙️ Профиль", "profile")],
        [btn("🏠 В меню", "back_main")],
    )

def profiles_list_keyboard(profiles, viewer_user_id):
    rows = [[btn(profile_button_label(p), f"open_profile:{p['user_id']}")] for p in profiles]
    rows.append([btn("🏠 В меню", "back_main")])
    return InlineKeyboardMarkup(rows)

def pending_profiles_list_keyboard(profiles):
    rows = [[btn(profile_button_label(p), f"open_pending_profile:{p['user_id']}")] for p in profiles]
    rows.append([btn("🏠 В меню", "back_main")])
    return InlineKeyboardMarkup(rows)

# ─── Profile DB ───────────────────────────────────────────────────────────────

def get_profile(uid):
    return db_fetchone("SELECT * FROM profiles WHERE user_id = ?", (str(uid),))

def save_profile(uid, full_name, username, phone_original, org_original):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    phone_normalized = normalize_phone(phone_original)
    existing = get_profile(uid)
    if existing and normalize_org_text(existing["organization_original"]) == normalize_org_text(org_original):
        org_canonical, org_status = existing["organization_canonical"], existing["organization_status"]
    else:
        org_canonical, org_status = None, "pending"
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO profiles (user_id, full_name, username, phone_original, phone_normalized,
                organization_original, organization_canonical, organization_status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                full_name=excluded.full_name, username=excluded.username,
                phone_original=excluded.phone_original, phone_normalized=excluded.phone_normalized,
                organization_original=excluded.organization_original,
                organization_canonical=excluded.organization_canonical,
                organization_status=excluded.organization_status,
                updated_at=excluded.updated_at
        """, (str(uid), full_name, username, phone_original, phone_normalized,
              org_original, org_canonical, org_status,
              existing["created_at"] if existing else now, now))

def get_duplicate_profiles_by_phone(phone_normalized, exclude_uid=None):
    if exclude_uid:
        return db_fetchall("SELECT * FROM profiles WHERE phone_normalized=? AND user_id!=? ORDER BY created_at DESC",
                           (phone_normalized, str(exclude_uid)))
    return db_fetchall("SELECT * FROM profiles WHERE phone_normalized=? ORDER BY created_at DESC", (phone_normalized,))

def set_profile_canonical_org(uid, canonical_org):
    db_execute(
        "UPDATE profiles SET organization_canonical=?, organization_status='confirmed', updated_at=? WHERE user_id=?",
        (canonical_org, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(uid))
    )

def set_profile_pending_org(uid):
    db_execute(
        "UPDATE profiles SET organization_canonical=NULL, organization_status='pending', updated_at=? WHERE user_id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(uid))
    )

def delete_profile_by_user_id(uid):
    with get_connection() as conn:
        return conn.execute("DELETE FROM profiles WHERE user_id=?", (str(uid),)).rowcount > 0

# ─── Order DB ─────────────────────────────────────────────────────────────────

def save_order_to_db(uid, full_name, username, phone_original, phone_normalized,
                     org_original, org_canonical, org_status, items, total_amount, total_qty):
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO orders (user_id, full_name, username, phone_original, phone_normalized,
                organization_original, organization_canonical, organization_status,
                items_json, total_amount, total_qty, payment_status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (str(uid), full_name, username, phone_original, phone_normalized,
              org_original, org_canonical, org_status,
              json.dumps(items, ensure_ascii=False), total_amount, total_qty,
              "new", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        return cur.lastrowid

def set_order_canonical_org(order_id, canonical_org):
    db_execute("UPDATE orders SET organization_canonical=?, organization_status='confirmed' WHERE id=?",
               (canonical_org, int(order_id)))

def update_order_payment_status(order_id, status):
    db_execute("UPDATE orders SET payment_status=? WHERE id=?", (status, int(order_id)))

def get_profiles(limit=500):
    return db_fetchall("SELECT * FROM profiles ORDER BY created_at DESC LIMIT ?", (limit,))

def get_pending_profiles(limit=200):
    return db_fetchall("SELECT * FROM profiles WHERE organization_status='pending' ORDER BY created_at DESC LIMIT ?", (limit,))

def get_unpaid_orders(limit=200):
    return db_fetchall("SELECT * FROM orders WHERE payment_status IN ('new','unpaid') ORDER BY created_at DESC LIMIT ?", (limit,))

# ─── Reports ──────────────────────────────────────────────────────────────────

def get_report_by_range(start, end):
    rows = db_fetchall(
        "SELECT organization_canonical, organization_status, payment_status, total_amount, total_qty "
        "FROM orders WHERE created_at >= ? AND created_at < ? ORDER BY created_at DESC",
        (start, end)
    )
    org_stats = {}
    summary = {"total_orders": len(rows), "paid_orders": 0, "unpaid_orders": 0, "new_orders": 0, "paid_revenue": 0}
    for r in rows:
        ps = r["payment_status"]
        if ps == "paid":
            summary["paid_orders"] += 1
            summary["paid_revenue"] += r["total_amount"]
        elif ps == "unpaid":
            summary["unpaid_orders"] += 1
        elif ps == "new":
            summary["new_orders"] += 1
        org = get_effective_org_name(r["organization_canonical"], r["organization_status"])
        s = org_stats.setdefault(org, {"orders": 0, "qty": 0, "paid_orders": 0, "unpaid_orders": 0, "new_orders": 0, "paid_amount": 0})
        s["orders"] += 1
        s["qty"] += r["total_qty"]
        if ps == "paid":
            s["paid_orders"] += 1
            s["paid_amount"] += r["total_amount"]
        elif ps == "unpaid":
            s["unpaid_orders"] += 1
        elif ps == "new":
            s["new_orders"] += 1
    return {**summary, "org_stats": org_stats}

def get_report_last_n_days(days):
    end = datetime.now()
    start = end - timedelta(days=days)
    return get_report_by_range(start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))

def get_period_range_last_month():
    today = date.today()
    first_cur = date(today.year, today.month, 1)
    last_prev = first_cur - timedelta(days=1)
    first_prev = date(last_prev.year, last_prev.month, 1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        datetime.combine(first_prev, datetime.min.time()).strftime(fmt),
        datetime.combine(first_cur, datetime.min.time()).strftime(fmt),
        first_prev,
        last_prev
    )

def format_report_text(title, report):
    text = (f"{title}\n\nВсего заказов: {report['total_orders']}\nНовых: {report['new_orders']}\n"
            f"Оплачено: {report['paid_orders']}\nНе оплачено: {report['unpaid_orders']}\n"
            f"Оплаченная выручка: {report['paid_revenue']}₾\n\nПО ОРГАНИЗАЦИЯМ:\n")
    if not report["org_stats"]:
        return text + "\nНет заказов за этот период."
    for org, d in report["org_stats"].items():
        text += (f"\n— {org}\nЗаказов: {d['orders']}\nШтук: {d['qty']}\nНовых: {d['new_orders']}\n"
                 f"Оплачено: {d['paid_orders']}\nНе оплачено: {d['unpaid_orders']}\nОплаченная сумма: {d['paid_amount']}₾\n")
    return text

# ─── Card builders ────────────────────────────────────────────────────────────

def build_profile_card_text(p, header="ПРОФИЛЬ"):
    return (
        f"{header}\n\n"
        f"Пользователь: {user_link(p['user_id'], p.get('full_name'))}\n"
        f"Username: {escape(fmt_username(p.get('username')))}\n"
        f"User ID: {p['user_id']}\n"
        f"Телефон: {escape(p['phone_original'])}\n"
        f"Телефон normalized: {escape(p['phone_normalized'])}\n"
        f"Организация original: {escape(p['organization_original'])}\n"
        f"Организация canonical: {escape(p['organization_canonical'] or '-')}\n"
        f"Статус организации: {escape(p['organization_status'])}\n"
        f"Создан: {escape(p['created_at'])}"
    )

def build_unpaid_order_card_text(o):
    return (f"НЕОПЛАЧЕННЫЙ ЗАКАЗ #{o['id']}\n\nПользователь: {user_link(o['user_id'], o.get('full_name'))}\n"
            f"Username: {escape(fmt_username(o.get('username')))}\nUser ID: {o['user_id']}\n"
            f"Телефон для доставки: {escape(o['phone_original'])}\n"
            f"Организация original: {escape(o['organization_original'])}\n"
            f"Организация canonical: {escape(o['organization_canonical'] or '-')}\n"
            f"Статус организации: {escape(o['organization_status'])}\nДата: {escape(o['created_at'])}\n"
            f"Сумма: {o['total_amount']}₾\nШтук: {o['total_qty']}\nСтатус оплаты: {escape(o['payment_status'].upper())}")

# ─── Admin notifications ──────────────────────────────────────────────────────

async def notify_admin_about_profile(profile, duplicates, context):
    text = (
        f"НОВЫЙ / ОБНОВЛЁННЫЙ ПРОФИЛЬ\n\nПользователь: {user_link(profile['user_id'], profile.get('full_name'))}\n"
        f"Username: {escape(fmt_username(profile.get('username')))}\nUser ID: {profile['user_id']}\n"
        f"Телефон: {escape(profile['phone_original'])}\nТелефон normalized: {escape(profile['phone_normalized'])}\n"
        f"Организация original: {escape(profile['organization_original'])}\n"
        f"Организация canonical: {escape(profile['organization_canonical'] or '-')}\n"
        f"Статус организации: {escape(profile['organization_status'])}\n"
    )
    if duplicates:
        text += "\n⚠️ Найдены дубли по номеру:\n"
        for d in duplicates[:10]:
            text += f"- {escape(d.get('full_name') or '-')}, {escape(fmt_username(d.get('username')))}, user_id={d['user_id']}, org={escape(d['organization_original'])}\n"

    for aid in ADMIN_USER_IDS:
        await context.bot.send_message(
            chat_id=aid,
            text=text,
            parse_mode="HTML",
            reply_markup=profile_actions_keyboard(profile["user_id"], aid, back_target="profiles_list")
        )

async def notify_admin_about_pending_order(order_id, profile, uid, context):
    text = (f"ЗАКАЗ #{order_id} ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ ОРГАНИЗАЦИИ\n\n"
            f"Пользователь: {user_link(uid, profile.get('full_name'))}\n"
            f"Username: {escape(fmt_username(profile.get('username')))}\nUser ID: {uid}\n"
            f"Телефон: {escape(profile['phone_original'])}\nТелефон normalized: {escape(profile['phone_normalized'])}\n"
            f"Организация original: {escape(profile['organization_original'])}\nСтатус организации: pending")
    for aid in ADMIN_USER_IDS:
        await context.bot.send_message(
            chat_id=aid,
            text=text,
            parse_mode="HTML",
            reply_markup=org_confirm_keyboard("confirm_order_org", order_id)
        )

# ─── Navigation helpers ───────────────────────────────────────────────────────

async def show_main_menu_message(message):
    await message.reply_text("Выберите категорию:", reply_markup=main_menu_keyboard())

async def show_main_menu_callback(query):
    await query.edit_message_text("Выберите категорию:", reply_markup=main_menu_keyboard())

async def show_profiles_list(query_or_message, viewer_user_id):
    profiles = get_profiles()
    text = f"ПРОФИЛИ\n\nВсего профилей: {len(profiles)}\nНажмите на имя, чтобы открыть профиль."

    if not profiles:
        text = "Профилей пока нет."
        markup = kb([btn("🏠 В меню", "back_main")])
    else:
        markup = profiles_list_keyboard(profiles, viewer_user_id)

    if hasattr(query_or_message, "edit_message_text"):
        await query_or_message.edit_message_text(text, reply_markup=markup)
    else:
        await query_or_message.reply_text(text, reply_markup=markup)

async def show_pending_profiles_list(query_or_message, viewer_user_id):
    profiles = get_pending_profiles()
    text = f"ПРОФИЛИ БЕЗ ПОДТВЕРЖДЕНИЯ\n\nНайдено: {len(profiles)}\nНажмите на имя, чтобы открыть профиль."

    if not profiles:
        text = "Нет профилей с неподтверждённой организацией."
        markup = kb([btn("🏠 В меню", "back_main")])
    else:
        markup = pending_profiles_list_keyboard(profiles)

    if hasattr(query_or_message, "edit_message_text"):
        await query_or_message.edit_message_text(text, reply_markup=markup)
    else:
        await query_or_message.reply_text(text, reply_markup=markup)

# ─── Commands setup ───────────────────────────────────────────────────────────

async def set_commands(application):
    await application.bot.set_my_commands(
        [BotCommand("start", "Открыть меню")], scope=BotCommandScopeAllPrivateChats()
    )
    for aid in ADMIN_USER_IDS:
        await application.bot.set_my_commands([
            BotCommand("start", "Открыть меню"),
            BotCommand("week", "Отчёт за 7 дней"),
            BotCommand("month", "Отчёт за 30 дней"),
            BotCommand("last_month", "Отчёт за прошлый месяц"),
            BotCommand("profiles", "Все профили"),
            BotCommand("pending_profiles", "Профили без подтверждения"),
            BotCommand("unpaid", "Неоплаченные заказы"),
        ], scope=BotCommandScopeChat(chat_id=aid))

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not get_profile(uid):
        context.user_data["reg_step"] = "phone"
        await update.message.reply_text(
            "Добро пожаловать.\nНажмите кнопку ниже, чтобы поделиться номером телефона для доставки:",
            reply_markup=contact_request_keyboard()
        )
    else:
        await show_main_menu_message(update.message)

async def registration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("reg_step")
    if not mode:
        return

    uid = str(update.effective_user.id)
    full_name = update.effective_user.full_name
    username = update.effective_user.username or ""

    if mode == "phone":
        phone = None
        if update.message.contact:
            if update.message.contact.user_id and str(update.message.contact.user_id) != uid:
                await update.message.reply_text(
                    "Пожалуйста, поделитесь своим номером через кнопку ниже.",
                    reply_markup=contact_request_keyboard()
                )
                return
            phone = update.message.contact.phone_number
        elif update.message.text:
            phone = update.message.text.strip()

        if not phone or not is_valid_phone(phone):
            await update.message.reply_text(
                "Номер телефона введён некорректно.\nНажмите кнопку «Поделиться номером» или введите номер ещё раз:",
                reply_markup=contact_request_keyboard()
            )
            return

        context.user_data["phone_original"] = phone
        context.user_data["reg_step"] = "organization"
        await update.message.reply_text("Теперь введите организацию:", reply_markup=ReplyKeyboardRemove())
        return

    if not update.message.text:
        await update.message.reply_text("Пожалуйста, отправьте текстом нужное значение.")
        return

    text = update.message.text.strip()

    if mode in ("organization", "edit_organization"):
        if not text:
            await update.message.reply_text("Организация не может быть пустой. Введите организацию:")
            return
        current = get_profile(uid)
        phone = context.user_data.get("phone_original") if mode == "organization" else current["phone_original"]
        save_profile(uid, full_name, username, phone, text)
        msg = "Регистрация завершена ✅" if mode == "organization" else "Организация обновлена ✅"
        context.user_data.pop("reg_step", None)
        context.user_data.pop("phone_original", None)
        await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
        await show_main_menu_message(update.message)

    elif mode == "edit_phone":
        if not is_valid_phone(text):
            await update.message.reply_text("Номер телефона введён некорректно.\nВведите номер ещё раз:")
            return
        current = get_profile(uid)
        save_profile(uid, full_name, username, text, current["organization_original"] if current else "-")
        context.user_data.pop("reg_step", None)
        await update.message.reply_text("Телефон обновлён ✅")
        await show_main_menu_message(update.message)

    profile_data = get_profile(uid)
    dupes = get_duplicate_profiles_by_phone(profile_data["phone_normalized"], exclude_uid=uid)
    await notify_admin_about_profile(profile_data, dupes, context)

async def category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split(":", 1)[1]
    rows = [[btn(f"{item} — {price}₾", f"item:{cat}:{item}")] for item, price in MENU[cat].items()]
    rows += [[btn("🛒 Корзина", "cart")], [btn("🔙 Назад", "back_main")]]
    await query.edit_message_text(cat, reply_markup=InlineKeyboardMarkup(rows))

async def item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, cat, item_name = query.data.split(":", 2)
    price = MENU[cat][item_name]
    rows = [[btn(f"{qty} шт", f"add:{cat}:{item_name}:{qty}")] for qty in [3, 6, 12]]
    rows.append([btn("🔙 Назад", f"cat:{cat}")])
    await query.edit_message_text(f"{item_name} — {price}₾\nВыбери количество:", reply_markup=InlineKeyboardMarkup(rows))

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, cat, item_name, qty = query.data.split(":", 3)
    qty = int(qty)
    uid = str(query.from_user.id)
    user_cart_store.setdefault(uid, []).append({"item": item_name, "qty": qty, "price": MENU[cat][item_name]})
    await query.edit_message_text(
        f"Добавлено: {item_name} x{qty}",
        reply_markup=kb([btn("➕ Добавить ещё", f"cat:{cat}")], [btn("🛒 Корзина", "cart")], [btn("🏠 В меню", "back_main")])
    )

async def cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    items = user_cart_store.get(uid, [])
    text, total = format_cart(items)
    total_qty = get_total_qty(items)
    if items:
        text += f"\nВсего штук: {total_qty}"
        if total_qty < MIN_ORDER_QTY:
            text += f"\n\n⚠️ Минимальный заказ — от {MIN_ORDER_QTY} шт суммарно по корзине."
    rows = (
        [[btn("✅ Оформить заказ", "checkout")], [btn("❌ Очистить", "clear")], [btn("🔙 Назад", "back_main")]]
        if total > 0 else
        [[btn("🏠 В меню", "back_main")]]
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))

async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    profile_data = get_profile(uid)
    items = user_cart_store.get(uid, [])

    if not profile_data:
        await query.edit_message_text("Профиль не найден. Нажмите /start и пройдите регистрацию заново.")
        return

    if not is_profile_confirmed(profile_data):
        await query.edit_message_text(
            "Ваша организация ещё не подтверждена администратором.\n"
            "После подтверждения вы сможете оформить заказ.",
            reply_markup=pending_org_block_keyboard()
        )
        return

    if not items:
        await query.edit_message_text("Корзина пуста.", reply_markup=kb([btn("🏠 В меню", "back_main")]))
        return

    total_qty = get_total_qty(items)
    if total_qty < MIN_ORDER_QTY:
        await query.edit_message_text(
            f"Минимальный заказ — от {MIN_ORDER_QTY} шт суммарно по корзине.\nСейчас в корзине: {total_qty} шт.",
            reply_markup=kb([btn("🛒 Вернуться в корзину", "cart")], [btn("🏠 В меню", "back_main")])
        )
        return

    cart_text, _ = format_cart(items)
    text = (
        f"Подтверждение заказа\n\nОрганизация: {profile_data.get('organization_original', '-')}\n"
        f"Телефон для доставки: {profile_data.get('phone_original', '-')}\n\n{cart_text}\nВсего штук: {total_qty}"
    )
    await query.edit_message_text(text, reply_markup=kb([btn("✅ Подтвердить", "final")], [btn("❌ Отмена", "cancel")]))

async def final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    items = user_cart_store.get(uid, [])
    profile_data = get_profile(uid)

    if not profile_data:
        await query.edit_message_text("Профиль не найден. Нажмите /start и зарегистрируйтесь снова.")
        return

    if not is_profile_confirmed(profile_data):
        await query.edit_message_text(
            "Ваша организация ещё не подтверждена администратором.\n"
            "Оформление заказа пока недоступно.",
            reply_markup=pending_org_block_keyboard()
        )
        return

    if not items:
        await query.edit_message_text("Корзина пуста.", reply_markup=kb([btn("🏠 В меню", "back_main")]))
        return

    total_qty = get_total_qty(items)
    if total_qty < MIN_ORDER_QTY:
        await query.edit_message_text(
            f"Минимальный заказ — от {MIN_ORDER_QTY} шт суммарно по корзине.\nСейчас в корзине: {total_qty} шт.",
            reply_markup=kb([btn("🛒 Вернуться в корзину", "cart")], [btn("🏠 В меню", "back_main")])
        )
        return

    cart_text, total_amount = format_cart(items)
    username = query.from_user.username or ""
    full_name = query.from_user.full_name

    order_id = save_order_to_db(
        uid, full_name, username,
        profile_data["phone_original"], profile_data["phone_normalized"],
        profile_data["organization_original"], profile_data["organization_canonical"],
        profile_data["organization_status"], items, total_amount, total_qty,
    )

    admin_text = (
        f"НОВЫЙ ЗАКАЗ #{order_id}\n\nПользователь: {user_link(uid, full_name)}\n"
        f"Username: {escape(fmt_username(username))}\nUser ID: {uid}\n"
        f"Телефон для доставки: {escape(profile_data['phone_original'])}\n"
        f"Телефон normalized: {escape(profile_data['phone_normalized'])}\n"
        f"Организация original: {escape(profile_data['organization_original'])}\n"
        f"Организация canonical: {escape(profile_data['organization_canonical'] or '-')}\n"
        f"Статус организации: {escape(profile_data['organization_status'])}\n\n"
        f"{escape(cart_text)}\nВсего штук: {total_qty}\nСтатус оплаты: NEW"
    )
    for aid in ADMIN_USER_IDS:
        await context.bot.send_message(
            chat_id=aid,
            text=admin_text,
            parse_mode="HTML",
            reply_markup=admin_order_keyboard(order_id)
        )

    user_cart_store[uid] = []
    await query.edit_message_text(
        f"Заказ отправлен ✅\nНомер заказа: #{order_id}",
        reply_markup=kb([btn("🏠 В меню", "back_main")])
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_cart_store[str(query.from_user.id)] = []
    await query.edit_message_text("Корзина очищена ✅",
                                  reply_markup=kb([btn("🏠 В меню", "back_main")], [btn("⚙️ Профиль", "profile")]))

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    p = get_profile(str(query.from_user.id))
    if not p:
        await query.edit_message_text("Профиль не найден.\nНажмите /start для регистрации.")
        return
    text = (f"Ваш профиль:\n\nТелефон для доставки: {p.get('phone_original', '-')}\n"
            f"Организация: {p.get('organization_original', '-')}\n"
            f"Статус организации: {p.get('organization_status', '-')}\n"
            f"Подтверждённая организация: {p.get('organization_canonical') or '-'}")
    await query.edit_message_text(text, reply_markup=profile_keyboard())

async def edit_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["reg_step"] = "edit_phone"
    await query.edit_message_text("Введите новый номер телефона для доставки:")

async def edit_organization(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["reg_step"] = "edit_organization"
    await query.edit_message_text("Введите новую организацию:")

async def mark_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    action, order_id = query.data.split(":")
    update_order_payment_status(int(order_id), "paid" if action == "mark_paid" else "unpaid")
    new_status = "PAID" if action == "mark_paid" else "UNPAID"
    lines = query.message.text.splitlines()
    updated = [f"Статус оплаты: {new_status}" if l.startswith("Статус оплаты:") else l for l in lines]
    if not any(l.startswith("Статус оплаты:") for l in lines):
        updated.append(f"Статус оплаты: {new_status}")
    await query.edit_message_text("\n".join(updated), parse_mode="HTML", reply_markup=admin_order_keyboard(int(order_id)))

async def confirm_profile_org(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return

    await query.answer()

    _, uid, canonical_org = query.data.split(":", 2)
    set_profile_canonical_org(uid, canonical_org)
    p = get_profile(uid)

    if not p:
        await query.edit_message_text("Профиль не найден.")
        return

    await query.edit_message_text(
        build_profile_card_text(p, header="ПРОФИЛЬ"),
        parse_mode="HTML",
        reply_markup=profile_actions_keyboard(
            profile_user_id=p["user_id"],
            viewer_user_id=query.from_user.id,
            back_target="profiles_list"
        )
    )

async def keep_profile_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return

    await query.answer("Оставлено без подтверждения")

    _, uid = query.data.split(":", 1)
    set_profile_pending_org(uid)
    p = get_profile(uid)

    if not p:
        await query.edit_message_text("Профиль не найден.")
        return

    await query.edit_message_text(
        build_profile_card_text(p, header="ПРОФИЛЬ"),
        parse_mode="HTML",
        reply_markup=profile_actions_keyboard(
            profile_user_id=p["user_id"],
            viewer_user_id=query.from_user.id,
            back_target="profiles_list"
        )
    )

async def delete_profile_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_alexander(query.from_user.id):
        await query.answer("Удалять профили может только Александр", show_alert=True)
        return
    await query.answer()
    _, uid = query.data.split(":", 1)
    p = get_profile(uid)
    if not p:
        await query.edit_message_text("Профиль уже удалён или не найден.")
        return
    text = (f"ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ ПРОФИЛЯ\n\nПользователь: {p.get('full_name') or '-'}"
            f"{' (@' + p.get('username') + ')' if p.get('username') else ''}\nUser ID: {p['user_id']}\n"
            f"Телефон: {p['phone_original']}\nОрганизация original: {p['organization_original']}\n\n"
            "⚠️ Профиль будет удалён из базы profiles.\nИстория заказов останется.")
    await query.edit_message_text(text, reply_markup=delete_profile_confirm_keyboard(uid))

async def delete_profile_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_alexander(query.from_user.id):
        await query.answer("Удалять профили может только Александр", show_alert=True)
        return
    await query.answer()
    _, uid = query.data.split(":", 1)
    if delete_profile_by_user_id(uid):
        await query.edit_message_text(
            f"Профиль user_id={uid} удалён ✅\nИстория заказов сохранена.",
            reply_markup=kb([btn("🔙 К списку профилей", "profiles_list")])
        )
    else:
        await query.edit_message_text("Профиль не найден или уже удалён.")

async def delete_profile_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_alexander(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer("Удаление отменено")
    _, uid = query.data.split(":", 1)
    p = get_profile(uid)
    if not p:
        await query.edit_message_text("Профиль не найден.")
        return
    await query.edit_message_text(
        build_profile_card_text(p, header="ПРОФИЛЬ"),
        parse_mode="HTML",
        reply_markup=profile_actions_keyboard(
            profile_user_id=p["user_id"],
            viewer_user_id=query.from_user.id,
            back_target="profiles_list"
        )
    )

async def confirm_order_org(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, order_id, canonical_org = query.data.split(":", 2)
    set_order_canonical_org(order_id, canonical_org)
    lines = query.message.text.splitlines()
    updated, c_done, s_done = [], False, False
    for l in lines:
        if l.startswith("Организация canonical:"):
            updated.append(f"Организация canonical: {canonical_org}")
            c_done = True
        elif l.startswith("Статус организации:"):
            updated.append("Статус организации: confirmed")
            s_done = True
        else:
            updated.append(l)
    if not c_done:
        updated.append(f"Организация canonical: {canonical_org}")
    if not s_done:
        updated.append("Статус организации: confirmed")
    await query.edit_message_text("\n".join(updated), parse_mode="HTML")

async def keep_order_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer("Оставлено без подтверждения")

async def open_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, uid = query.data.split(":", 1)
    p = get_profile(uid)
    if not p:
        await query.edit_message_text("Профиль не найден.")
        return
    await query.edit_message_text(
        build_profile_card_text(p, header="ПРОФИЛЬ"),
        parse_mode="HTML",
        reply_markup=profile_actions_keyboard(
            profile_user_id=p["user_id"],
            viewer_user_id=query.from_user.id,
            back_target="profiles_list"
        )
    )

async def open_pending_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, uid = query.data.split(":", 1)
    p = get_profile(uid)
    if not p:
        await query.edit_message_text("Профиль не найден.")
        return
    await query.edit_message_text(
        build_profile_card_text(p, header="ПРОФИЛЬ БЕЗ ПОДТВЕРЖДЕНИЯ"),
        parse_mode="HTML",
        reply_markup=profile_actions_keyboard(
            profile_user_id=p["user_id"],
            viewer_user_id=query.from_user.id,
            back_target="pending_profiles_list"
        )
    )

async def profiles_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    await show_profiles_list(query, query.from_user.id)

async def pending_profiles_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    await show_pending_profiles_list(query, query.from_user.id)

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_main_menu_callback(query)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Отменено", reply_markup=kb([btn("🏠 В меню", "back_main")]))

# ─── Admin commands ───────────────────────────────────────────────────────────

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("У вас нет доступа к этой команде.")
            return
        await func(update, context)
    return wrapper

@admin_only
async def report_week(update, context):
    await update.message.reply_text(format_report_text("ОТЧЁТ ЗА 7 ДНЕЙ", get_report_last_n_days(7)))

@admin_only
async def report_month(update, context):
    await update.message.reply_text(format_report_text("ОТЧЁТ ЗА 30 ДНЕЙ", get_report_last_n_days(30)))

@admin_only
async def report_last_month(update, context):
    start, end, first_prev, last_prev = get_period_range_last_month()
    title = f"ОТЧЁТ ЗА ПРОШЛЫЙ МЕСЯЦ ({first_prev.strftime('%d.%m.%Y')} - {last_prev.strftime('%d.%m.%Y')})"
    await update.message.reply_text(format_report_text(title, get_report_by_range(start, end)))

@admin_only
async def profiles_command(update, context):
    await show_profiles_list(update.message, update.effective_user.id)

@admin_only
async def pending_profiles_command(update, context):
    await show_pending_profiles_list(update.message, update.effective_user.id)

@admin_only
async def unpaid_orders_command(update, context):
    orders = get_unpaid_orders()
    if not orders:
        await update.message.reply_text("Неоплаченных заказов нет.")
        return
    await update.message.reply_text(
        f"НЕОПЛАЧЕННЫЕ ЗАКАЗЫ\n\nВсего заказов: {len(orders)}\n"
        f"Общая сумма: {sum(o['total_amount'] for o in orders)}₾\n"
        f"Всего штук: {sum(o['total_qty'] for o in orders)}"
    )
    for o in orders:
        await update.message.reply_text(
            build_unpaid_order_card_text(o),
            parse_mode="HTML",
            reply_markup=admin_order_keyboard(o["id"])
        )
        if o["organization_status"] != "confirmed":
            await update.message.reply_text(
                f"Подтвердить организацию для заказа #{o['id']}:",
                reply_markup=org_confirm_keyboard("confirm_order_org", o["id"])
            )

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN не задан в переменных окружения Railway")

    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(set_commands).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("week", report_week))
    app.add_handler(CommandHandler("month", report_month))
    app.add_handler(CommandHandler("last_month", report_last_month))
    app.add_handler(CommandHandler("profiles", profiles_command))
    app.add_handler(CommandHandler("pending_profiles", pending_profiles_command))
    app.add_handler(CommandHandler("unpaid", unpaid_orders_command))
    app.add_handler(MessageHandler((filters.TEXT | filters.CONTACT) & ~filters.COMMAND, registration_handler))

    for pattern, handler in [
        (r"^cat:", category),
        (r"^item:", item),
        (r"^add:", add),
        (r"^cart$", cart),
        (r"^checkout$", checkout),
        (r"^final$", final),
        (r"^clear$", clear),
        (r"^profile$", profile),
        (r"^edit_phone$", edit_phone),
        (r"^edit_organization$", edit_organization),
        (r"^(mark_paid|mark_unpaid):", mark_order_status),
        (r"^confirm_profile_org:", confirm_profile_org),
        (r"^keep_profile_pending:", keep_profile_pending),
        (r"^delete_profile_confirm:", delete_profile_confirm),
        (r"^delete_profile_execute:", delete_profile_execute),
        (r"^delete_profile_cancel:", delete_profile_cancel),
        (r"^confirm_order_org:", confirm_order_org),
        (r"^keep_order_pending:", keep_order_pending),
        (r"^open_profile:", open_profile),
        (r"^open_pending_profile:", open_pending_profile),
        (r"^profiles_list$", profiles_list_callback),
        (r"^pending_profiles_list$", pending_profiles_list_callback),
        (r"^back_main$", back_main),
        (r"^cancel$", cancel),
    ]:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))

    print(f"Bot DB path: {DB_FILE}")
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()