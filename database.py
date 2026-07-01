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
        # Таблица промокодов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                reward INTEGER,
                activations_left INTEGER
            )
        ''')
        # Таблица логов активаций (защита от повторного ввода)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS activated_promos (
                user_id INTEGER,
                code TEXT,
                PRIMARY KEY (user_id, code)
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

async def get_global_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*), SUM(balance) FROM users") as cursor:
            row = await cursor.fetchone()
            return {"total_users": row[0] or 0, "total_balance": row[1] or 0}

async def update_balance_admin(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            if not await cursor.fetchone():
                return False
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
        return True

async def make_transfer(from_id: int, to_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, from_id))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, to_id))
        await db.commit()

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С ПРОМОКОДАМИ ---

async def create_promocode(code: str, reward: int, activations: int):
    """Добавить или обновить промокод в базе"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO promocodes (code, reward, activations_left) VALUES (?, ?, ?)",
            (code.lower().strip(), reward, activations)
        )
        await db.commit()

async def use_promocode(user_id: int, code: str):
    """Попытка активировать промокод игроком"""
    code = code.lower().strip()
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем существование промокода
        async with db.execute("SELECT reward, activations_left FROM promocodes WHERE code = ?", (code,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return "not_found", 0
            reward, activations_left = row[0], row[1]
            
        if activations_left <= 0:
            return "no_activations", 0
            
        # Проверяем, не активировал ли этот юзер его ранее
        async with db.execute("SELECT 1 FROM activated_promos WHERE user_id = ? AND code = ?", (user_id, code)) as cursor:
            if await cursor.fetchone():
                return "already_used", 0
                
        # Процесс успешной активации
        await db.execute("INSERT INTO activated_promos (user_id, code) VALUES (?, ?)", (user_id, code))
        await db.execute("UPDATE promocodes SET activations_left = activations_left - 1 WHERE code = ?", (code,))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
        await db.commit()
        return "success", reward
