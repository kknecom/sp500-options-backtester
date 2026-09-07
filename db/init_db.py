"""
Initialize (or re-initialize) the local SQLite database from schema.sql.
Safe to run against an existing database file: CREATE TABLE IF NOT EXISTS
leaves existing tables alone, and migrate() (see db/migrate.py) then
brings any of them that predate a schema change up to date.
"""
import sqlite3
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config
from db.migrate import migrate


def init_db(db_path: str = config.DB_PATH):
    schema_path = Path(__file__).parent / "schema.sql"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    with open(schema_path) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    migrate(db_path)
    print(f"Database initialized at {db_path}")


if __name__ == "__main__":
    init_db()
