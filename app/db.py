"""Small SQLite WAL store; all updates serialized across asyncio tasks using threading.Lock."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from threading import RLock
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).isoformat()

class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA busy_timeout=5000')
            self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS news (
              id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
              source TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
              url TEXT NOT NULL, published_at TEXT, received_at TEXT NOT NULL,
              source_symbols TEXT NOT NULL, related_symbols TEXT NOT NULL,
              priority TEXT NOT NULL, ai_json TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'received');
            CREATE INDEX IF NOT EXISTS idx_news_received ON news(received_at DESC);
            CREATE TABLE IF NOT EXISTS alerts (
              id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT UNIQUE NOT NULL,
              news_id INTEGER, title TEXT NOT NULL, body TEXT NOT NULL,
              severity TEXT NOT NULL, created_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT NOT NULL,
              sent_at TEXT,
              last_error TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS idx_alerts_due ON alerts(status,next_attempt_at);
            CREATE TABLE IF NOT EXISTS quotes (
              symbol TEXT PRIMARY KEY, price REAL NOT NULL, observed_at TEXT NOT NULL,
              source TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS health (
              source TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT NOT NULL,
              updated_at TEXT NOT NULL);
            ''')
            self.conn.commit()

    def put_news(self, event: dict) -> int | None:
        with self.lock:
            cur = self.conn.execute('''INSERT OR IGNORE INTO news
              (event_key,source,title,summary,url,published_at,received_at,source_symbols,related_symbols,priority)
              VALUES (?,?,?,?,?,?,?,?,?,?)''', (
                  event['event_key'],event['source'],event['title'],event['summary'],event['url'],
                  event.get('published_at'),event['received_at'],json.dumps(event['source_symbols']),
                  json.dumps(event['related_symbols']),event['priority']))
            self.conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def set_ai(self, news_id: int, data: dict):
        with self.lock:
            self.conn.execute('UPDATE news SET ai_json=?,status=? WHERE id=?', (json.dumps(data,ensure_ascii=False),'analyzed',news_id))
            self.conn.commit()

    def add_alert(self, key: str, title: str, body: str, severity: str = 'high', news_id: int | None = None) -> bool:
        with self.lock:
            cur = self.conn.execute('''INSERT OR IGNORE INTO alerts
              (dedupe_key,title,body,severity,news_id,created_at,next_attempt_at)
              VALUES (?,?,?,?,?,?,?)''', (key,title,body,severity,news_id,utcnow(),utcnow()))
            self.conn.commit()
            return cur.rowcount > 0

    def due_alerts(self, limit: int = 15) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.conn.execute('''SELECT * FROM alerts
                WHERE status='pending' AND next_attempt_at<=? ORDER BY id LIMIT ?''', (utcnow(),limit))]

    def alert_result(self, alert_id: int, ok: bool, err: str = ''):
        with self.lock:
            attempts = self.conn.execute('SELECT attempts FROM alerts WHERE id=?',(alert_id,)).fetchone()['attempts']+1
            if ok:
                self.conn.execute("UPDATE alerts SET status='sent',attempts=?,sent_at=?,last_error='' WHERE id=?",(attempts,utcnow(),alert_id))
            else:
                from datetime import timedelta
                delay = min(3600, 15 * 2**min(8,attempts))
                when = (datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat()
                self.conn.execute("UPDATE alerts SET attempts=?,next_attempt_at=?,last_error=? WHERE id=?",(attempts,when,err[:500],alert_id))
            self.conn.commit()

    def quote(self, symbol: str, price: float, source: str):
        with self.lock:
            self.conn.execute('''INSERT INTO quotes(symbol,price,observed_at,source) VALUES (?,?,?,?)
             ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,observed_at=excluded.observed_at,source=excluded.source''',
             (symbol,price,utcnow(),source))
            self.conn.commit()

    def health(self, source: str, state: str, detail: str = ''):
        with self.lock:
            self.conn.execute('''INSERT INTO health(source,state,detail,updated_at) VALUES (?,?,?,?)
              ON CONFLICT(source) DO UPDATE SET state=excluded.state,detail=excluded.detail,updated_at=excluded.updated_at''',
              (source,state,detail[:500],utcnow()))
            self.conn.commit()

    def list_rows(self, table: str, limit=50):
        if table not in ('news','alerts','quotes','health'):
            raise ValueError('unknown table')
        order = {'news':'id DESC','alerts':'id DESC','quotes':'symbol','health':'source'}[table]
        with self.lock:
            return [dict(row) for row in self.conn.execute(f'SELECT * FROM {table} ORDER BY {order} LIMIT ?', (min(500,max(1,limit)),))]
