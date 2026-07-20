import os
import sqlite3

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outreach.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)


def _postgres_dsn():
    """Render provides postgres:// URLs; psycopg2 accepts postgresql://."""
    if DATABASE_URL.startswith("postgres://"):
        return DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return DATABASE_URL


def _translate_sql(query):
    """Translate the small SQLite SQL subset used by the app to Postgres."""
    translated = query
    translated = translated.replace("datetime('now', ?)", "(NOW() + (?::interval))")
    translated = translated.replace(
        "SUBSTR(r.email, INSTR(r.email, '@') + 1)",
        "SPLIT_PART(r.email, '@', 2)",
    )
    translated = translated.replace(
        "SUBSTR(email, INSTR(email, '@') + 1)",
        "SPLIT_PART(email, '@', 2)",
    )
    translated = translated.replace("%", "%%")
    translated = translated.replace("?", "%s")
    return translated


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def description(self):
        return self.cursor.description

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, query, args=()):
        self.cursor.execute(_translate_sql(query), args)
        return self

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchone(self):
        return self.cursor.fetchone()


class PostgresConnection:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return PostgresCursor(self.conn.cursor(cursor_factory=RealDictCursor))

    def execute(self, query, args=()):
        cursor = self.cursor()
        return cursor.execute(query, args)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def get_db():
    """Get a database connection. Uses Postgres when DATABASE_URL is set."""
    if USE_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary is required when DATABASE_URL is set")
        conn = psycopg2.connect(_postgres_dsn())
        return PostgresConnection(conn)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _postgres_columns(conn, table_name):
    cursor = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        """,
        (table_name,),
    )
    return [row["column_name"] for row in cursor.fetchall()]

def migrate_reply_status_columns(conn):
    if USE_POSTGRES:
        conn.execute("ALTER TABLE recipients ADD COLUMN IF NOT EXISTS reply_status TEXT DEFAULT 'no_reply'")
        conn.execute("ALTER TABLE recipients ADD COLUMN IF NOT EXISTS reply_content TEXT")
        conn.execute("ALTER TABLE recipients ADD COLUMN IF NOT EXISTS check_back_date TEXT")
        conn.execute("ALTER TABLE recipients ADD COLUMN IF NOT EXISTS exclude_followup INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE recipients ADD COLUMN IF NOT EXISTS status_updated_at TEXT")
        conn.commit()
        return

    cursor = conn.cursor()
    existing = [row[1] for row in cursor.execute("PRAGMA table_info(recipients)").fetchall()]

    new_columns = [
        ("reply_status",    "TEXT DEFAULT 'no_reply'"),
        ("reply_content",   "TEXT"),
        ("check_back_date", "TEXT"),
        ("exclude_followup","INTEGER DEFAULT 0"),
        ("status_updated_at", "TEXT"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in existing:
            cursor.execute(f"ALTER TABLE recipients ADD COLUMN {col_name} {col_def}")

    conn.commit()


def migrate_tracking_columns(conn):
    """Safe migration: add open-tracking columns to recipients and followups."""
    if USE_POSTGRES:
        for table in ("recipients", "followups"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tracking_id TEXT")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS open_count INTEGER DEFAULT 0")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS first_opened_at TEXT")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS last_opened_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipients_tracking_id ON recipients(tracking_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_followups_tracking_id ON followups(tracking_id)")
        conn.commit()
        return

    cursor = conn.cursor()

    tracking_columns = [
        ("tracking_id",     "TEXT"),
        ("open_count",      "INTEGER DEFAULT 0"),
        ("first_opened_at", "TEXT"),
        ("last_opened_at",  "TEXT"),
    ]

    for table in ("recipients", "followups"):
        existing = [row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
        for col_name, col_def in tracking_columns:
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recipients_tracking_id ON recipients(tracking_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_followups_tracking_id ON followups(tracking_id)")
    conn.commit()


def migrate_blocked_domains_table(conn):
    """Safe migration: create blocked_domains table if it doesn't exist."""
    if USE_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blocked_domains (
                id SERIAL PRIMARY KEY,
                domain TEXT UNIQUE NOT NULL,
                reason TEXT,
                blocked_at TEXT NOT NULL
            )
        """)
        conn.commit()
        return

    conn.execute("""
        CREATE TABLE IF NOT EXISTS blocked_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL,
            reason TEXT,
            blocked_at TEXT NOT NULL
        )
    """)
    conn.commit()


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()

    if USE_POSTGRES:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                goal TEXT NOT NULL,
                additional_context TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS recipients (
                id SERIAL PRIMARY KEY,
                campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                email TEXT NOT NULL,
                name TEXT,
                subject TEXT,
                email_body TEXT,
                status TEXT DEFAULT 'draft',
                sent_at TIMESTAMP,
                message_id TEXT,
                error_message TEXT,
                follow_up_sent INTEGER DEFAULT 0,
                reply_status TEXT DEFAULT 'no_reply',
                reply_content TEXT,
                check_back_date TEXT,
                exclude_followup INTEGER DEFAULT 0,
                status_updated_at TEXT,
                tracking_id TEXT,
                open_count INTEGER DEFAULT 0,
                first_opened_at TEXT,
                last_opened_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS followups (
                id SERIAL PRIMARY KEY,
                recipient_id INTEGER NOT NULL REFERENCES recipients(id),
                subject TEXT,
                email_body TEXT,
                status TEXT DEFAULT 'draft',
                sent_at TIMESTAMP,
                error_message TEXT,
                tracking_id TEXT,
                open_count INTEGER DEFAULT 0,
                first_opened_at TEXT,
                last_opened_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_recipients_email ON recipients(email)",
            "CREATE INDEX IF NOT EXISTS idx_recipients_domain ON recipients(email)",
            "CREATE INDEX IF NOT EXISTS idx_recipients_sent_at ON recipients(sent_at)",
            "CREATE INDEX IF NOT EXISTS idx_recipients_campaign_id ON recipients(campaign_id)",
            "CREATE INDEX IF NOT EXISTS idx_recipients_reply_status ON recipients(reply_status)",
        ]
        for statement in statements:
            conn.execute(statement)
        conn.commit()
        migrate_reply_status_columns(conn)
        migrate_blocked_domains_table(conn)
        migrate_tracking_columns(conn)
        conn.close()
        return

    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            goal TEXT NOT NULL,
            additional_context TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            email TEXT NOT NULL,
            name TEXT,
            subject TEXT,
            email_body TEXT,
            status TEXT DEFAULT 'draft',
            sent_at DATETIME,
            message_id TEXT,
            error_message TEXT,
            follow_up_sent INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER NOT NULL REFERENCES recipients(id),
            subject TEXT,
            email_body TEXT,
            status TEXT DEFAULT 'draft',
            sent_at DATETIME,
            error_message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_recipients_email ON recipients(email);
        CREATE INDEX IF NOT EXISTS idx_recipients_domain ON recipients(email);
        CREATE INDEX IF NOT EXISTS idx_recipients_sent_at ON recipients(sent_at);
        CREATE INDEX IF NOT EXISTS idx_recipients_campaign_id ON recipients(campaign_id);
    """)
    conn.commit()
    
    migrate_reply_status_columns(conn)
    
    # Create index for reply_status after ensuring the column exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recipients_reply_status ON recipients(reply_status);")
    conn.commit()
    
    migrate_blocked_domains_table(conn)
    migrate_tracking_columns(conn)
    conn.close()


def query_db(query, args=(), one=False):
    """Execute a query and return results as list of dicts."""
    conn = get_db()
    cursor = conn.execute(query, args)
    rows = cursor.fetchall()
    conn.close()

    result = [dict(row) for row in rows]
    if one:
        return result[0] if result else None
    return result


def execute_db(query, args=()):
    """Execute a write query and return lastrowid."""
    conn = get_db()
    if USE_POSTGRES and query.lstrip().lower().startswith("insert") and "returning" not in query.lower():
        cursor = conn.execute(f"{query} RETURNING id", args)
        row = cursor.fetchone()
        conn.commit()
        conn.close()
        return row["id"]

    cursor = conn.execute(query, args)
    conn.commit()
    lastrowid = cursor.lastrowid
    conn.close()
    return lastrowid
