import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
db_path = os.getenv("DB_PATH","src/database/data/database.db")


def initialize_database(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS User (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            passwordHash TEXT NOT NULL,
            status TEXT CHECK(status IN ('online','offline')) NOT NULL DEFAULT 'offline',
            role CHAR(10) CHECK(role IN ('player','developer')) NOT NULL DEFAULT 'user'
        );

        CREATE TABLE IF NOT EXISTS Game (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            OwnerId INTEGER NOT NULL,
            LatestVersion CHAR(10) NOT NULL,
            min_players INTEGER NOT NULL DEFAULT 2,
            max_players INTEGER NOT NULL DEFAULT 2,
            FOREIGN KEY(OwnerId) REFERENCES User(id)
        );

        CREATE TABLE IF NOT EXISTS Room (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            hostUserId INTEGER NOT NULL,
            visibility TEXT CHECK(visibility IN ('public','private')) NOT NULL,
            status TEXT CHECK(status IN ('idle','playing')) NOT NULL,
            gameId INTEGER NOT NULL,
            FOREIGN KEY(gameId) REFERENCES Game(id),
            FOREIGN KEY(hostUserId) REFERENCES User(id)
        );

        CREATE TABLE IF NOT EXISTS invite_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roomId INTEGER NOT NULL,
            fromId INTEGER NOT NULL,
            toId INTEGER NOT NULL,
            FOREIGN KEY(roomId) REFERENCES Room(id),
            FOREIGN KEY(fromId) REFERENCES User(id),
            FOREIGN KEY(toId) REFERENCES User(id)
        );

        CREATE TABLE IF NOT EXISTS in_room (
            roomId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            PRIMARY KEY(roomId, userId),
            FOREIGN KEY(roomId) REFERENCES Room(id),
            FOREIGN KEY(userId) REFERENCES User(id)
        );

        CREATE TABLE IF NOT EXISTS request_join_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roomId INTEGER NOT NULL,
            fromId INTEGER NOT NULL,
            toId INTEGER NOT NULL,
            FOREIGN KEY(roomId) REFERENCES Room(id),
            FOREIGN KEY(fromId) REFERENCES User(id),
            FOREIGN KEY(toId) REFERENCES User(id)
        );

        CREATE TABLE IF NOT EXISTS GameVersion(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gameId INTEGER NOT NULL,
            VersionNumber CHAR(10) NOT NULL,
            Command TEXT NOT NULL ,
            UploadDate TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(gameId) REFERENCES Game(id)
        );

        CREATE TABLE IF NOT EXISTS played(
            gameId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            PRIMARY KEY(gameId, userId),
            FOREIGN KEY(gameId) REFERENCES Game(id),
            FOREIGN KEY(userId) REFERENCES User(id)
        );

        CREATE TABLE IF NOT EXISTS comment(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gameId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            score INTEGER CHECK(score BETWEEN 1 AND 5) NOT NULL,
            FOREIGN KEY(gameId) REFERENCES Game(id),
            FOREIGN KEY(userId) REFERENCES User(id)
        );

        """
    )

    conn.commit()
    conn.close()

# Fake Data Initialization

def seed_fake_data(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    fake_users = [
        ('Alice', 'hash1','developer'),
        ('Bob', 'hash2','developer'),
        ('Charlie', 'hash3','player')
    ]
    cur.executemany("INSERT INTO User ( name, passwordHash,role) VALUES ( ?, ?,?)", fake_users)



    fake_games = [
        ("fake_game","description",1,"0.0.1")
    ]
    cur.executemany("INSERT INTO Game (name,description,OwnerId,LatestVersion) VALUES(?,?,?,?) ",fake_games)

    fake_version = [
        (1,"0.0.1",".")
    ]
    cur.executemany("INSERT INTO GameVersion (gameId,VersionNumber,FilePath) VALUES(?,?,?)",fake_version)


    conn.commit()
    conn.close()


if __name__ == '__main__':
    initialize_database(db_path)
    # seed_fake_data(db_path)