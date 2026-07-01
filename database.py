# database.py
import aiosqlite
import time

DB_NAME = "casino.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 1000,
                total_lost INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                last_bonus INTEGER DEFAULT 0
            )
        ''')
        await db.commit()

async def get_or_create_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, balance, total_lost, games_played, last_bonus FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                if row[1] != username:
                    await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
                    await db.commit()
                return {"user_id": row[0], "username": username, "balance": row[2], "total_lost": row[3], "games_played": row[4], "last_bonus": row[5]}, False
            
            await db.execute("INSERT INTO users (user_id, username, balance) VALUES (?, ?, 1000)", (user_id, username))
            await db.commit()
            return {"user_id": user_id, "username": username, "balance": 1000, "total_lost": 0, "games_played": 0, "last_bonus": 0}, True

async def start_game_bet(user_id: int, bet_amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet_amount, user_id))
        await db.commit()

async def win_game(user_id: int, win_amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ?, games_played = games_played + 1 WHERE user_id = ?", (win_amount, user_id))
        await db.commit()

async def lose_game(user_id: int, bet_amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET total_lost = total_lost + ?, games_played = games_played + 1 WHERE user_id = ?", (bet_amount, user_id))
        await db.commit()

async def claim_bonus(user_id: int, bonus_amount: int):
    current_time = int(time.time())
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?", (bonus_amount, current_time, user_id))
        await db.commit()

# --- ФУНКЦИИ ДЛЯ АДМИНКИ И ПЕРЕВОДОВ ---

async def get_global_stats():
    """Возвращает общее число игроков и сумму всех коинов в экономике"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*), SUM(balance) FROM users") as cursor:
            row = await cursor.fetchone()
            return {"total_users": row[0] or 0, "total_balance": row[1] or 0}

async def update_balance_admin(user_id: int, amount: int):
    """Принудительное изменение баланса (может быть отрицательным)"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем, есть ли пользователь
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            if not await cursor.fetchone():
                return False
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
        return True

async def make_transfer(from_id: int, to_id: int, amount: int):
    """Безопасный перевод денег между пользователями"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, from_id))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, to_id))
        await db.commit()
