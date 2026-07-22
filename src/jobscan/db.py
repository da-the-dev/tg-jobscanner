"""SQLite storage: message cache + verdict cache."""
import hashlib
import json
import re
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels(
    id INTEGER PRIMARY KEY,
    username TEXT,
    title TEXT,
    last_msg_id INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages(
    channel_id INTEGER,
    msg_id INTEGER,
    date TEXT,
    text TEXT,
    text_hash TEXT,
    link TEXT,
    status TEXT DEFAULT 'new',  -- new | filtered_out | duplicate | scored | error
    PRIMARY KEY(channel_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_hash ON messages(text_hash);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE TABLE IF NOT EXISTS verdicts(
    text_hash TEXT PRIMARY KEY,
    relevant INTEGER,
    score INTEGER,
    title TEXT, company TEXT, salary TEXT, location TEXT,
    reasons_apply TEXT, reasons_skip TEXT,
    strengths TEXT, weaknesses TEXT,
    model TEXT, created_at TEXT DEFAULT (datetime('now'))
);
"""


def text_hash(text):
    norm = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- channels ---
    def upsert_channel(self, cid, username, title):
        self.conn.execute(
            "INSERT INTO channels(id, username, title) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, title=excluded.title",
            (cid, username, title))
        self.conn.commit()

    def last_msg_id(self, cid):
        row = self.conn.execute("SELECT last_msg_id FROM channels WHERE id=?", (cid,)).fetchone()
        return row["last_msg_id"] if row else 0

    def set_last_msg_id(self, cid, msg_id):
        self.conn.execute("UPDATE channels SET last_msg_id=? WHERE id=? AND last_msg_id<?",
                          (msg_id, cid, msg_id))
        self.conn.commit()

    # --- messages ---
    def add_message(self, cid, msg_id, date, text, link):
        h = text_hash(text)
        dup = self.conn.execute(
            "SELECT 1 FROM messages WHERE text_hash=? LIMIT 1", (h,)).fetchone()
        status = "duplicate" if dup else "new"
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO messages(channel_id,msg_id,date,text,text_hash,link,status) "
            "VALUES(?,?,?,?,?,?,?)", (cid, msg_id, date, text, h, link, status))
        self.conn.commit()
        return cur.rowcount > 0, status

    def set_status(self, cid, msg_id, status):
        self.conn.execute("UPDATE messages SET status=? WHERE channel_id=? AND msg_id=?",
                          (status, cid, msg_id))
        self.conn.commit()

    def pending_messages(self):
        """Messages that passed nothing yet: status='new'."""
        return self.conn.execute("SELECT * FROM messages WHERE status='new' ORDER BY date").fetchall()

    # --- verdicts ---
    def has_verdict(self, h):
        return self.conn.execute("SELECT 1 FROM verdicts WHERE text_hash=?", (h,)).fetchone() is not None

    def save_verdict(self, h, v, model):
        self.conn.execute(
            "INSERT OR REPLACE INTO verdicts(text_hash,relevant,score,title,company,salary,location,"
            "reasons_apply,reasons_skip,strengths,weaknesses,model) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (h, int(bool(v.get("relevant"))), int(v.get("score") or 0),
             v.get("title") or "", v.get("company") or "", v.get("salary") or "",
             v.get("location") or "",
             json.dumps(v.get("reasons_apply") or [], ensure_ascii=False),
             json.dumps(v.get("reasons_skip") or [], ensure_ascii=False),
             json.dumps(v.get("strengths") or [], ensure_ascii=False),
             json.dumps(v.get("weaknesses") or [], ensure_ascii=False),
             model))
        self.conn.commit()

    def dashboard_rows(self):
        """One row per unique relevant verdict, joined to its earliest message."""
        return self.conn.execute("""
            SELECT v.*, m.link, m.date, m.channel_id, c.title AS channel_title, c.username
            FROM verdicts v
            JOIN messages m ON m.text_hash = v.text_hash
            JOIN channels c ON c.id = m.channel_id
            WHERE v.relevant = 1
            GROUP BY v.text_hash
            ORDER BY v.score DESC
        """).fetchall()

    def stats(self):
        rows = self.conn.execute(
            "SELECT status, COUNT(*) n FROM messages GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}
