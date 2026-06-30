# database.py
import aiosqlite

DB_NAME = "casino.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 1000,
                total_lost INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0
            )
        ''')
        await db.commit()

async def get_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, balance, total_lost, games_played FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                # Если у пользователя изменился ник в ТГ, обновляем его в базе
                if row[1] != username:
                    await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
                    await db.commit()
                return {"user_id": row[0], "username": username, "balance": row[2], "total_lost": row[3], "games_played": row[4]}
            
            # Если пользователя нет, регистрируем со 1000 Ucoin
            await db.execute("INSERT INTO users (user_id, username, balance) VALUES (?, ?, 1000)", (user_id, username))
            await db.commit()
            return {"user_id": user_id, "username": username, "balance": 1000, "total_lost": 0, "games_played": 0}

async def start_game_bet(user_id: int, bet_amount: int):
    """Списание ставки в начале игры"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet_amount, user_id))
        await db.commit()

async def win_game(user_id: int, win_amount: int):
    """Зачисление выигрыша и прибавление счетчика игр"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ?, games_played = games_played + 1 WHERE user_id = ?", (win_amount, user_id))
        await db.commit()

async def lose_game(user_id: int, bet_amount: int):
    """Запись проигрыша в статистику"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET total_lost = total_lost + ?, games_played = games_played + 1 WHERE user_id = ?", (bet_amount, user_id))
        await db.commit()
