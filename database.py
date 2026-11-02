import sqlite3

connection = sqlite3.connect("bot.db")
connection.row_factory = sqlite3.Row


def init_database():
    cursor = connection.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS tickets ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, "
        "channel_id INTEGER, "
        "difficulty TEXT, "
        "created_at INTEGER, "
        "closed_at INTEGER, "
        "closed_by INTEGER, "
        "claimed_by INTEGER, "
        "claimed_at INTEGER)"
    )
    cursor.execute("PRAGMA table_info(tickets)")
    ticket_columns = []
    for row in cursor.fetchall():
        ticket_columns.append(row["name"])
    if "os_name" not in ticket_columns:
        cursor.execute("ALTER TABLE tickets ADD COLUMN os_name TEXT")
    if "modified" not in ticket_columns:
        cursor.execute("ALTER TABLE tickets ADD COLUMN modified TEXT")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS cooldowns ("
        "user_id INTEGER PRIMARY KEY, "
        "cooldown_end INTEGER)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS activity ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "staff_id INTEGER, "
        "ticket_id INTEGER, "
        "difficulty TEXT, "
        "points INTEGER, "
        "created_at INTEGER, "
        "removed INTEGER)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS transcripts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticket_id INTEGER, "
        "channel_name TEXT, "
        "created_at INTEGER)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS bypass_bans ("
        "user_id INTEGER PRIMARY KEY)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS bypass_points ("
        "user_id INTEGER PRIMARY KEY, "
        "points INTEGER)"
    )
    connection.commit()


def create_ticket(user_id, channel_id, difficulty, created_at, os_name=None, modified=None):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO tickets (user_id, channel_id, difficulty, created_at, os_name, modified) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, channel_id, difficulty, created_at, os_name, modified),
    )
    connection.commit()
    return cursor.lastrowid


def get_next_ticket_number():
    cursor = connection.cursor()
    cursor.execute("SELECT MAX(id) AS max_id FROM tickets")
    row = cursor.fetchone()
    if row["max_id"] is None:
        return 1
    return row["max_id"] + 1


def get_open_ticket_by_user(user_id):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT * FROM tickets WHERE user_id = ? AND closed_at IS NULL",
        (user_id,),
    )
    return cursor.fetchone()


def get_open_ticket_by_channel(channel_id):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT * FROM tickets WHERE channel_id = ? AND closed_at IS NULL",
        (channel_id,),
    )
    return cursor.fetchone()


def claim_ticket(ticket_id, staff_id, claimed_at):
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE tickets SET claimed_by = ?, claimed_at = ? WHERE id = ?",
        (staff_id, claimed_at, ticket_id),
    )
    connection.commit()


def unclaim_ticket(ticket_id):
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE tickets SET claimed_by = NULL, claimed_at = NULL WHERE id = ?",
        (ticket_id,),
    )
    connection.commit()


def close_ticket(ticket_id, staff_id, closed_at):
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE tickets SET closed_at = ?, closed_by = ? WHERE id = ?",
        (closed_at, staff_id, ticket_id),
    )
    connection.commit()


def add_activity(staff_id, ticket_id, difficulty, points, created_at):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO activity (staff_id, ticket_id, difficulty, points, created_at, removed) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (staff_id, ticket_id, difficulty, points, created_at),
    )
    connection.commit()


def add_activity_adjustment(staff_id, points, created_at):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO activity (staff_id, ticket_id, difficulty, points, created_at, removed) "
        "VALUES (?, NULL, ?, ?, ?, 0)",
        (staff_id, "manual", points, created_at),
    )
    connection.commit()


def remove_activity(ticket_id):
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE activity SET removed = 1 WHERE ticket_id = ? AND removed = 0",
        (ticket_id,),
    )
    connection.commit()


def set_cooldown(user_id, cooldown_end):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO cooldowns (user_id, cooldown_end) VALUES (?, ?)",
        (user_id, cooldown_end),
    )
    connection.commit()


def get_cooldown(user_id):
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM cooldowns WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


def delete_cooldown(user_id):
    cursor = connection.cursor()
    cursor.execute("DELETE FROM cooldowns WHERE user_id = ?", (user_id,))
    connection.commit()


def get_all_cooldowns():
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM cooldowns")
    return cursor.fetchall()


def add_bypass_ban(user_id):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO bypass_bans (user_id) VALUES (?)",
        (user_id,),
    )
    connection.commit()


def remove_bypass_ban(user_id):
    cursor = connection.cursor()
    cursor.execute("DELETE FROM bypass_bans WHERE user_id = ?", (user_id,))
    connection.commit()


def is_bypass_banned(user_id):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT user_id FROM bypass_bans WHERE user_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return False
    return True


def add_bypass_points(user_id, amount):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO bypass_points (user_id, points) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET points = points + ?",
        (user_id, amount, amount),
    )
    connection.commit()


def remove_bypass_points(user_id, amount):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO bypass_points (user_id, points) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET points = points - ?",
        (user_id, amount * -1, amount),
    )
    connection.commit()


def add_bypass_point(user_id):
    add_bypass_points(user_id, 1)


def get_top_bypassers(limit):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT user_id, points FROM bypass_points "
        "ORDER BY points DESC, user_id ASC LIMIT ?",
        (limit,),
    )
    return cursor.fetchall()


def get_bypass_position(user_id):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT user_id FROM bypass_points ORDER BY points DESC, user_id ASC"
    )
    rows = cursor.fetchall()
    position = 0
    for index, row in enumerate(rows):
        if row["user_id"] == user_id:
            position = index + 1
    return position


def get_activity_points_since(since):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT staff_id, SUM(points) AS total FROM activity "
        "WHERE removed = 0 AND created_at >= ? GROUP BY staff_id",
        (since,),
    )
    return cursor.fetchall()


def create_transcript(ticket_id, channel_name, created_at):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO transcripts (ticket_id, channel_name, created_at) VALUES (?, ?, ?)",
        (ticket_id, channel_name, created_at),
    )
    connection.commit()
    return cursor.lastrowid
