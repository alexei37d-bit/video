# database.py
import aiosqlite
import time

DB_FILE = "casino_database.db"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 1000,
                games_played INTEGER DEFAULT 0,
                total_lost INTEGER DEFAULT 0,
                last_bonus INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                name TEXT PRIMARY KEY,
                reward INTEGER,
                activations INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_promos (
                user_id INTEGER,
                promo_name TEXT,
                PRIMARY KEY (user_id, promo_name)
            )
        """)
        await db.commit()

async def get_or_create_user(user_id: int, full_name: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row), False
            else:
                await db.execute(
                    "INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)",
                    (user_id, full_name, 1000)
                )
                await db.commit()
                return {
                    "user_id": user_id,
                    "username": full_name,
                    "balance": 1000,
                    "games_played": 0,
                    "total_lost": 0,
                    "last_bonus": 0
                }, True

async def make_transfer(sender_id: int, recipient_id: int, amount: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, recipient_id))
        await db.commit()

async def claim_bonus(user_id: int, bonus_amount: int):
    async with aiosqlite.connect(DB_FILE) as db:
        current_time = int(time.time())
        await db.execute(
            "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?",
            (bonus_amount, current_time, user_id)
        )
        await db.commit()

async def start_game_bet(user_id: int, bet: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, user_id))
        await db.commit()

async def lose_game(user_id: int, bet: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET games_played = games_played + 1, total_lost = total_lost + ? WHERE user_id = ?",
            (bet, user_id)
        )
        await db.commit()

async def win_game(user_id: int, current_win: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ?, games_played = games_played + 1 WHERE user_id = ?",
            (current_win, user_id)
        )
        await db.commit()

async def get_global_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*), SUM(balance) FROM users") as cursor:
            res = await cursor.fetchone()
            total_users = res[0] if res[0] else 0
            total_balance = res[1] if res[1] else 0
            return {"total_users": total_users, "total_balance": total_balance}

async def create_promocode(name: str, reward: int, activations: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO promocodes (name, reward, activations) VALUES (?, ?, ?)",
            (name, reward, activations)
        )
        await db.commit()

async def use_promocode(user_id: int, promo_code: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("SELECT * FROM promocodes WHERE name = ?", (promo_code,)) as cursor:
            promo = await cursor.fetchone()
            if not promo:
                return "not_found", 0
                
            if promo['activations'] <= 0:
                return "no_activations", 0
                
        async with db.execute("SELECT * FROM user_promos WHERE user_id = ? AND promo_name = ?", (user_id, promo_code)) as cursor:
            if await cursor.fetchone():
                return "already_used", 0
                
        await db.execute("UPDATE promocodes SET activations = activations - 1 WHERE name = ?", (promo_code,))
        await db.execute("INSERT INTO user_promos (user_id, promo_name) VALUES (?, ?)", (user_id, promo_code))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (promo['reward'], user_id))
        await db.commit()
        
        return "success", promo['reward']

async def update_balance_admin(target_id: int, amount: int) -> bool:
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.commit()
        return cursor.rowcount > 0
