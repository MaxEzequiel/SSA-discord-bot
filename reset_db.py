import os
import sqlite3


def reset_database():
    database_path = os.path.join(os.path.dirname(__file__), "bot.db")
    connection = sqlite3.connect(database_path)
    cursor = connection.cursor()
    cursor.execute("DELETE FROM tickets")
    cursor.execute("DELETE FROM cooldowns")
    cursor.execute("DELETE FROM activity")
    cursor.execute("DELETE FROM transcripts")
    cursor.execute("DELETE FROM bypass_bans")
    cursor.execute("DELETE FROM bypass_points")
    cursor.execute("DELETE FROM sqlite_sequence")
    connection.commit()
    connection.close()


if __name__ == "__main__":
    reset_database()
