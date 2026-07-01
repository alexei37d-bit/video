# bot.py
import os
import math
import random
import time
import logging
from aiogram import Bot, Dispatcher, types, F
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

TOWER_MULTIPLIERS = {
    1: [1.15, 1.40, 1.75, 2.20, 2.80],
    2: [2.50, 5.80, 14.00, 35.00, 90.00],
    3: [5.00, 22.00, 95.00, 450.00, 2500.00],
    4: [15.00, 120.00, 1100.00, 9500.00, 85000.00]
}

class GameStates(StatesGroup):
    playing_tower = State()
    playing_mines = State()

class AdminStates(StatesGroup):
    waiting_for_give_id = State()
    waiting_for_give_amount = State()
    waiting_for_take_id = State()
    waiting_for_take_amount = State()
    # Состояния для создания промокодов
    waiting_for_promo_name = State()
    waiting_for_promo_reward = State()
    waiting_for_promo_activations = State()

# Преобразование букв в числа (1кк -> 1000000)
def parse_amount(text: str, current_balance: int) -> int:
    text = text.strip().lower()
    if text in ["все", "вб", "vse", "vb"]:
        return current_balance
    
    text = text.replace(" ", "").replace(",", ".")
    multiplier = 1
    
    if "ккк" in text:
        multiplier = 1_000_000_000
        text = text.replace("ккк", "")
    elif "кк" in text:
        multiplier = 1_000_000
        text = text.replace("кк", "")
    elif "к" in text:
        multiplier = 1_000
        text = text.replace("к", "")
        
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return -1

# УМНЫЙ КОНВЕРТЕР ЧИСЕЛ В БУКВЫ (Для отображения баланса: 800000000 -> 800кк)
def format_short_amount(amount: int) -> str:
    if amount >= 1_000_000_000:
        val = amount / 1_000_000_000
        return f"{int(val) if val.is_integer() else round(val, 2)}ккк"
    elif amount >= 1_000_000:
        val = amount / 1_000_000
        return f"{int(val) if val.is_integer() else round(val, 2)}кк"
    elif amount >= 1_000:
        val = amount / 1_000
        return f"{int(val) if val.is_integer() else round(val, 2)}к"
    return str(amount)

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
        f"💰 <b>Ставка:</b> <b>{bet:,} Ucoin ({format_short_amount(bet)})</b>\n"
        f"💣 <b>Мин на этаже:</b> <b>{mines_count}</b>\n"
        f"💵 <b>Текущий куш:</b> <b>{current_win:,} Ucoin ({format_short_amount(current_win)})</b>\n"
        f"📈 <b>Следующий шаг:</b> <b>+{next_win:,} Ucoin</b>"
    ).replace(",", " ")

# --- СИСТЕМА ПЕРЕВОДОВ В ГРУППАХ ---
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message)
async def handle_group_transfer(message: types.Message):
    if message.reply_to_message.from_user.is_bot: return
    text = message.text.lower().strip() if message.text else ""
    if not (text.startswith("дать ") or text.startswith("/give ")): return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    
    sender, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    amount = parse_amount(parts[1], sender['balance'])
    
    if amount <= 0:
        await message.reply("<b>❌ Неверная сумма для перевода!</b>")
        return
    if sender['balance'] < amount:
        await message.reply(f"<b>❌ У вас нет такой суммы! Баланс: {sender['balance']:,} Ucoin ({format_short_amount(sender['balance'])})</b>".replace(",", " "))
        return
        
    recipient, _ = await database.get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.full_name)
    await database.make_transfer(sender['user_id'], recipient['user_id'], amount)
    await message.reply(
        f"<b>💸 ПЕРЕВОД ВЫПОЛНЕН!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Отправитель:</b> <b>{message.from_user.first_name}</b>\n"
        f"👤 <b>Получатель:</b> <b>{message.reply_to_message.from_user.first_name}</b>\n"
        f"💰 <b>Сумма:</b> <b>{amount:,} Ucoin ({format_short_amount(amount)})</b>".replace(",", " ")
    )

# --- АКТИВАЦИЯ ПРОМОКОДОВ ИГРОКАМИ ---
@dp.message(lambda msg: msg.text and (msg.text.lower().startswith("промо ") or msg.text.lower().startswith("/promo ")))
async def cmd_activate_promo(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат ввода: <code>промо [название]</code> или <code>/promo [название]</code></b>")
        return
        
    promo_code = parts[1].strip()
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    
    status, reward = await database.use_promocode(user['user_id'], promo_code)
    
    if status == "not_found":
        await message.answer("<b>❌ Такого промокода не существует!</b>")
    elif status == "no_activations":
        await message.answer("<b>📥 Увы, этот промокод уже закончился (все активации исчерпаны)!</b>")
    elif status == "already_used":
        await message.answer("<b>🚫 Вы уже активировали этот промокод ранее!</b>")
    elif status == "success":
        await message.answer(
            f"<b>🎉 ПРОМОКОД УСПЕШНО АКТИВИРОВАН!</b>\n"
            f"💰 Вам начислено: <b>+{reward:,} Ucoin (+{format_short_amount(reward)})</b>".replace(",", " ")
        )

# --- УПРАВЛЕНИЕ АДМИНКОЙ ---
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
        f"👥 <b>Всего игроков в базе:</b> <b>{stats['total_users']}</b>\n"
        f"💰 <b>Всего коинов в обороте:</b> <b>{stats['total_balance']:,} ({format_short_amount(stats['total_balance'])})</b>".replace(",", " ")
    )

# Цепочка создания промокода администратором
@dp.callback_query(F.data == "adm_promo")
async def adm_promo_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("<b>Введите кодовое слово промокода (например: BONUS2026):</b>")
    await state.set_state(AdminStates.waiting_for_promo_name)

@dp.message(AdminStates.waiting_for_promo_name)
async def adm_save_promo_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(promo_name=message.text.strip())
    await message.answer("<b>Какую сумму Ucoin будет давать промокод? (можно писать 50к, 1кк и т.д.):</b>")
    await state.set_state(AdminStates.waiting_for_promo_reward)

@dp.message(AdminStates.waiting_for_promo_reward)
async def adm_save_promo_reward(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    reward = parse_amount(message.text, 0)
    if reward <= 0:
        await message.answer("<b>Неверный формат суммы! Настройка отменена.</b>")
        await state.clear()
        return
    await state.update_data(promo_reward=reward)
    await message.answer("<b>Сколько максимум человек смогут активировать этот промокод? (введите число):</b>")
    await state.set_state(AdminStates.waiting_for_promo_activations)

@dp.message(AdminStates.waiting_for_promo_activations)
async def adm_save_promo_activations(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        activations = int(message.text)
        if activations <= 0: raise ValueError
    except ValueError:
        await message.answer("<b>Число активаций должно быть положительным числом! Отмена.</b>")
        await state.clear()
        return
        
    data = await state.get_data()
    await database.create_promocode(data['promo_name'], data['promo_reward'], activations)
    
    await message.answer(
        f"<b>🎫 ПРОМОКОД УСПЕШНО СОЗДАН!</b>\n\n"
        f"📌 <b>Код:</b> <code>{data['promo_name']}</code>\n"
        f"💰 <b>Награда:</b> {data['promo_reward']:,} коинов ({format_short_amount(data['promo_reward'])})\n"
        f"👥 <b>Лимит активаций:</b> {activations}".replace(",", " ")
    )
    await state.clear()

# Логика выдачи и снятия баланса админом
@dp.callback_query(F.data == "adm_give")
async def adm_give_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("<b>Введите Telegram ID игрока, которому хотите НАЧИСЛИТЬ баланс:</b>")
    await state.set_state(AdminStates.waiting_for_give_id)

@dp.message(AdminStates.waiting_for_give_id)
async def adm_give_id(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        await state.update_data(target_id=int(message.text))
        await message.answer("<b>Какую сумму выдать? (можно 10кк):</b>")
        await state.set_state(AdminStates.waiting_for_give_amount)
    except ValueError: await message.answer("ID должен состоять из цифр!")

@dp.message(AdminStates.waiting_for_give_amount)
async def adm_give_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    amount = parse_amount(message.text, 999_999_999_999)
    if amount <= 0:
        await message.answer("Ошибка в сумме.")
        await state.clear()
        return
    success = await database.update_balance_admin(data['target_id'], amount)
    if success: await message.answer(f"Успешно выдано +{amount:,} ({format_short_amount(amount)})".replace(",", " "))
    else: await message.answer("Пользователь не найден.")
    await state.clear()

@dp.callback_query(F.data == "adm_take")
async def adm_take_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("<b>Введите Telegram ID игрока, у которого хотите СНЯТЬ баланс:</b>")
    await state.set_state(AdminStates.waiting_for_take_id)

@dp.message(AdminStates.waiting_for_take_id)
async def adm_take_id(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        await state.update_data(target_id=int(message.text))
        await message.answer("<b>Какую сумму списать? (можно 10кк):</b>")
        await state.set_state(AdminStates.waiting_for_take_amount)
    except ValueError: await message.answer("ID должен быть числом.")

@dp.message(AdminStates.waiting_for_take_amount)
async def adm_take_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    amount = parse_amount(message.text, 999_999_999_999)
    if amount <= 0:
        await message.answer("Ошибка.")
        await state.clear()
        return
    success = await database.update_balance_admin(data['target_id'], -amount)
    if success: await message.answer(f"Списано -{amount:,} ({format_short_amount(amount)})".replace(",", " "))
    else: await message.answer("Пользователь не найден.")
    await state.clear()

# --- ОБЩИЕ КОМАНДЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user, is_new = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    welcome_text = f"<b>🚀 С ВОЗВРАЩЕНИЕМ!</b>\n💰 Баланс: <b>{user['balance']:,} Ucoin ({format_short_amount(user['balance'])})</b>" if not is_new else f"<b>🚀 ПРИВЕТ! ТЕБЕ НАЧИСЛЕНО 1 000 СТАРТОВЫХ UCOIN!</b>"
    
    await message.answer(
        f"{welcome_text}\n\n"
        f"<b>📋 ВСЕ НАШИ КОМАНДЫ:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 <b>БАНК / БАЛАНС / Б</b> — <b>Баланс кошелька</b>\n"
        f"👉 <b>ПРОФИЛЬ</b> — <b>Статистика аккаунта</b>\n"
        f"👉 <b>БОНУС</b> — <b>Ежедневная халява (до 10кк)</b>\n"
        f"👉 <b>ПРОМО [код]</b> — <b>Активировать промокод</b>\n"
        f"👉 <b>БАШНЯ [ставка] [мины]</b> — <b>Запустить Башню (мины от 1 до 4)</b>\n"
        f"👉 <b>МИНЫ [ставка] [мины]</b> — <b>Запустить Мины 5х5 (мины от 1 до 24)</b>"
    )

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
    await message.answer(f"<b>🎁 БОНУС ПОЛУЧЕН! 🎉 Вы выиграли: +{bonus_amount:,} Ucoin!</b>")

@dp.message(lambda msg: msg.text and msg.text.lower() in ["баланс", "/balance", "б", "банк"])
async def check_balance(message: types.Message):
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(f"<b>💰 Ваш баланс: {user['balance']:,} Ucoin ({format_short_amount(user['balance'])})</b>\n\n🎮 Всего проиграно: {user['total_lost']:,} Ucoin ({format_short_amount(user['total_lost'])})".replace(",", " "))

@dp.message(lambda msg: msg.text and msg.text.lower() in ["профиль", "/profile"])
async def check_profile(message: types.Message):
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"<b>👤 ЛИЧНЫЙ ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📝 НИКНЕЙМ: {user['username'].upper()}</b>\n"
        f"<b>🆔 ТВОЙ ID: <code>{user['user_id']}</code></b>\n\n"
        f"<b>💰 БАЛАНС: {user['balance']:,} Ucoin ({format_short_amount(user['balance'])})</b>\n"
        f"<b>🎮 СЫГРАНО ИГР: {user['games_played']}</b>\n"
        f"<b>📉 ВСЕГО ПРОИГРАНО: {user['total_lost']:,} Ucoin ({format_short_amount(user['total_lost'])})</b>".replace(",", " ")
    )

# --- МИНЫ ---
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("мины"))
async def start_mines(message: types.Message, state: FSMContext):
    if await state.get_state() in [GameStates.playing_tower, GameStates.playing_mines]:
        await message.answer("<b>❌ Завершите прошлую игру!</b>")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат: <code>Мины [ставка] [мины]</code> (Пример: Мины вб или Мины 15к 3)</b>")
        return

    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    
    # Автоматически 1 мина, если не указано число
    mines_count = 1
    if len(parts) == 3:
        try: mines_count = int(parts[2])
        except ValueError: pass

    if bet <= 0 or not (1 <= mines_count <= 24):
        await message.answer("<b>❌ Неверные параметры ставки или количества мин (от 1 до 24)!</b>")
        return
    if user['balance'] < bet:
        await message.answer(f"<b>❌ Недостаточно средств!</b>")
        return

    await database.start_game_bet(message.from_user.id, bet)
    grid = [False] * 25
    for idx in random.sample(range(25), mines_count): grid[idx] = True
    revealed = [False] * 25
    
    await state.set_state(GameStates.playing_mines)
    await state.update_data(bet=bet, mines_count=mines_count, grid=grid, revealed=revealed, opened_count=0)

    builder = InlineKeyboardBuilder()
    for i in range(25): builder.button(text="⬛", callback_data=f"mn_clk_{i}")
    builder.adjust(5)

    await message.answer(
        f"<b>💣 ИГРА: МИНЫ (Поле 5х5)</b>\n\n"
        f"💵 <b>Ставка:</b> <b>{bet:,} Ucoin ({format_short_amount(bet)})</b>\n"
        f"💥 <b>Всего мин:</b> <b>{mines_count}</b>\n"
        f"📈 <b>Множитель:</b> <b>1.0х (0 Ucoin)</b>".replace(",", " "),
        reply_markup=builder.as_markup()
    )

@dp.callback_query(GameStates.playing_mines, F.data.startswith("mn_clk_"))
async def process_mines_click(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    cell_idx = int(callback.data.split("_")[2])
    bet, mines_count, grid, revealed, opened_count = data['bet'], data['mines_count'], data['grid'], data['revealed'], data['opened_count']

    if revealed[cell_idx]: return
    revealed[cell_idx] = True

    if grid[cell_idx]:
        await database.lose_game(callback.from_user.id, bet)
        await state.clear()
        builder = InlineKeyboardBuilder()
        for i in range(25): builder.button(text="💥" if i == cell_idx else ("💣" if grid[i] else "💎"), callback_data="void")
        builder.adjust(5)
        await callback.message.edit_text(f"<b>💥 БУМ! ВЫ ПОДОРВАЛИСЬ!</b>\n📉 Проиграно: <b>{bet:,} Ucoin</b>".replace(",", " "), reply_markup=builder.as_markup())
        return

    opened_count += 1
    mult = get_mines_multiplier(mines_count, opened_count)
    current_win = int(bet * mult)
    await state.update_data(revealed=revealed, opened_count=opened_count)

    if opened_count == (25 - mines_count):
        await database.win_game(callback.from_user.id, current_win)
        await state.clear()
        builder = InlineKeyboardBuilder()
        for i in range(25): builder.button(text="💣" if grid[i] else "💎", callback_data="void")
        builder.adjust(5)
        await callback.message.edit_text(f"<b>🏆 ЧИСТАЯ ПОБЕДА! 🎉 Выигрыш: {current_win:,} Ucoin ({mult}x)!</b>".replace(",", " "), reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    for i in range(25): builder.button(text="💎" if revealed[i] else "⬛", callback_data=f"mn_clk_{i}")
    next_mult = get_mines_multiplier(mines_count, opened_count + 1)
    builder.button(text=f"💰 ЗАБРАТЬ {current_win:,} UCOIN", callback_data="mn_cashout")
    builder.adjust(5, 5, 5, 5, 5, 1)

    await callback.message.edit_text(
        f"<b>💣 ИГРА: МИНЫ (Поле 5х5)</b>\n\n"
        f"💵 <b>Ставка:</b> <b>{bet:,} Ucoin ({format_short_amount(bet)})</b>\n"
        f"💥 <b>Всего мин:</b> <b>{mines_count}</b>\n"
        f"💎 <b>Алмазов:</b> <b>{opened_count} / {25 - mines_count}</b>\n"
        f"📈 <b>Множитель:</b> <b>{mult}х ({current_win:,} Ucoin)</b>\n"
        f"🔮 <b>Далее:</b> <b>{next_mult}x</b>".replace(",", " "),
        reply_markup=builder.as_markup()
    )

@dp.callback_query(GameStates.playing_mines, F.data == "mn_cashout")
async def process_mines_cashout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    mult = get_mines_multiplier(data['mines_count'], data['opened_count'])
    current_win = int(data['bet'] * mult)
    await database.win_game(callback.from_user.id, current_win)
    await state.clear()
    await callback.message.edit_text(f"<b>💰 КЭШАУТ! Забрано: {current_win:,} Ucoin ({mult}x)</b>".replace(",", " "), reply_markup=None)

# --- БАШНЯ ---
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("башня"))
async def start_tower(message: types.Message, state: FSMContext):
    if await state.get_state() in [GameStates.playing_tower, GameStates.playing_mines]:
        await message.answer("<b>❌ Завершите активную игру!</b>")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("<b>⚠️ Формат: <code>Башня [ставка] [мины]</code> (Пример: Башня все или Башня 10к 2)</b>")
        return

    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    bet = parse_amount(parts[1], user['balance'])
    
    # ТЕПЕРЬ АВТОМАТИЧЕСКИ ВЫСТАВЛЯЕТСЯ 1 МИНА, ЕСЛИ НЕ УКАЗАТЬ
    mines_count = 1
    if len(parts) == 3:
        try: mines_count = int(parts[2])
        except ValueError: pass

    if bet <= 0 or not (1 <= mines_count <= 4):
        await message.answer("<b>❌ Мины в башне выставляются строго от 1 до 4!</b>")
        return
    if user['balance'] < bet:
        await message.answer("<b>❌ Недостаточно Ucoin!</b>")
        return

    await database.start_game_bet(message.from_user.id, bet)
    current_mines = [False] * 5
    for idx in random.sample(range(5), mines_count): current_mines[idx] = True

    next_win = int(bet * TOWER_MULTIPLIERS[mines_count][0])
    await state.set_state(GameStates.playing_tower)
    await state.update_data(bet=bet, mines_count=mines_count, current_level=1, current_mines=current_mines, accumulated_win=0)

    builder = InlineKeyboardBuilder()
    for i in range(1, 6): builder.button(text=f"📦 Клетка {i}", callback_data=f"tw_step_{i-1}")
    builder.adjust(5)

    text = render_tower_text(current_level=1, bet=bet, mines_count=mines_count, next_win=next_win)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(GameStates.playing_tower, F.data.startswith("tw_step_"))
async def tower_turn(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    bet, mines_count, current_level, current_mines = data['bet'], data['mines_count'], data['current_level'], data['current_mines']
    chosen_cell = int(callback.data.split("_")[2])

    if current_mines[chosen_cell]:
        await database.lose_game(callback.from_user.id, bet)
        await state.clear()
        await callback.message.edit_text(f"<b>💥 МИНА НА {current_level}-М ЭТАЖЕ!</b>\n📉 Потеряно: {bet:,} Ucoin.".replace(",", " "), reply_markup=None)
        return

    current_win = int(bet * TOWER_MULTIPLIERS[mines_count][current_level - 1])

    if current_level == 5:
        await database.win_game(callback.from_user.id, current_win)
        await state.clear()
        await callback.message.edit_text(f"<b>🏆 БАШНЯ СНЕСЕНА! Выигрыш: {current_win:,} Ucoin!</b>".replace(",", " "), reply_markup=None)
        return

    next_level = current_level + 1
    next_win = int(bet * TOWER_MULTIPLIERS[mines_count][next_level - 1])
    new_mines = [False] * 5
    for idx in random.sample(range(5), mines_count): new_mines[idx] = True

    await state.update_data(current_level=next_level, current_mines=new_mines, accumulated_win=current_win)

    builder = InlineKeyboardBuilder()
    for i in range(1, 6): builder.button(text=f"📦 Клетка {i}", callback_data=f"tw_step_{i-1}")
    builder.button(text=f"💰 ЗАБРАТЬ {current_win:,} UCOIN", callback_data="tw_cashout")
    builder.adjust(5, 1)

    text = render_tower_text(current_level=next_level, bet=bet, mines_count=mines_count, next_win=next_win, current_win=current_win)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(GameStates.playing_tower, F.data == "tw_cashout")
async def tower_cashout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    win_sum = data.get('accumulated_win', 0)
    await database.win_game(callback.from_user.id, win_sum)
    await callback.message.edit_text(f"<b>💰 ЗАБРАНО {win_sum:,} UCOIN!</b>".replace(",", " "), reply_markup=None)
    await state.clear()

async def main():
    await database.init_db()
    await bot.set_my_commands([
        types.BotCommand(command="start", description="Главное меню"),
        types.BotCommand(command="balance", description="Мой баланс"),
        types.BotCommand(command="profile", description="Мой профиль"),
        types.BotCommand(command="bonus", description="Взять бонус"),
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
