# bot.py
import os
import random
import asyncio
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
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Таблица множителей для игры Башня (зависит от количества мин и текущего этажа)
MULTIPLIERS = {
    1: [1.15, 1.40, 1.75, 2.20, 2.80],
    2: [1.50, 2.40, 3.80, 6.20, 10.00],
    3: [2.30, 5.50, 13.00, 32.00, 80.00],
    4: [4.60, 22.00, 100.00, 450.00, 2000.00]
}

class GameStates(StatesGroup):
    playing_tower = State()

# Функция отрисовки текстовой графики Башни
def render_tower_text(current_level, bet, mines_count, next_win, current_win=0):
    rows = []
    for lvl in range(5, 0, -1):
        if lvl > current_level:
            rows.append(f"Этаж {lvl}: ⬜ ⬜ ⬜ ⬜ ⬜")
        elif lvl == current_level:
            rows.append(f"Этаж {lvl}: ❓ ❓ ❓ ❓ ❓  ◀️")
        else:
            rows.append(f"Этаж {lvl}: ✅ ✅ ✅ ✅ ✅ (Пройден)")
            
    text = (
        f"🏰 <b>ИГРА: БАШНЯ (TOWER)</b>\n\n"
        + "\n".join(rows) + "\n\n"
        f"💰 Ставка: <b>{bet} Ucoin</b>\n"
        f"💣 Мин на этаже: <b>{mines_count}</b>\n"
        f"💵 Текущий куш: <b>{current_win} Ucoin</b>\n"
        f"📈 Шаг на следующий этаж: <b>+{next_win} Ucoin</b>"
    )
    return text

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await database.get_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"Привет, {message.from_user.first_name}! Добро пожаловать в виртуальное казино 🎰\n\n"
        f"Тебе начислено <b>1000 Ucoin</b> стартового баланса!\n\n"
        f"ℹ️ <b>Команды:</b>\n"
        f"• Напиши <b>Баланс</b> или <b>Б</b> — проверить счет\n"
        f"• Напиши <b>Профиль</b> — статистика аккаунта\n"
        f"• Напиши <b>Башня [ставка] [мины]</b> — начать игру (например: <code>Башня 100 2</code>)"
    )

# Обработка Баланса (Баланс, /balance, б)
@dp.message(lambda msg: msg.text and msg.text.lower() in ["баланс", "/balance", "б"])
async def check_balance(message: types.Message):
    user = await database.get_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"💰Ваш баланс: {user['balance']} Ucoin\n\n\n"
        f"🎮Всего проигранно: {user['total_lost']} Ucoin"
    )

# Обработка Профиля (Профиль, /profile)
@dp.message(lambda msg: msg.text and msg.text.lower() in ["профиль", "/profile"])
async def check_profile(message: types.Message):
    user = await database.get_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"👤 <b>Ваш профиль:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 Никнейм: <b>{user['username']}</b>\n"
        f"🆔 Твой ID: <code>{user['user_id']}</code>\n\n"
        f"💰 Баланс: <b>{user['balance']} Ucoin</b>\n"
        f"🎮 Сыграно игр: <b>{user['games_played']}</b>\n"
        f"📉 Всего проиграно: <b>{user['total_lost']} Ucoin</b>"
    )

# Старт игры Башня
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("башня"))
async def start_tower(message: types.Message, state: FSMContext):
    # Проверяем, нет ли уже активной игры
    current_state = await state.get_state()
    if current_state == GameStates.playing_tower:
        await message.answer("❌ У вас уже идет игра! Закончите её перед началом новой.")
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("⚠️ <b>Неверный формат!</b>\nИспользуйте: <code>Башня [ставка] [мины]</code>\nПример: <code>Башня 100 2</code>")
        return

    try:
        bet = int(parts[1])
        mines_count = int(parts[2])
    except ValueError:
        await message.answer("❌ Ставка и мины должны быть целыми числами!")
        return

    if bet <= 0:
        await message.answer("❌ Ставка должна быть больше нуля!")
        return

    if not (1 <= mines_count <= 4):
        await message.answer("❌ Количество мин на этаже должно быть от 1 до 4!")
        return

    user = await database.get_user(message.from_user.id, message.from_user.full_name)
    if user['balance'] < bet:
        await message.answer(f"❌ Недостаточно Ucoin! Ваш баланс: {user['balance']} Ucoin")
        return

    # Списываем ставку из БД
    await database.start_game_bet(message.from_user.id, bet)

    # Генерируем расположение мин для 1-го этажа (5 клеток, True = мина)
    current_mines = [False] * 5
    mine_indices = random.sample(range(5), mines_count)
    for idx in mine_indices:
        current_mines[idx] = True

    next_win = int(bet * MULTIPLIERS[mines_count][0])

    # Сохраняем состояние игры в кэш системы FSM
    await state.set_state(GameStates.playing_tower)
    await state.update_data(
        bet=bet,
        mines_count=mines_count,
        current_level=1,
        current_mines=current_mines,
        accumulated_win=0
    )

    # Клавиатура из 5 скрытых ячеек инлайн
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.button(text=f"📦 Клетка {i}", callback_data=f"tw_step_{i-1}")
    builder.adjust(5)

    text = render_tower_text(current_level=1, bet=bet, mines_count=mines_count, next_win=next_win, current_win=0)
    await message.answer(text, reply_markup=builder.as_markup())

# Ход в инлайн-клетках Башни
@dp.callback_query(GameStates.playing_tower, F.data.startswith("tw_step_"))
async def tower_turn(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    
    bet = data['bet']
    mines_count = data['mines_count']
    current_level = data['current_level']
    current_mines = data['current_mines']
    
    chosen_cell = int(callback.data.split("_")[2])

    # 💥 Наступил на мину
    if current_mines[chosen_cell]:
        await database.lose_game(callback.from_user.id, bet)
        await state.clear()
        
        # Показываем карту этажа игроку
        reveal = ["💣" if m else "💎" for m in current_mines]
        reveal[chosen_cell] = "💥"
        
        await callback.message.edit_text(
            f"💥 <b>БАБАХ! На {current_level}-м этаже оказалась мина!</b>\n"
            f"Раскладка этажа: [ { ' '.join(reveal) } ]\n\n"
            f"📉 Ты потерял <b>{bet} Ucoin</b>.",
            reply_markup=None
        )
        return

    # 🎉 Успешный шаг
    current_mult = MULTIPLIERS[mines_count][current_level - 1]
    current_win = int(bet * current_mult)

    # Если дошел до верха башни (5 этаж) — автоматическая победа
    if current_level == 5:
        await database.win_game(callback.from_user.id, current_win)
        await state.clear()
        await callback.message.edit_text(
            f"🏆 <b>НЕВЕРОЯТНО! Вы покорили всю Башню!</b>\n\n"
            f"💰 Ваш чистый выигрыш: <b>{current_win} Ucoin</b>!",
            reply_markup=None
        )
        return

    # Переход на следующий этаж
    next_level = current_level + 1
    next_mult = MULTIPLIERS[mines_count][next_level - 1]
    next_win = int(bet * next_mult)

    # Генерируем новые мины для следующего этажа
    new_mines = [False] * 5
    mine_indices = random.sample(range(5), mines_count)
    for idx in mine_indices:
        new_mines[idx] = True

    await state.update_data(
        current_level=next_level,
        current_mines=new_mines,
        accumulated_win=current_win
    )

    # Перерисовываем клавиатуру + добавляем кнопку "Забрать кэш"
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.button(text=f"📦 Клетка {i}", callback_data=f"tw_step_{i-1}")
    builder.button(text=f"💰 Забрать {current_win} Ucoin", callback_data="tw_cashout")
    builder.adjust(5, 1)

    text = render_tower_text(current_level=next_level, bet=bet, mines_count=mines_count, next_win=next_win, current_win=current_win)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

# Забрать выигрыш и выйти
@dp.callback_query(GameStates.playing_tower, F.data == "tw_cashout")
async def tower_cashout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    accumulated_win = data.get('accumulated_win', 0)

    if accumulated_win > 0:
        await database.win_game(callback.from_user.id, accumulated_win)
        await callback.message.edit_text(
            f"💰 <b>Вы решили не рисковать!</b>\n"
            f"💵 Забрано с башни: <b>{accumulated_win} Ucoin</b>. Деньги зачислены на баланс!",
            reply_markup=None
        )
    else:
        await callback.message.edit_text("Ошибка зачисления средств.", reply_markup=None)

    await state.clear()

async def main():
    await database.init_db() # Инициализируем БД при старте
    print("Бот-казино успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
