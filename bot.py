# bot.py
import os
import math
import random
import time
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
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

TOWER_MULTIPLIERS = {
    1: [1.15, 1.40, 1.75, 2.20, 2.80],
    2: [1.50, 2.40, 3.80, 6.20, 10.00],
    3: [2.30, 5.50, 13.00, 32.00, 80.00],
    4: [4.60, 22.00, 100.00, 450.00, 2000.00]
}

class GameStates(StatesGroup):
    playing_tower = State()
    playing_mines = State()

def get_mines_multiplier(total_mines, opened_count):
    if opened_count == 0: return 1.0
    try:
        ways_total = math.comb(25, opened_count)
        ways_safe = math.comb(25 - total_mines, opened_count)
        if ways_safe == 0: return 0
        return round((ways_total / ways_safe) * 0.96, 2)
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
        f"💰 <b>Ставка:</b> <b>{bet} Ucoin</b>\n"
        f"💣 <b>Мин на этаже:</b> <b>{mines_count}</b>\n"
        f"💵 <b>Текущий куш:</b> <b>{current_win} Ucoin</b>\n"
        f"📈 <b>Следующий шаг:</b> <b>+{next_win} Ucoin</b>"
    )

# Умный обработчик кнопки /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user, is_new = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    
    if is_new:
        # Текст ТОЛЬКО ДЛЯ ПЕРВОГО СТАРТА
        await message.answer(
            f"<b>🚀 ПРИВЕТ, {message.from_user.first_name.upper()}! ДОБРО ПОЖАЛОВАТЬ В КАЗИНО UCOIN!</b>\n\n"
            f"<b>💰 ТЕБЕ НАЧИСЛЕНО 1000 UCOIN СТАРТОВОГО БАЛАНСА!</b>\n\n"
            f"<b>📋 ПОЛНЫЙ СПИСОК КОМАНД БОТА:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <b>БАНК / БАЛАНС / Б</b> — <b>Посмотреть личный счет</b>\n"
            f"👉 <b>ПРОФИЛЬ</b> — <b>Ваша статистика аккаунта</b>\n"
            f"👉 <b>БОНУС</b> — <b>Получить от 300 до 10,000 Ucoin (Раз в сутки)</b>\n"
            f"👉 <b>БАШНЯ [ставка] [мины]</b> — <b>Игра (от 1 до 4 мин)</b>\n"
            f"👉 <b>МИНЫ [ставка] [мины]</b> — <b>Игра 5х5 (от 1 до 24 мин)</b>"
        )
    else:
        # Текст ДЛЯ ВСЕХ ПОДРЯД ПОВТОРНЫХ НАЖАТИЙ /start
        await message.answer(
            f"<b>🚀 С ВОЗВРАЩЕНИЕМ, {message.from_user.first_name.upper()}!</b>\n"
            f"💰 <b>Твой текущий баланс:</b> <b>{user['balance']} Ucoin</b>\n\n"
            f"<b>📋 ВСЕ НАШИ КОМАНДЫ ДЛЯ ИГРЫ:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <b>БАНК / БАЛАНС / Б</b> — <b>Баланс кошелька</b>\n"
            f"👉 <b>ПРОФИЛЬ</b> — <b>Посмотреть ID и статистику игр</b>\n"
            f"👉 <b>БОНУС</b> — <b>Забрать ежедневную халяву</b>\n"
            f"👉 <b>БАШНЯ [ставка] [мины]</b> — <b>Запустить Башню</b>\n"
            f"👉 <b>МИНЫ [ставка] [мины]</b> — <b>Запустить Мины 5х5</b>"
        )

@dp.message(lambda msg: msg.text and msg.text.lower() in ["бонус", "/bonus"])
async def get_daily_bonus(message: types.Message):
    user = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    user_data = user[0] # Получаем сам словарь данных юзера
    current_time = int(time.time())
    cooldown = 24 * 60 * 60
    
    if current_time - user_data['last_bonus'] < cooldown:
        time_left = cooldown - (current_time - user_data['last_bonus'])
        hours, minutes = time_left // 3600, (time_left % 3600) // 60
        await message.answer(f"<b>⏳ ВЫ УЖЕ ЗАБИРАЛИ БОНУС!</b>\n<b>Приходите через: {hours} ч. и {minutes} мин.</b>")
        return

    bonus_amount = random.randint(300, 10000)
    await database.claim_bonus(message.from_user.id, bonus_amount)
    await message.answer(
        f"<b>🎁 ЕЖЕДНЕВНЫЙ БОНУС ПОЛУЧЕН!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>🎉 Вы выиграли:</b> <b>+{bonus_amount} Ucoin</b>\n"
        f"<b>💰 Текущий баланс:</b> <b>{user_data['balance'] + bonus_amount} Ucoin</b>"
    )

@dp.message(lambda msg: msg.text and msg.text.lower() in ["баланс", "/balance", "б", "банк"])
async def check_balance(message: types.Message):
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"<b>💰 Ваш баланс: {user['balance']} Ucoin</b>\n\n\n"
        f"<b>🎮 Всего проигранно: {user['total_lost']} Ucoin</b>"
    )

@dp.message(lambda msg: msg.text and msg.text.lower() in ["профиль", "/profile"])
async def check_profile(message: types.Message):
    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"<b>👤 ЛИЧНЫЙ ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📝 НИКНЕЙМ: {user['username'].upper()}</b>\n"
        f"<b>🆔 ТВОЙ ID: <code>{user['user_id']}</code></b>\n\n"
        f"<b>💰 БАЛАНС: {user['balance']} Ucoin</b>\n"
        f"<b>🎮 СЫГРАНО ИГР: {user['games_played']}</b>\n"
        f"<b>📉 ВСЕГО ПРОИГРАНО: {user['total_lost']} Ucoin</b>"
    )

# --- МИНЫ ---
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("мины"))
async def start_mines(message: types.Message, state: FSMContext):
    if await state.get_state() in [GameStates.playing_tower, GameStates.playing_mines]:
        await message.answer("<b>❌ У вас уже открыта игра! Завершите её.</b>")
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("<b>⚠️ НЕВЕРНЫЙ ФОРМАТ!</b>\nИспользуйте: <code>Мины [ставка] [мины]</code>")
        return

    try:
        bet, mines_count = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("<b>❌ Вводите только целые числа!</b>")
        return

    if bet <= 0 or not (1 <= mines_count <= 24):
        await message.answer("<b>❌ Ставка больше 0, а мины строго от 1 до 24!</b>")
        return

    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    if user['balance'] < bet:
        await message.answer(f"<b>❌ Недостаточно средств! Баланс: {user['balance']} Ucoin</b>")
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
        f"💵 <b>Ставка:</b> <b>{bet} Ucoin</b>\n"
        f"💥 <b>Всего мин:</b> <b>{mines_count}</b>\n"
        f"💎 <b>Открыто алмазов:</b> <b>0 / {25 - mines_count}</b>\n"
        f"📈 <b>Множитель:</b> <b>1.0х (0 Ucoin)</b>",
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
        for i in range(25):
            if i == cell_idx: text = "💥"
            elif grid[i]: text = "💣"
            else: text = "💎"
            builder.button(text=text, callback_data="void")
        builder.adjust(5)
        await callback.message.edit_text(f"<b>💥 БУМ! ВЫ ПОДОРВАЛИСЬ!</b>\n━━━━━━━━━━━━━━━━━━━━\n📉 <b>Проиграно:</b> <b>{bet} Ucoin</b>", reply_markup=builder.as_markup())
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
        await callback.message.edit_text(f"<b>🏆 ЧИСТАЯ ПОБЕДА!</b>\n━━━━━━━━━━━━━━━━━━━━\n🎉 <b>Выигрыш:</b> <b>{current_win} Ucoin ({mult}x)!</b>", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    for i in range(25): builder.button(text="💎" if revealed[i] else "⬛", callback_data=f"mn_clk_{i}")
    next_mult = get_mines_multiplier(mines_count, opened_count + 1)
    builder.button(text=f"💰 ЗАБРАТЬ {current_win} UCOIN", callback_data="mn_cashout")
    builder.adjust(5, 5, 5, 5, 5, 1)

    await callback.message.edit_text(
        f"<b>💣 ИГРА: МИНЫ (Поле 5х5)</b>\n\n"
        f"💵 <b>Ставка:</b> <b>{bet} Ucoin</b>\n"
        f"💥 <b>Всего мин:</b> <b>{mines_count}</b>\n"
        f"💎 <b>Открыто алмазов:</b> <b>{opened_count} / {25 - mines_count}</b>\n"
        f"📈 <b>Множитель:</b> <b>{mult}х ({current_win} Ucoin)</b>\n"
        f"🔮 <b>Следующий шаг:</b> <b>{next_mult}x</b>",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(GameStates.playing_mines, F.data == "mn_cashout")
async def process_mines_cashout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    opened_count, mines_count, bet = data['opened_count'], data['mines_count'], data['bet']
    mult = get_mines_multiplier(mines_count, opened_count)
    current_win = int(bet * mult)

    await database.win_game(callback.from_user.id, current_win)
    await state.clear()
    await callback.message.edit_text(f"<b>💰 КЭШАУТ!</b>\n━━━━━━━━━━━━━━━━━━━━\n💵 <b>Забрано:</b> <b>{current_win} Ucoin ({mult}x)</b>", reply_markup=None)

# --- БАШНЯ ---
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("башня"))
async def start_tower(message: types.Message, state: FSMContext):
    if await state.get_state() in [GameStates.playing_tower, GameStates.playing_mines]:
        await message.answer("<b>❌ У вас уже идет игра!</b>")
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("<b>⚠️ Формат: <code>Башня [ставка] [мины]</code></b>")
        return

    try:
        bet, mines_count = int(parts[1]), int(parts[2])
    except ValueError: return

    if bet <= 0 or not (1 <= mines_count <= 4):
        await message.answer("<b>❌ Мины в башне: от 1 до 4!</b>")
        return

    user, _ = await database.get_or_create_user(message.from_user.id, message.from_user.full_name)
    if user['balance'] < bet:
        await message.answer("<b>❌ Недостаточно коинов!</b>")
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
        await callback.message.edit_text(f"<b>💥 МИНА НА {current_level} ЭТАЖЕ!</b>\n📉 Проиграно {bet} Ucoin.", reply_markup=None)
        return

    current_win = int(bet * TOWER_MULTIPLIERS[mines_count][current_level - 1])

    if current_level == 5:
        await database.win_game(callback.from_user.id, current_win)
        await state.clear()
        await callback.message.edit_text(f"<b>🏆 БАШНЯ ПОКОРЕНА! Выигрыш: {current_win} Ucoin!</b>", reply_markup=None)
        return

    next_level = current_level + 1
    next_win = int(bet * TOWER_MULTIPLIERS[mines_count][next_level - 1])
    new_mines = [False] * 5
    for idx in random.sample(range(5), mines_count): new_mines[idx] = True

    await state.update_data(current_level=next_level, current_mines=new_mines, accumulated_win=current_win)

    builder = InlineKeyboardBuilder()
    for i in range(1, 6): builder.button(text=f"📦 Клетка {i}", callback_data=f"tw_step_{i-1}")
    builder.button(text=f"💰 ЗАБРАТЬ {current_win} UCOIN", callback_data="tw_cashout")
    builder.adjust(5, 1)

    text = render_tower_text(current_level=next_level, bet=bet, mines_count=mines_count, next_win=next_win, current_win=current_win)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(GameStates.playing_tower, F.data == "tw_cashout")
async def tower_cashout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await database.win_game(callback.from_user.id, data.get('accumulated_win', 0))
    await callback.message.edit_text(f"<b>💰 ЗАБРАНО {data.get('accumulated_win', 0)} UCOIN!</b>", reply_markup=None)
    await state.clear()

async def main():
    await database.init_db()
    
    # Регистрируем меню команд прямо внутри Telegram
    await bot.set_my_commands([
        types.BotCommand(command="start", description="Перезапустить бота / Инфо"),
        types.BotCommand(command="balance", description="Посмотреть баланс"),
        types.BotCommand(command="profile", description="Мой профиль"),
        types.BotCommand(command="bonus", description="Получить ежедневный бонус"),
    ])
    
    print("Супер-казино успешно запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
