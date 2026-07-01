# bot.py
import os
import math
import random
import time
import logging
import asyncio
import html
from decimal import Decimal, InvalidOperation
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

ADMIN_ID = 7921743592
MAX_DB_INT = 9223372036854775807  # Физический лимит BIGINT для баз данных

# --- БЫСТРОЕ ХРАНИЛИЩЕ ДЛЯ ПАРАЛЛЕЛЬНЫХ ИГР (БЕЗ ЛАГОВ И БЛОКИРОВОК) ---
SOLO_GAMES = {}  # Ключ: (user_id, game_type) -> данные игры
DUELS = {}       # Ключ: уникальный токен дуэли (int) -> данные дуэли

TOWER_MULTIPLIERS = {
    1: [1.15, 1.40, 1.75, 2.20, 2.80],
    2: [2.50, 5.80, 14.00, 35.00, 90.00],
    3: [5.00, 22.00, 95.00, 450.00, 2500.00],
    4: [15.00, 120.00, 1100.00, 9500.00, 85000.00]
}

GOLD_MULTIPLIERS = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]

CARDS_21 = {
    '6 🂶': 6, '7 🂷': 7, '8 🂸': 8, '9 🂹': 9, '10 🂺': 10,
    'Валет 🂻': 2, 'Дама 🂽': 3, 'Король 🂾': 4, 'Туз 🂱': 11
}

class AdminStates(StatesGroup):
    waiting_for_give_id = State()
    waiting_for_give_amount = State()
    waiting_for_take_id = State()
    waiting_for_take_amount = State()
    waiting_for_promo_name = State()
    waiting_for_promo_reward = State()
    waiting_for_promo_activations = State()

# --- МИДЛВАРЬ ДЛЯ КАТЕГОРИЧЕСКОЙ ПРОВЕРКИ ПОДПИСКИ ---
class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        if not user or user.id == ADMIN_ID:
            return await handler(event, data)

        if isinstance(event, types.Message) and event.chat.type != "private":
            return await handler(event, data)
        if isinstance(event, types.CallbackQuery) and event.message and event.message.chat.type != "private":
            return await handler(event, data)

        if isinstance(event, types.CallbackQuery) and event.data == "sub_check_btn":
            return await handler(event, data)

        bot_instance = data.get("bot")
        is_subscribed = True

        for channel in ["@Chat_ucoins", "@Ucoins_news"]:
            try:
                member = await bot_instance.get_chat_member(chat_id=channel, user_id=user.id)
                if member.status in ["left", "kicked"]:
                    is_subscribed = False
                    break
            except Exception:
                is_subscribed = False
                break

        if not is_subscribed:
            builder = InlineKeyboardBuilder()
            builder.button(text="💬 Войти в чат", url="https://t.me/Chat_ucoins")
            builder.button(text="📢 Наш канал", url="https://t.me/Ucoins_news")
            builder.button(text="🔄 Проверить подписку", callback_data="sub_check_btn")
            builder.adjust(1, 1, 1)

            msg_text = (
                "❌ <b>ДОСТУП ОГРАНИЧЕН!</b>\n\n"
                "Чтобы использовать функции бота, вы обязательно должны быть подписаны на наши официальные ресурсы:\n\n"
                "💬 <b>Чат общения:</b> @Chat_ucoins\n"
                "📢 <b>Канал новостей:</b> @Ucoins_news\n\n"
                "<i>Подпишитесь на оба ресурса и нажмите кнопку ниже!</i>"
            )

            if isinstance(event, types.Message):
                await event.answer(msg_text, reply_markup=builder.as_markup())
            elif isinstance(event, types.CallbackQuery):
                await event.answer("⚠️ Доступ ограничен! Пожалуйста, подпишитесь на чат и канал.", show_alert=True)
            return

        return await handler(event, data)


def parse_amount(text: str, current_balance: int) -> int:
    text = text.strip().lower()
    if text in ["все", "вб", "vse", "vb"]:
        return int(current_balance)
    
    text = text.replace(" ", "").replace(",", ".")
    
    k_count = 0
    while text.endswith('к') or text.endswith('k'):
        k_count += 1
        text = text[:-1]
        
    multiplier = 1000 ** k_count
        
    try:
        val = Decimal(text) * Decimal(multiplier)
        final_val = int(val)
        if final_val > MAX_DB_INT:
            return MAX_DB_INT
        return final_val
    except (ValueError, InvalidOperation):
        return -1

def format_short_amount(amount: int) -> str:
    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return "0"
        
    is_negative = amount < 0
    abs_amount = abs(amount)
    if abs_amount == 0:
        return "0"
        
    suffixes = [""] + ["к" * i for i in range(1, 51)]
    tier = 0
    temp = abs_amount
    while temp >= 1000 and tier < len(suffixes) - 1:
        temp //= 1000
        tier += 1
        
    if tier == 0:
        res = str(abs_amount)
    else:
        divisor = Decimal(1000 ** tier)
        val = Decimal(abs_amount) / divisor
        if val == val.to_integral_value():
            res = f"{val:.0f}{suffixes[tier]}"
        else:
            res = f"{val:.2f}".rstrip('0').rstrip('.') + suffixes[tier]
        
    return f"-{res}" if is_negative else res

def get_mines_multiplier(total_mines, opened_count):
    if opened_count == 0: return 1.0
    try:
        ways_total = math.comb(25, opened_count)
        ways_safe = math.comb(25 - total_mines, opened_count)
        if ways_safe == 0: return 0
        factor = 0.96 if total_mines == 1 else 1.35
        return round((ways_total / ways_safe) * factor, 2)
    except Exception:
        return 1.0

def render_tower_text(current_level, bet, mines_count, next_win, current_win=0):
    rows = []
    for lvl in range(5, 0, -1):
        if lvl > current_level: rows.append(f"<b>Этаж {lvl}: ⬜ ⬜ ⬜ ⬜ ⬜</b>")
        elif lvl == current_level: rows.append(f"<b>Этаж {lvl}: ❓ ❓ ❓ ❓ ❓  ◀️</b>")
        else: rows.append(f"<b>Этаж {lvl}: ✅ ✅ ✅ ✅ ✅ (Пройден)</b>")
    return (
        f"🏰 <b>ИГРА: БАШНЯ</b>\n\n" + "\n".join(rows) + "\n\n"
        f"💰 <b>Ставка:</b> <b>{format_short_amount(bet)} Ucoin</b>\n"
        f"💣 <b>Мин на этаже:</b> <b>{mines_count}</b>\n"
        f"💵 <b>Текущий куш:</b> <b>{format_short_amount(current_win)} Ucoin</b>\n"
        f"📈 <b>Следующий шаг:</b> <b>+{format_short_amount(next_win)} Ucoin</b>"
    )

def render_gold_text(current_level, bet, next_win, current_win=0):
    rows = []
    for lvl in range(10, 0, -1):
        if lvl > current_level: rows.append(f"<b>Уровень {lvl}: ⬜ ⬜</b>")
        elif lvl == current_level: rows.append(f"<b>Уровень {lvl}: ❓ ❓  ◀️</b>")
        else: rows.append(f"<b>Уровень {lvl}: 🟡 🟡 (Пройден)</b>")
    return (
        f"👑 <b>ИГРА: ЗОЛОТО НАЦИИ (10 Уровней)</b>\n\n" + "\n".join(rows) + "\n\n"
        f"💰 <b>Ставка:</b> <b>{format_short_amount(bet)} Ucoin</b>\n"
        f"💵 <b>Текущий куш:</b> <b>{format_short_amount(current_win)} Ucoin</b>\n"
        f"📈 <b>Следующий шаг (x2):</b> <b>+{format_short_amount(next_win)} Ucoin</b>"
    )

def get_random_card_21():
    card = random.choice(list(CARDS_21.keys()))
    return card, CARDS_21[card]

@dp.callback_query(F.data == "void")
async def process_void_click(callback: types.CallbackQuery):
    await callback.answer()

@dp.callback_query(F.data == "sub_check_btn")
async def process_sub_check_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    is_subscribed = True
    for channel in ["@Chat_ucoins", "@Ucoins_news"]:
        try:
            member = await callback.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ["left", "kicked"]:
                is_subscribed = False
                break
        except Exception:
            is_subscribed = False
            break

    if is_subscribed:
        await callback.answer("✅ Подписка подтверждена! Доступ открыт.", show_alert=True)
        await callback.message.delete()
        user, is_new = await database.get_or_create_user(callback.from_user.id, callback.from_user.full_name)
        welcome_text = f"<b>🚀 С ВОЗВРАЩЕНИЕМ!</b>\n💰 Баланс: <b>{format_short_amount(user['balance'])} Ucoin</b>" if not is_new else f"<b>🚀 ПРИВЕТ! ТЕБЕ НАЧИСЛЕНО 1к СТАРТОВЫХ UCOIN!</b>"
        await callback.message.answer(f"{welcome_text}\n\n" + get_help_text())
    else:
        await callback.answer("❌ Проверка не пройдена! Вы подписались не на все ресурсы.", show_alert=True)

def get_help_text():
    return (
        f"<b>📋 ВСЕ НАШИ КОМАНДЫ:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 <b>БАНК / БАЛАНС / Б</b> — <b>Баланс кошелька</b>\n"
        f"👉 <b>ПРОФИЛЬ</b> — <b>Статистика аккаунта</b>\n"
        f"👉 <b>ТОП / /top</b> — <b>Топ-10 богачей бота</b> 🏆\n"
        f"👉 <b>БОНУС</b> — <b>Ежедневная халява (до 10кк)</b>\n"
        f"👉 <b>ПРОМО [код]</b> — <b>Активировать промокод</b>\n"
        f"👉 <b>ЗОЛОТО [ставка]</b> — <b>Золото 50/50 (10 уровней)</b> 🌟\n"
        f"👉 <b>БАШНЯ [ставка] [мины]</b> — <b>Запустить Башню (от 1 до 4 мин)</b>\n"
        f"👉 <b>МИНЫ [ставка] [мины]</b> — <b>Запустить Мины 5х5 (от 1 до 24 мин)</b>\n"
        f"👉 <b>21 [ставка]</b> — <b>Классическое Очко (21) на кнопках</b> 🃏\n"
        f"👉 <b>КРАШ [ставка] [икс]</b> — <b>Режим Краш ракеты</b> 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚔️ <b>PvP ДУЭЛИ (В ГРУППАХ):</b>\n"
        f"👉 <b>Кн [ставка]</b> — <b>Дуэль в Крестики-Нолики</b> ❌⭕\n"
        f"👉 <b>Куб [ставка]</b> — <b>Дуэль на Кубиках (через Reply 🎲)</b> 🎲\n"
        f"❌ <b>ОТМЕНА</b> — <b>Отменить созданную дуэль, пока никто не зашел</b>"
    )

# --- СИСТЕМА ПЕРЕВОДОВ В ГРУППАХ ---
@dp.message(
    F.chat.type.in_({"group", "supergroup"}), 
    F.reply_to_message,
    lambda msg: msg.text and msg.text.lower().strip().startswith(("дать ", "/give "))
)
async def handle_group_transfer(message: types.Message):
    if message.reply_to_message.from_user.is_bot: return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    
    sender, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    amount = parse_amount(parts[1], sender['balance'])
    
    if amount <= 0:
        await message.reply("<b>❌ Неверная сумма для перевода!</b>")
        return
    if sender['balance'] < amount:
        await message.reply(f"<b>❌ У вас нет такой суммы! Баланс: {format_short_amount(sender['balance'])} Ucoin</b>")
        return
        
    recipient, _ = await database.get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.full_name)
    await database.make_transfer(sender['user_id'], recipient['user_id'], amount)
    await message.reply(
        f"<b>💸 ПЕРЕВОД ВЫПОЛНЕН!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Отправитель:</b> <b>{html.escape(message.from_user.first_name)}</b>\n"
        f"👤 <b>Получатель:</b> <b>{html.escape(message.reply_to_message.from_user.first_name)}</b>\n"
        f"💰 <b>Сумма:</b> <b>{format_short_amount(amount)} Ucoin</b>"
    )

# --- ТОП ПО БАЛАНСАМ ---
@dp.message(lambda msg: msg.text and msg.text.lower() in ["топ", "/top", "топ 10"])
async def cmd_top_users(message: types.Message):
    try:
        top_list = await database.get_top_users(limit=10)
    except AttributeError:
        top_list = []

    if not top_list:
        await message.answer("<b>📊 Топ игроков пуст или функция не настроена в базе данных.</b>")
        return

    text = "🏆 <b>ТОП-10 ИГРОКОВ ПО БАЛАНСУ:</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, user in enumerate(top_list, 1):
        name = user.get('full_name') or user.get('username') or 'Игрок'
        balance = user.get('balance', 0)
        text += f"{idx}. <b>{html.escape(name)}</b> — <code>{format_short_amount(balance)}</code> Ucoin\n"
    
    await message.answer(text)

# --- АКТИВАЦИЯ ПРОМОКОДОВ ---
@dp.message(lambda msg: msg.text and (msg.text.lower().startswith("промо ") or msg.text.lower().startswith("/promo ")))
async def cmd_activate_promo(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат ввода: <code>промо [название]</code></b>")
        return
        
    promo_code = parts[1].strip()
    user, _ = await database.get_or_create_user(user_id=message.from_user.id, username=message.from_user.full_name)
    status, reward = await database.use_promocode(user['user_id'], promo_code)
    
    if status == "not_found":
        await message.answer("<b>❌ Такого промокода не существует!</b>")
    elif status == "no_activations":
        await message.answer("<b>📥 Активации этого промокода закончились!</b>")
    elif status == "already_used":
        await message.answer("<b>🚫 Вы уже активировали этот промокод!</b>")
    elif status == "success":
        await message.answer(f"<b>🎉 ПРОМОКОД АКТИВИРОВАН!</b>\n💰 Начислено: <b>+{format_short_amount(reward)} Ucoin</b>")

# --- АДМИН-ПАНЕЛЬ ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    builder = InlineKeyboardBuilder()
    builder.button(text="📈 Статистика", callback_data="adm_stats")
    builder.button(text="➕ Выдать баланс", callback_data="adm_give")
    builder.button(text="➖ Снять баланс", callback_data="adm_take")
    builder.button(text="🎫 Создать Промокод", callback_data="adm_promo")
    builder.adjust(1, 2, 1)
    await message.answer("<b>👑 ПАНЕЛЬ АДМИНИСТРАТОРА КАЗИНО</b>", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "adm_stats")
async def adm_view_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    stats = await database.get_global_stats()
    await callback.message.edit_text(
        f"<b>📊 ГЛОБАЛЬНАЯ СТАТИСТИКА БОТА:</b>\n\n"
        f"👥 <b>Всего игроков:</b> <b>{stats['total_users']}</b>\n"
        f"💰 <b>Всего коинов в обороте:</b> <b>{format_short_amount(stats['total_balance'])} Ucoin</b>"
    )

@dp.callback_query(F.data == "adm_promo")
async def adm_promo_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("<b>Введите кодовое слово промокода (например: BONUS2026):</b>")
    await state.set_state(AdminStates.waiting_for_promo_name)

@dp.message(AdminStates.waiting_for_promo_name)
async def adm_save_promo_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(promo_name=message.text.strip())
    await message.answer("<b>Какую сумму Ucoin будет давать промокод? (можно 50к, 1кк):</b>")
    await state.set_state(AdminStates.waiting_for_promo_reward)

@dp.message(AdminStates.waiting_for_promo_reward)
async def adm_save_promo_reward(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    reward = parse_amount(message.text, 0)
    if reward <= 0:
        await message.answer("Неверный формат суммы! Отмена.")
        await state.clear()
        return
    await state.update_data(promo_reward=reward)
    await message.answer("<b>Введите максимальное количество активаций:</b>")
    await state.set_state(AdminStates.waiting_for_promo_activations)

@dp.message(AdminStates.waiting_for_promo_activations)
async def adm_save_promo_activations(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        activations = int(message.text)
        if activations <= 0: raise ValueError
    except ValueError:
        await message.answer("Должно быть числом! Отмена.")
        await state.clear()
        return
    data = await state.get_data()
    await database.create_promocode(data['promo_name'], data['promo_reward'], activations)
    await message.answer(
        f"<b>🎫 ПРОМОКОД СОЗДАН!</b>\n\n"
        f"📌 <b>Код:</b> <code>{data['promo_name']}</code>\n"
        f"💰 <b>Награда:</b> {format_short_amount(data['promo_reward'])} Ucoin\n"
        f"👥 <b>Лимит активаций:</b> {activations}"
    )
    await state.clear()

@dp.callback_query(F.data == "adm_give")
async def adm_give_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("<b>Введите Telegram ID игрока для НАЧИСЛЕНИЯ:</b>")
    await state.set_state(AdminStates.waiting_for_give_id)

@dp.message(AdminStates.waiting_for_give_id)
async def adm_give_id(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        await state.update_data(target_id=int(message.text))
        await message.answer("<b>Какую сумму выдать? (Ограничений нет):</b>")
        await state.set_state(AdminStates.waiting_for_give_amount)
    except ValueError: await message.answer("ID должен состоять из цифр!")

@dp.message(AdminStates.waiting_for_give_amount)
async def adm_give_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    target_id = data['target_id']
    amount = parse_amount(message.text, MAX_DB_INT)
    if amount <= 0:
        await message.answer("Ошибка в изменении.")
        await state.clear()
        return
    success = await database.update_balance_admin(target_id, amount)
    if success: await message.answer(f"Успешно выдано +{format_short_amount(amount)} Ucoin")
    else: await message.answer("Пользователь не найден.")
    await state.clear()

@dp.callback_query(F.data == "adm_take")
async def adm_take_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("<b>Введите Telegram ID игрока для СПИСАНИЯ:</b>")
    await state.set_state(AdminStates.waiting_for_take_id)

@dp.message(AdminStates.waiting_for_take_id)
async def adm_take_id(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        await state.update_data(target_id=int(message.text))
        await message.answer("<b>Какую сумму списать? («все» — обнулить):</b>")
        await state.set_state(AdminStates.waiting_for_take_amount)
    except ValueError: await message.answer("ID должен быть числом.")

@dp.message(AdminStates.waiting_for_take_amount)
async def adm_take_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    target_id = data['target_id']
    target_user, _ = await database.get_or_create_user(target_id, "Игрок")
    amount = parse_amount(message.text, target_user['balance'])
    if amount <= 0:
        await message.answer("Ошибка в сумме.")
        await state.clear()
        return
    success = await database.update_balance_admin(target_id, -amount)
    if success: await message.answer(f"Списано -{format_short_amount(amount)} Ucoin")
    else: await message.answer("Пользователь не найден.")
    await state.clear()

# --- ОБЩИЕ ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user, is_new = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    welcome_text = f"<b>🚀 С ВОЗВРАЩЕНИЕМ!</b>\n💰 Баланс: <b>{format_short_amount(user['balance'])} Ucoin</b>" if not is_new else f"<b>🚀 ПРИВЕТ! ТЕБЕ НАЧИСЛЕНО 1к СТАРТОВЫХ UCOIN!</b>"
    await message.answer(f"{welcome_text}\n\n" + get_help_text())

@dp.message(lambda msg: msg.text and msg.text.lower() in ["бонус", "/bonus"])
async def get_daily_bonus(message: types.Message):
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    current_time = int(time.time())
    cooldown = 24 * 60 * 60
    if current_time - user['last_bonus'] < cooldown:
        time_left = cooldown - (current_time - user['last_bonus'])
        await message.answer(f"<b>⏳ ВЫ УЖЕ ЗАБИРАЛИ БОНУС! Приходите через: {time_left // 3600} ч. и {(time_left % 3600) // 60} мин.</b>")
        return
    bonus_amount = random.randint(300, 10000)
    await database.claim_bonus(message.from_user.id, bonus_amount)
    await message.answer(f"<b>🎁 БОНУС ПОЛУЧЕН! 🎉 Вы выиграли: +{format_short_amount(bonus_amount)} Ucoin!</b>")

@dp.message(lambda msg: msg.text and msg.text.lower() in ["баланс", "/balance", "б", "банк"])
async def check_balance(message: types.Message):
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(f"<b>💰 Ваш balance: {format_short_amount(user['balance'])} Ucoin</b>\n\n🎮 Всего проиграно: {format_short_amount(user['total_lost'])} Ucoin")

@dp.message(lambda msg: msg.text and msg.text.lower() in ["профиль", "/profile"])
async def check_profile(message: types.Message):
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"<b>👤 ЛИЧНЫЙ ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📝 НИКНЕЙМ: {html.escape(user['username'].upper())}</b>\n"
        f"<b>🆔 ТВОЙ ID: <code>{user['user_id']}</code></b>\n\n"
        f"<b>💰 БАЛАНС: {format_short_amount(user['balance'])} Ucoin</b>\n"
        f"<b>🎮 СЫГРАНО ИГР: {user['games_played']}</b>\n"
        f"<b>📉 ВСЕГО ПРОИГРАНО: {format_short_amount(user['total_lost'])} Ucoin</b>"
    )

# --- РЕЖИМ ЗОЛОТО ---
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("золото"))
async def start_gold(message: types.Message):
    user_id = message.from_user.id
    if (user_id, 'gold') in SOLO_GAMES:
        await message.answer("<b>❌ Вы уже играете в Золото! Завершите активную сессию.</b>")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат: <code>Золото [ставка]</code></b>")
        return

    user, _ = await database.get_or_create_user(user_id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    if bet <= 0 or user['balance'] < bet:
        await message.answer("<b>❌ Неверная ставка или недостаточно коинов!</b>")
        return

    await database.start_game_bet(user_id, bet)
    level_mine = random.randint(0, 1)
    next_win = int(bet * GOLD_MULTIPLIERS[0])

    SOLO_GAMES[(user_id, 'gold')] = {
        'bet': bet, 'current_level': 1, 'level_mine': level_mine, 'accumulated_win': 0
    }

    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Ячейка 1", callback_data=f"gd_step_0_{user_id}")
    builder.button(text="📦 Ячейка 2", callback_data=f"gd_step_1_{user_id}")
    builder.adjust(2)
    await message.answer(render_gold_text(1, bet, next_win), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("gd_step_"))
async def gold_turn(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    owner_id = int(parts[3])
    if callback.from_user.id != owner_id:
        await callback.answer("❌ Это не ваша игра!", show_alert=True)
        return
    if (owner_id, 'gold') not in SOLO_GAMES:
        await callback.message.edit_text("⚠️ Сессия игры истекла или завершена.", reply_markup=None)
        return

    await callback.answer()
    data = SOLO_GAMES[(owner_id, 'gold')]
    bet, current_level, level_mine = data['bet'], data['current_level'], data['level_mine']
    chosen_cell = int(parts[2])

    if chosen_cell == level_mine:
        await database.lose_game(owner_id, bet)
        SOLO_GAMES.pop((owner_id, 'gold'), None)
        await callback.message.edit_text(f"<b>💥 МИНА! Вы подорвались на {current_level}-м уровне Золота!</b>\n📉 Сгорело: {format_short_amount(bet)} Ucoin.", reply_markup=None)
        return

    current_win = int(bet * GOLD_MULTIPLIERS[current_level - 1])
    if current_level == 10:
        await database.win_game(owner_id, current_win)
        SOLO_GAMES.pop((owner_id, 'gold'), None)
        await callback.message.edit_text(f"<b>👑 НЕВЕРОЯТНО! ВЫ ПРОШЛИ ВСЕ 10 УРОВНЕЙ ЗОЛОТА! 🎉\n🏆 Выиграно: {format_short_amount(current_win)} Ucoin (x1024)!</b>", reply_markup=None)
        return

    next_level = current_level + 1
    next_win = int(bet * GOLD_MULTIPLIERS[next_level - 1])
    SOLO_GAMES[(owner_id, 'gold')].update({
        'current_level': next_level, 'level_mine': random.randint(0, 1), 'accumulated_win': current_win
    })

    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Ячейка 1", callback_data=f"gd_step_0_{owner_id}")
    builder.button(text="📦 Ячейка 2", callback_data=f"gd_step_1_{owner_id}")
    builder.button(text=f"💰 ЗАБРАТЬ {format_short_amount(current_win)} UCOIN", callback_data=f"gd_cashout_{owner_id}")
    builder.adjust(2, 1)
    await callback.message.edit_text(render_gold_text(next_level, bet, next_win, current_win), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("gd_cashout_"))
async def gold_cashout(callback: types.CallbackQuery):
    owner_id = int(callback.data.split("_")[2])
    if callback.from_user.id != owner_id: return
    if (owner_id, 'gold') not in SOLO_GAMES: return

    await callback.answer()
    data = SOLO_GAMES.pop((owner_id, 'gold'), {})
    win_sum = data.get('accumulated_win', 0)
    await database.win_game(owner_id, win_sum)
    await callback.message.edit_text(f"<b>💰 КЭШАУТ ЗОЛОТА! Забрано: {format_short_amount(win_sum)} Ucoin!</b>", reply_markup=None)

# --- РЕЖИМ МИНЫ ---
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("мины"))
async def start_mines(message: types.Message):
    user_id = message.from_user.id
    if (user_id, 'mines') in SOLO_GAMES:
        await message.answer("<b>❌ У вас уже запущен режим Мины!</b>")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат: <code>Мины [ставка] [мины]</code></b>")
        return

    user, _ = await database.get_or_create_user(user_id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    mines_count = 1
    if len(parts) == 3:
        try: mines_count = int(parts[2])
        except ValueError: pass

    if bet <= 0 or not (1 <= mines_count <= 24) or user['balance'] < bet:
        await message.answer("<b>❌ Неверные параметры игры или баланса!</b>")
        return

    await database.start_game_bet(user_id, bet)
    grid = [False] * 25
    for idx in random.sample(range(25), mines_count): grid[idx] = True

    SOLO_GAMES[(user_id, 'mines')] = {
        'bet': bet, 'mines_count': mines_count, 'grid': grid, 'revealed': [False] * 25, 'opened_count': 0
    }

    builder = InlineKeyboardBuilder()
    for i in range(25): builder.button(text="⬛", callback_data=f"mn_clk_{i}_{user_id}")
    builder.adjust(5)
    await message.answer(
        f"<b>💣 ИГРА: МИНЫ (Поле 5х5)</b>\n\n💵 Ставка: <b>{format_short_amount(bet)} Ucoin</b>\n💥 Мин: <b>{mines_count}</b>",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("mn_clk_"))
async def process_mines_click(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    owner_id = int(parts[3])
    if callback.from_user.id != owner_id: return
    if (owner_id, 'mines') not in SOLO_GAMES: return

    await callback.answer()
    data = SOLO_GAMES[(owner_id, 'mines')]
    cell_idx = int(parts[2])
    bet, mines_count, grid, revealed, opened_count = data['bet'], data['mines_count'], data['grid'], data['revealed'], data['opened_count']

    if revealed[cell_idx]: return
    revealed[cell_idx] = True

    if grid[cell_idx]:
        await database.lose_game(owner_id, bet)
        SOLO_GAMES.pop((owner_id, 'mines'), None)
        builder = InlineKeyboardBuilder()
        for i in range(25): builder.button(text="💥" if i == cell_idx else ("💣" if grid[i] else "💎"), callback_data="void")
        builder.adjust(5)
        await callback.message.edit_text(f"<b>💥 БУМ! ВЫ ПОДОРВАЛИСЬ!</b>\n📉 Проиграно: <b>{format_short_amount(bet)} Ucoin</b>", reply_markup=builder.as_markup())
        return

    opened_count += 1
    mult = get_mines_multiplier(mines_count, opened_count)
    current_win = int(bet * mult)
    SOLO_GAMES[(owner_id, 'mines')].update({'revealed': revealed, 'opened_count': opened_count})

    if opened_count == (25 - mines_count):
        await database.win_game(owner_id, current_win)
        SOLO_GAMES.pop((owner_id, 'mines'), None)
        builder = InlineKeyboardBuilder()
        for i in range(25): builder.button(text="💣" if grid[i] else "💎", callback_data="void")
        builder.adjust(5)
        await callback.message.edit_text(f"<b>🏆 ЧИСТАЯ ПОБЕДА! 🎉 Выигрыш: {format_short_amount(current_win)} Ucoin ({mult}x)!</b>", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    for i in range(25): builder.button(text="💎" if revealed[i] else "⬛", callback_data=f"mn_clk_{i}_{owner_id}")
    builder.button(text=f"💰 ЗАБРАТЬ {format_short_amount(current_win)} UCOIN", callback_data=f"mn_cashout_{owner_id}")
    builder.adjust(5, 5, 5, 5, 5, 1)

    await callback.message.edit_text(
        f"<b>💣 ИГРА: МИНЫ</b>\n\n💰 Ставка: {format_short_amount(bet)}\n📈 Множитель: {mult}x ({format_short_amount(current_win)} Ucoin)",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("mn_cashout_"))
async def process_mines_cashout(callback: types.CallbackQuery):
    owner_id = int(callback.data.split("_")[2])
    if callback.from_user.id != owner_id: return
    if (owner_id, 'mines') not in SOLO_GAMES: return

    await callback.answer()
    data = SOLO_GAMES.pop((owner_id, 'mines'), {})
    mult = get_mines_multiplier(data['mines_count'], data['opened_count'])
    current_win = int(data['bet'] * mult)
    await database.win_game(owner_id, current_win)
    await callback.message.edit_text(f"<b>💰 КЭШАУТ! Забрано: {format_short_amount(current_win)} Ucoin ({mult}x)</b>", reply_markup=None)


# --- РЕЖИМ БАШНЯ ---
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("башня"))
async def start_tower(message: types.Message):
    user_id = message.from_user.id
    if (user_id, 'tower') in SOLO_GAMES:
        await message.answer("<b>❌ Игра Башня уже идет!</b>")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат: <code>Башня [ставка] [мины]</code></b>")
        return

    user, _ = await database.get_or_create_user(user_id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    mines_count = 1
    if len(parts) == 3:
        try: mines_count = int(parts[2])
        except ValueError: pass

    if bet <= 0 or not (1 <= mines_count <= 4) or user['balance'] < bet:
        await message.answer("<b>❌ Мины в башне выставляются от 1 до 4!</b>")
        return

    await database.start_game_bet(user_id, bet)
    current_mines = [False] * 5
    for idx in random.sample(range(5), mines_count): current_mines[idx] = True
    next_win = int(bet * TOWER_MULTIPLIERS[mines_count][0])

    SOLO_GAMES[(user_id, 'tower')] = {
        'bet': bet, 'mines_count': mines_count, 'current_level': 1, 'current_mines': current_mines, 'accumulated_win': 0
    }

    builder = InlineKeyboardBuilder()
    for i in range(1, 6): builder.button(text=f"📦 Клетка {i}", callback_data=f"tw_step_{i-1}_{user_id}")
    builder.adjust(5)
    await message.answer(render_tower_text(1, bet, mines_count, next_win), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("tw_step_"))
async def tower_turn(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    owner_id = int(parts[3])
    if callback.from_user.id != owner_id: return
    if (owner_id, 'tower') not in SOLO_GAMES: return

    await callback.answer()
    data = SOLO_GAMES[(owner_id, 'tower')]
    bet, mines_count, current_level, current_mines = data['bet'], data['mines_count'], data['current_level'], data['current_mines']
    chosen_cell = int(parts[2])

    if current_mines[chosen_cell]:
        await database.lose_game(owner_id, bet)
        SOLO_GAMES.pop((owner_id, 'tower'), None)
        await callback.message.edit_text(f"<b>💥 МИНА НА {current_level}-М ЭТАЖЕ!</b>\n📉 Потеряно: {format_short_amount(bet)} Ucoin.", reply_markup=None)
        return

    current_win = int(bet * TOWER_MULTIPLIERS[mines_count][current_level - 1])
    if current_level == 5:
        await database.win_game(owner_id, current_win)
        SOLO_GAMES.pop((owner_id, 'tower'), None)
        await callback.message.edit_text(f"<b>🏆 БАШНЯ СНЕСЕНА! Выигрыш: {format_short_amount(current_win)} Ucoin!</b>", reply_markup=None)
        return

    next_level = current_level + 1
    next_win = int(bet * TOWER_MULTIPLIERS[mines_count][next_level - 1])
    new_mines = [False] * 5
    for idx in random.sample(range(5), mines_count): new_mines[idx] = True

    SOLO_GAMES[(owner_id, 'tower')].update({
        'current_level': next_level, 'current_mines': new_mines, 'accumulated_win': current_win
    })

    builder = InlineKeyboardBuilder()
    for i in range(1, 6): builder.button(text=f"📦 Клетка {i}", callback_data=f"tw_step_{i-1}_{owner_id}")
    builder.button(text=f"💰 ЗАБРАТЬ {format_short_amount(current_win)} UCOIN", callback_data=f"tw_cashout_{owner_id}")
    builder.adjust(5, 1)
    await callback.message.edit_text(render_tower_text(next_level, bet, mines_count, next_win, current_win), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("tw_cashout_"))
async def tower_cashout(callback: types.CallbackQuery):
    owner_id = int(callback.data.split("_")[2])
    if callback.from_user.id != owner_id: return
    if (owner_id, 'tower') not in SOLO_GAMES: return

    await callback.answer()
    data = SOLO_GAMES.pop((owner_id, 'tower'), {})
    win_sum = data.get('accumulated_win', 0)
    await database.win_game(owner_id, win_sum)
    await callback.message.edit_text(f"<b>💰 ЗАБРАНО {format_short_amount(win_sum)} UCOIN!</b>", reply_markup=None)


# --- РЕЖИМ КРАШ (CRASH) ---
@dp.message(lambda msg: msg.text and (msg.text.lower().startswith("краш") or msg.text.lower().startswith("/crash")))
async def start_crash(message: types.Message):
    user_id = message.from_user.id
    if (user_id, 'crash') in SOLO_GAMES:
        await message.answer("<b>❌ У вас уже летит одна ракета!</b>")
        return

    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("<b>⚠️ Формат: <code>Краш [ставка] [икс]</code></b>")
        return

    user, _ = await database.get_or_create_user(user_id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    if bet <= 0 or user['balance'] < bet:
        await message.answer("<b>❌ Недостаточно средств или неверная ставка!</b>")
        return

    try:
        target_x = float(parts[2].replace(",", "."))
        if target_x <= 1.0: raise ValueError
    except ValueError:
        await message.answer("<b>❌ Икс должен быть больше 1.0!</b>")
        return

    SOLO_GAMES[(user_id, 'crash')] = True
    await database.start_game_bet(user_id, bet)

    crash_point = 1.0 if random.random() < 0.12 else round(1.01 / (random.uniform(0.01, 1.0)), 2)
    if crash_point > 30.0: crash_point = round(random.uniform(5.0, 30.0), 2)

    status_msg = await message.answer("🚀 <b>Ракета взлетает... Набираем высоту!</b>")
    await asyncio.sleep(1)

    animation_steps = [1.0]
    if crash_point > 1.3: animation_steps.append(round(crash_point * 0.4, 2))
    if crash_point > 1.7: animation_steps.append(round(crash_point * 0.7, 2))

    for x_step in animation_steps:
        if x_step >= target_x: break
        try:
            await status_msg.edit_text(f"🚀 <b>Ракета летит... 📈 Множитель: {x_step:.2f}x</b>")
            await asyncio.sleep(0.7)
        except Exception: pass

    if crash_point >= target_x:
        win_amount = int(bet * target_x)
        await database.win_game(user_id, win_amount)
        await status_msg.edit_text(
            f"<b>💰 РАКЕТА УСПЕШНО ДОЛЕТЕЛА!</b>\n🎯 Твой забор: <code>{target_x}x</code>\n🎉 Выигрыш: <b>{format_short_amount(win_amount)} Ucoin!</b>"
        )
    else:
        await database.lose_game(user_id, bet)
        await status_msg.edit_text(
            f"<b>💥 БУУУМ! РАКЕТА ВЗОРВАЛАСЬ! (КРАШ)</b>\n💥 Точка взрыва: <b>{crash_point}x</b>\n❌ Потеряно: <b>{format_short_amount(bet)} Ucoin</b>."
        )
    SOLO_GAMES.pop((user_id, 'crash'), None)


# --- РЕЖИМ 21 ОЧКО ---
@dp.message(lambda msg: msg.text and (msg.text.lower().startswith("21") or msg.text.lower().startswith("/21")))
async def start_game_21(message: types.Message):
    user_id = message.from_user.id
    if (user_id, 'bj') in SOLO_GAMES:
        await message.answer("<b>❌ Вы уже играете в 21 очко!</b>")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат: <code>21 [ставка]</code></b>")
        return

    user, _ = await database.get_or_create_user(user_id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    if bet <= 0 or user['balance'] < bet:
        await message.answer("<b>❌ Ошибка ставки.</b>")
        return

    await database.start_game_bet(user_id, bet)
    p1_card, p1_val = get_random_card_21()
    p2_card, p2_val = get_random_card_21()
    d1_card, d1_val = get_random_card_21()

    player_cards, player_score = [p1_card, p2_card], p1_val + p2_val
    dealer_cards, dealer_score = [d1_card], d1_val

    if p1_val == 11 and p2_val == 11: player_score = 21

    if player_score == 21:
        win_amount = int(bet * 2)
        await database.win_game(user_id, win_amount)
        await message.answer(f"<b>👑 ЗОЛОТОЕ ОЧКО! Сразу 21!</b>\n🎉 Выиграно: <b>{format_short_amount(win_amount)} Ucoin!</b>")
        return

    SOLO_GAMES[(user_id, 'bj')] = {
        'bet': bet, 'player_cards': player_cards, 'player_score': player_score, 'dealer_cards': dealer_cards, 'dealer_score': dealer_score
    }

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Еще карту", callback_data=f"bj_hit_{user_id}")
    builder.button(text="🛑 Стоп", callback_data=f"bj_stop_{user_id}")
    builder.adjust(2)

    await message.answer(
        f"<b>🃏 ИГРА: 21 ОЧКО</b>\n💰 Ставка: {format_short_amount(bet)}\n🫵 Твои карты: {', '.join(player_cards)} (<b>{player_score}</b>)\n🤖 Дилер: {', '.join(dealer_cards)} (<b>{dealer_score}</b>)",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("bj_hit_"))
async def blackjack_hit_callback(callback: types.CallbackQuery):
    owner_id = int(callback.data.split("_")[2])
    if callback.from_user.id != owner_id: return
    if (owner_id, 'bj') not in SOLO_GAMES: return

    await callback.answer()
    data = SOLO_GAMES[(owner_id, 'bj')]
    bet, player_cards, player_score, dealer_cards, dealer_score = data['bet'], data['player_cards'], data['player_score'], data['dealer_cards'], data['dealer_score']

    card, val = get_random_card_21()
    player_cards.append(card)
    player_score += val
    SOLO_GAMES[(owner_id, 'bj')].update({'player_cards': player_cards, 'player_score': player_score})

    if player_score > 21:
        await database.lose_game(owner_id, bet)
        SOLO_GAMES.pop((owner_id, 'bj'), None)
        await callback.message.edit_text(f"<b>💥 ПЕРЕБОР! У вас {player_score} очков.</b>\n❌ Сгорело: <b>{format_short_amount(bet)} Ucoin</b>", reply_markup=None)
    elif player_score == 21:
        win_amount = int(bet * 2)
        await database.win_game(owner_id, win_amount)
        SOLO_GAMES.pop((owner_id, 'bj'), None)
        await callback.message.edit_text(f"<b>👑 21 ОЧКО! Победа!</b>\n🎉 Куш: <b>{format_short_amount(win_amount)} Ucoin</b>", reply_markup=None)
    else:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Еще карту", callback_data=f"bj_hit_{owner_id}")
        builder.button(text="🛑 Стоп", callback_data=f"bj_stop_{owner_id}")
        builder.adjust(2)
        await callback.message.edit_text(
            f"<b>🃏 ИГРА: 21 ОЧКО</b>\n🫵 Твои: {', '.join(player_cards)} (<b>{player_score}</b>)\n🤖 Дилер: {dealer_score}", reply_markup=builder.as_markup()
        )

@dp.callback_query(F.data.startswith("bj_stop_"))
async def blackjack_stop_callback(callback: types.CallbackQuery):
    owner_id = int(callback.data.split("_")[2])
    if callback.from_user.id != owner_id: return
    if (owner_id, 'bj') not in SOLO_GAMES: return

    await callback.answer()
    data = SOLO_GAMES.pop((owner_id, 'bj'), {})
    bet, player_cards, player_score, dealer_cards, dealer_score = data['bet'], data['player_cards'], data['player_score'], data['dealer_cards'], data['dealer_score']

    while dealer_score < 17:
        card, val = get_random_card_21()
        dealer_cards.append(card)
        dealer_score += val

    if dealer_score > 21:
        win_amount = int(bet * 2)
        await database.win_game(owner_id, win_amount)
        res = f"<b>💰 У Дилера перебор ({dealer_score})!</b>\n🎉 Выиграно: {format_short_amount(win_amount)}"
    elif player_score > dealer_score:
        win_amount = int(bet * 2)
        await database.win_game(owner_id, win_amount)
        res = f"<b>💰 Победа по очкам! ({player_score} vs {dealer_score})</b>\n🎉 Выиграно: {format_short_amount(win_amount)}"
    elif player_score < dealer_score:
        await database.lose_game(owner_id, bet)
        res = f"<b>❌ Проигрыш! У дилера {dealer_score} очков.</b>"
    else:
        await database.win_game(owner_id, bet)
        res = f"<b>🤝 Ничья! Ставка возвращена.</b>"

    await callback.message.edit_text(f"<b>👑 ФИНАЛ: 21 ОЧКО</b>\n🫵 Вы: {player_score} | 🤖 Дилер: {dealer_score}\n\n{res}", reply_markup=None)


# =====================================================================
# --- PvP РЕЖИМ 1: КРЕСТИКИ-НОЛИКИ (Кн) ---
# =====================================================================
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("кн"))
async def create_duel_ttt(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат в чате: <code>Кн [ставка]</code> (Пример: Кн 500)</b>")
        return

    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    if bet <= 0 or user['balance'] < bet:
        await message.answer("<b>❌ Ошибка ставки или недостаточно баланса!</b>")
        return

    # Списываем баланс СРАЗУ при создании дуэли
    await database.start_game_bet(message.from_user.id, bet)

    # Генерация случайного уникального ID дуэли для предотвращения багов cross-chat'а
    duel_id = random.randint(100000, 999999)
    while duel_id in DUELS:
        duel_id = random.randint(100000, 999999)

    DUELS[duel_id] = {
        'type': 'ttt', 'bet': bet, 'creator': message.from_user.id, 'creator_name': message.from_user.first_name,
        'opponent': None, 'opponent_name': None, 'board': [' '] * 9, 'turn': message.from_user.id, 'status': 'open',
        'chat_id': message.chat.id, 'message_id': None
    }

    builder = InlineKeyboardBuilder()
    builder.button(text="⚔️ Принять Дуэль", callback_data=f"ttj_{duel_id}")
    
    sent_msg = await message.reply(
        f"⚔️ <b>PvP ДУЭЛЬ: КРЕСТИКИ-НОЛИКИ!</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Ставка: <b>{format_short_amount(bet)} Ucoin</b>\n"
        f"👤 Создатель: {html.escape(message.from_user.first_name)}\n\n"
        f"<i>Ждем оппонента... Жми кнопку ниже!</i>",
        reply_markup=builder.as_markup()
    )
    DUELS[duel_id]['message_id'] = sent_msg.message_id

@dp.callback_query(F.data.startswith("ttj_"))
async def join_duel_ttt(callback: types.CallbackQuery):
    duel_id = int(callback.data.split("_")[1])
    if duel_id not in DUELS or DUELS[duel_id]['status'] != 'open':
        await callback.answer("⚠️ Лобби уже недоступно или заполнено.", show_alert=True)
        return

    duel = DUELS[duel_id]
    user_id = callback.from_user.id
    if user_id == duel['creator']:
        await callback.answer("❌ Нельзя играть против самого себя!", show_alert=True)
        return

    user, _ = await database.get_or_create_user(user_id, callback.from_user.full_name)
    if user['balance'] < duel['bet']:
        await callback.answer("❌ У вас недостаточно Ucoin для принятия ставки!", show_alert=True)
        return

    await callback.answer("Дуэль принята!")
    await database.start_game_bet(user_id, duel['bet'])

    duel.update({
        'opponent': user_id, 'opponent_name': callback.from_user.first_name, 'status': 'playing'
    })

    await edit_ttt_board(callback.message, duel_id)

def check_ttt_winner(b):
    lines = [[0,1,2], [3,4,5], [6,7,8], [0,3,6], [1,4,7], [2,5,8], [0,4,8], [2,4,6]]
    for l in lines:
        if b[l[0]] != ' ' and b[l[0]] == b[l[1]] == b[l[2]]:
            return b[l[0]]
    if ' ' not in b: return 'Ничья'
    return None

async def edit_ttt_board(message: types.Message, duel_id: int):
    duel = DUELS.get(duel_id)
    if not duel: return

    builder = InlineKeyboardBuilder()
    for i in range(9):
        char = duel['board'][i]
        btn_text = "⬛" if char == ' ' else char
        builder.button(text=btn_text, callback_data=f"tt_turn_{i}_{duel_id}")
    builder.adjust(3)

    cur_turn_name = html.escape(duel['creator_name']) if duel['turn'] == duel['creator'] else html.escape(duel['opponent_name'])
    await message.edit_text(
        f"❌⭕ <b>ИГРА: КРЕСТИКИ-НОЛИКИ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Ставка: <b>{format_short_amount(duel['bet'])} Ucoin</b>\n"
        f"❌ <b>{html.escape(duel['creator_name'])}</b> vs ⭕ <b>{html.escape(duel['opponent_name'])}</b>\n\n"
        f"👉 Сейчас ходит: <b>{cur_turn_name}</b>",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("tt_turn_"))
async def process_ttt_turn(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    cell, duel_id = int(parts[2]), int(parts[3])
    
    if duel_id not in DUELS or DUELS[duel_id]['status'] != 'playing': return
    duel = DUELS[duel_id]
    user_id = callback.from_user.id

    if user_id != duel['turn']:
        await callback.answer("⚠️ Не твой ход! Ожидай.", show_alert=True)
        return

    if duel['board'][cell] != ' ':
        await callback.answer("❌ Эта ячейка уже занята!", show_alert=True)
        return

    await callback.answer()
    duel['board'][cell] = '❌' if user_id == duel['creator'] else '⭕'
    
    result = check_ttt_winner(duel['board'])
    if result:
        bet = duel['bet']
        if result == 'Ничья':
            await database.win_game(duel['creator'], bet)
            await database.win_game(duel['opponent'], bet)
            await callback.message.edit_text(f"🤝 <b>НИЧЬЯ В КРЕСТИКИ-НОЛИКИ!</b>\nВсе клетки заполнены, коины возвращены игрокам.", reply_markup=None)
        else:
            winner_id = duel['creator'] if result == '❌' else duel['opponent']
            winner_name = html.escape(duel['creator_name'] if result == '❌' else duel['opponent_name'])
            await database.win_game(winner_id, bet * 2)
            await callback.message.edit_text(f"🏆 <b>ПОБЕДА В ДУЭЛИ!</b>\n━━━━━━━━━━━━━━━━━━━━━━━\nИгрок <b>{winner_name}</b> выиграл <b>{format_short_amount(bet * 2)} Ucoin!</b>", reply_markup=None)
        DUELS.pop(duel_id, None)
        return

    # Переключаем ход
    duel['turn'] = duel['opponent'] if user_id == duel['creator'] else duel['creator']
    await edit_ttt_board(callback.message, duel_id)


# =====================================================================
# --- PvP РЕЖИМ 2: ДУЭЛЬ НА КУБИКАХ (Куб) ---
# =====================================================================
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("куб"))
async def create_duel_dice(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат в чате: <code>Куб [ставка]</code> (Пример: Куб 10к)</b>")
        return

    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    if bet <= 0 or user['balance'] < bet:
        await message.answer("<b>❌ Недостаточно средств для создания дуэли!</b>")
        return

    # Списываем баланс СРАЗУ при создании дуэли
    await database.start_game_bet(message.from_user.id, bet)

    duel_id = random.randint(100000, 999999)
    while duel_id in DUELS:
        duel_id = random.randint(100000, 999999)

    DUELS[duel_id] = {
        'type': 'dice', 'bet': bet, 'creator': message.from_user.id, 'creator_name': message.from_user.first_name,
        'opponent': None, 'opponent_name': None, 'creator_roll': None, 'opponent_roll': None, 'status': 'open',
        'chat_id': message.chat.id, 'message_id': None
    }

    builder = InlineKeyboardBuilder()
    builder.button(text="🎲 Принять дуэль кубов", callback_data=f"dcj_{duel_id}")
    sent_msg = await message.reply(
        f"🎲 <b>PvP ДУЭЛЬ: КУБИКИ!</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Ставка: <b>{format_short_amount(bet)} Ucoin</b>\n"
        f"👤 Создатель: {html.escape(message.from_user.first_name)}\n\n"
        f"💬 <b>ПРАВИЛА ИГРЫ:</b>\n"
        f"1. Оппонент жмет кнопку ниже для входа.\n"
        f"2. <b>ОБА</b> игрока делают бросок 🎲 (Reply кубиком) на это сообщение!\n"
        f"3. Бот считывает очки и выдает x2 победителю!",
        reply_markup=builder.as_markup()
    )
    DUELS[duel_id]['message_id'] = sent_msg.message_id

@dp.callback_query(F.data.startswith("dcj_"))
async def join_duel_dice(callback: types.CallbackQuery):
    duel_id = int(callback.data.split("_")[1])
    if duel_id not in DUELS or DUELS[duel_id]['status'] != 'open':
        await callback.answer("⚠️ Эта дуэль кубов уже занята или неактивна.", show_alert=True)
        return

    duel = DUELS[duel_id]
    user_id = callback.from_user.id
    if user_id == duel['creator']:
        await callback.answer("❌ Вы создатель!", show_alert=True)
        return

    user, _ = await database.get_or_create_user(user_id, callback.from_user.full_name)
    if user['balance'] < duel['bet']:
        await callback.answer("❌ Нет денег для входа!", show_alert=True)
        return

    await callback.answer("Вы зашли в дуэль кубов!")
    await database.start_game_bet(user_id, duel['bet'])
    
    duel.update({
        'opponent': user_id, 'opponent_name': callback.from_user.first_name, 'status': 'playing'
    })

    await callback.message.edit_text(
        f"🎲 <b>ДУЭЛЬ В КУБЫ (ИДЁТ СБОР БРОСКОВ)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Ставка: {format_short_amount(duel['bet'])} Ucoin\n"
        f"⚔️ Игроки: <b>{html.escape(duel['creator_name'])}</b> и <b>{html.escape(duel['opponent_name'])}</b>\n\n"
        f"👉 <b>ОБОИМ игрокам нужно отправить эмодзи 🎲 ответом (REPLY) на это сообщение!</b>",
        reply_markup=None
    )

@dp.message(F.dice, F.reply_to_message)
async def handle_pvp_dice_rolls(message: types.Message):
    if message.dice.emoji != "🎲": return
    target_msg_id = message.reply_to_message.message_id
    chat_id = message.chat.id

    # Находим игру по chat_id и message_id родительского сообщения бота
    found_duel_id = None
    for d_id, d_data in DUELS.items():
        if d_data['chat_id'] == chat_id and d_data['message_id'] == target_msg_id:
            found_duel_id = d_id
            break

    if found_duel_id is None: return
    duel = DUELS[found_duel_id]
    if duel['type'] != 'dice' or duel['status'] != 'playing': return

    user_id = message.from_user.id
    val = message.dice.value  # Бот успешно считывает, сколько выпало на кубике

    if user_id == duel['creator']:
        if duel['creator_roll'] is not None:
            await message.reply("⚠️ Вы уже сделали свой бросок!")
            return
        duel['creator_roll'] = val
    elif user_id == duel['opponent']:
        if duel['opponent_roll'] is not None:
            await message.reply("⚠️ Оппонент уже сделал свой бросок!")
            return
        duel['opponent_roll'] = val
    else:
        return

    # Если оба броска собраны — подводим результаты
    if duel['creator_roll'] is not None and duel['opponent_roll'] is not None:
        # Меняем статус немедленно, чтобы второй параллельный поток прервался
        if duel['status'] != 'playing':
            return
        duel['status'] = 'finished'
        
        await asyncio.sleep(2.5)  # Даем время анимации кубика докрутиться
        
        # Забираем сессию из ОЗУ сразу, защищаясь от дублирования выплат
        if found_duel_id not in DUELS: return
        duel_data = DUELS.pop(found_duel_id, None)
        if not duel_data: return
        
        c_score = duel_data['creator_roll']
        o_score = duel_data['opponent_roll']
        bet = duel_data['bet']
        
        c_name = html.escape(duel_data['creator_name'])
        o_name = html.escape(duel_data['opponent_name'])
        
        # Форматирование и определение победителя по правилу "у кого больше — тот и вин"
        if c_score > o_score:
            await database.win_game(duel_data['creator'], bet * 2)
            res_text = (
                f"🏆 В дуэли кубов побеждает <b>{c_name} ({c_score})</b>!\n"
                f"🎲 Броски игроков: <b>{c_name} ({c_score})</b> vs <b>{o_name} ({o_score})</b>\n"
                f"📈 У кого выпало число больше — тот забрал весь банк!\n"
                f"🎉 Выигрыш: <b>{format_short_amount(bet * 2)} Ucoin</b>"
            )
        elif o_score > c_score:
            await database.win_game(duel_data['opponent'], bet * 2)
            res_text = (
                f"🏆 В дуэли кубов побеждает <b>{o_name} ({o_score})</b>!\n"
                f"🎲 Броски игроков: <b>{c_name} ({c_score})</b> vs <b>{o_name} ({o_score})</b>\n"
                f"📈 У кого выпало число больше — тот забрал весь банк!\n"
                f"🎉 Выигрыш: <b>{format_short_amount(bet * 2)} Ucoin</b>"
            )
        else:
            await database.win_game(duel_data['creator'], bet)
            await database.win_game(duel_data['opponent'], bet)
            res_text = (
                f"🤝 <b>Ничья!</b> У игроков выпало одинаково по <b>{c_score}</b>!\n"
                f"🎲 Броски игроков: <b>{c_name} ({c_score})</b> vs <b>{o_name} ({o_score})</b>\n"
                f"💰 Ставки возвращены обоим участникам на баланс."
            )

        await bot.send_message(
            chat_id=message.chat.id,
            text=f"📊 <b>ИТОГИ PvP ДУЭЛИ В КУБЫ:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{res_text}",
            reply_to_message_id=target_msg_id
        )


# =====================================================================
# --- СИСТЕМА УМНОЙ ОТМЕНЫ ДУЭЛЕЙ ---
# =====================================================================
@dp.message(lambda msg: msg.text and msg.text.lower().strip() in ["отмена", "/отмена"])
async def cancel_duel_handler(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    found_duel_id = None

    # Сценарий 1: Пользователь сделал REPLY на сообщение дуэли
    if message.reply_to_message:
        target_msg_id = message.reply_to_message.message_id
        for d_id, d_data in DUELS.items():
            if d_data['chat_id'] == chat_id and d_data['message_id'] == target_msg_id:
                found_duel_id = d_id
                break

    # Сценарий 2: Пользователь просто написал отмена в чат (ищем его открытое лобби в этой группе)
    if found_duel_id is None:
        for d_id, d_data in DUELS.items():
            if d_data['chat_id'] == chat_id and d_data['creator'] == user_id and d_data['status'] == 'open':
                found_duel_id = d_id
                break

    if found_duel_id is None: return

    duel = DUELS[found_duel_id]

    if duel['creator'] != user_id:
        await message.reply("<b>❌ Вы не являетесь создателем этой дуэли!</b>")
        return

    if duel['status'] != 'open':
        await message.reply("<b>❌ Дуэль уже идет или завершена, отмена невозможна!</b>")
        return

    # Возврат коинов создателю на баланс
    await database.win_game(user_id, duel['bet'])
    DUELS.pop(found_duel_id, None)

    if duel['message_id']:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=duel['message_id'],
                text=f"❌ <b>Дуэль отменена создателем ({html.escape(duel['creator_name'])}).</b>\n💰 Ставка <b>{format_short_amount(duel['bet'])} Ucoin</b> возвращена на баланс.",
                reply_markup=None
            )
        except Exception: pass

    try: await message.delete()
    except Exception: pass


async def main():
    await database.init_db()
    dp.message.outer_middleware(SubscriptionMiddleware())
    dp.callback_query.outer_middleware(SubscriptionMiddleware())
    
    await bot.set_my_commands([
        types.BotCommand(command="start", description="Главное меню"),
        types.BotCommand(command="balance", description="Мой баланс"),
        types.BotCommand(command="profile", description="Мой профиль"),
        types.BotCommand(command="top", description="Топ игроков"),
        types.BotCommand(command="bonus", description="Взять бонус"),
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
