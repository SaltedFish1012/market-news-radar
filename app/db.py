"""Small SQLite WAL store; all updates serialized across asyncio tasks using threading.Lock."""
from __future__ import annotations
import json
import math
import sqlite3
from pathlib import Path
from threading import RLock
from datetime import datetime, timezone
from .market_sessions import Quote
from .instruments import resolve_instrument, SESSIONS
from .smart_money_store import init_smart_money_schema


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
            CREATE TABLE IF NOT EXISTS session_quotes (
              symbol TEXT NOT NULL, session TEXT NOT NULL, price REAL NOT NULL,
              currency TEXT NOT NULL, source TEXT NOT NULL, quote_at REAL,
              received_at REAL NOT NULL, trade_status TEXT NOT NULL,
              PRIMARY KEY(symbol,session));
            ''')
            columns = {r['name'] for r in self.conn.execute('PRAGMA table_info(news)')}
            for name, declaration in [('source_type',"TEXT NOT NULL DEFAULT 'news'"),
                    ('raw_text',"TEXT NOT NULL DEFAULT ''"),('x_metadata',"TEXT NOT NULL DEFAULT '{}'"),
                    ('edited_at','TEXT')]:
                if name not in columns:
                    self.conn.execute(f'ALTER TABLE news ADD COLUMN {name} {declaration}')
            self.conn.executescript('''
                CREATE TABLE IF NOT EXISTS x_users (user_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS x_user_aliases (username TEXT PRIMARY KEY, user_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS x_cursors (user_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS x_post_aliases (post_id TEXT PRIMARY KEY, news_id INTEGER NOT NULL);
            ''')
            for row in self.conn.execute('SELECT * FROM quotes').fetchall():
                if self.conn.execute('SELECT 1 FROM session_quotes WHERE symbol=?', (row['symbol'],)).fetchone():
                    continue
                try:
                    received = datetime.fromisoformat(row['observed_at']).timestamp()
                    currency = resolve_instrument(row['symbol']).currency
                except (ValueError, TypeError):
                    received, currency = 0, 'USD'
                self.conn.execute('''INSERT OR IGNORE INTO session_quotes
                    VALUES (?, 'unknown', ?, ?, ?, NULL, ?, 'unknown')''',
                    (row['symbol'], row['price'], currency, row['source'], received))
            init_smart_money_schema(self.conn)
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

    def news_rows(self, *, limit: int, source_type: str = 'all') -> list[dict]:
        if source_type not in ('all', 'news'):
            raise ValueError('Invalid news source type')
        where = " WHERE source_type != 'x'" if source_type == 'news' else ''
        with self.lock:
            return [dict(r) for r in self.conn.execute('SELECT * FROM news'+where+' ORDER BY id DESC LIMIT ?', (limit,))]

    def x_posts(self, *, limit: int = 100, username: str | None = None) -> list[dict]:
        where, args = "source_type='x'", []
        with self.lock:
            if username:
                user = self.x_user(username)
                if not user:
                    return []
                where += " AND CASE WHEN json_valid(x_metadata) THEN json_extract(x_metadata,'$.author_id') END = ?"
                args.append(user['id'])
            return [dict(r) for r in self.conn.execute('SELECT * FROM news WHERE '+where+' ORDER BY id DESC LIMIT ?',
                                                      (*args, min(100, max(1, limit))))]

    def x_accounts(self) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.conn.execute('SELECT data FROM x_users ORDER BY user_id')]

    def x_user(self, username: str) -> dict | None:
        with self.lock:
            row = self.conn.execute('SELECT data FROM x_users JOIN x_user_aliases USING(user_id) WHERE username=?',
                                    (username.casefold().removeprefix('@'),)).fetchone()
            return json.loads(row[0]) if row else None

    def save_x_user(self, user: dict, configured_username: str):
        with self.lock, self.conn:
            self.conn.execute('INSERT INTO x_users VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',
                              (user['id'],json.dumps(user,ensure_ascii=False)))
            for name in (configured_username,user['username']):
                self.conn.execute('INSERT INTO x_user_aliases VALUES (?,?) ON CONFLICT(username) DO UPDATE SET user_id=excluded.user_id',
                                  (name.removeprefix('@').casefold(),user['id']))

    def x_cursor(self, user_id: str) -> dict:
        with self.lock:
            row = self.conn.execute('SELECT data FROM x_cursors WHERE user_id=?',(user_id,)).fetchone()
            return json.loads(row[0]) if row else {'initialized':False,'since_id':None,'retry_at':0,'failures':0}

    def save_x_cursor(self, user_id: str, state: dict):
        with self.lock, self.conn:
            self.conn.execute('INSERT INTO x_cursors VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',
                              (user_id,json.dumps(state,ensure_ascii=False)))

    def x_states(self) -> list[dict]:
        with self.lock:
            return [{'user_id':r['user_id'],**json.loads(r['data'])} for r in self.conn.execute('SELECT * FROM x_cursors')]

    def store_x_event(self, event: dict, alert: dict | None = None) -> tuple[int,str]:
        meta = event['x_metadata']
        post_id = str(meta['post_id'])
        ids = list(dict.fromkeys([post_id,*map(str,meta.get('edit_history_ids',[]))]))
        if any(not v.isascii() or not v.isdigit() or len(v)>64 for v in ids):
            raise ValueError('Invalid X post identity')
        root = min(ids,key=int)
        with self.lock, self.conn:
            row = self.conn.execute('SELECT n.* FROM news n JOIN x_post_aliases a ON a.news_id=n.id WHERE a.post_id IN ('+
                ','.join('?' for _ in ids)+') LIMIT 1',ids).fetchone()
            if row:
                news_id = row['id']
                previous = json.loads(row['x_metadata'])
                status = 'duplicate'
                if int(post_id)>int(previous['post_id']):
                    self.conn.execute('UPDATE news SET raw_text=?,summary=?,title=?,url=?,x_metadata=?,edited_at=? WHERE id=?',
                        (event['raw_text'],event['summary'],event['title'],event['url'],json.dumps(meta,ensure_ascii=False),
                         event.get('edited_at') or utcnow(),news_id))
                    status = 'updated'
            else:
                cur = self.conn.execute('''INSERT INTO news (event_key,source,title,summary,url,published_at,received_at,
                    source_symbols,related_symbols,priority,source_type,raw_text,x_metadata,edited_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',('x:'+root,event['source'],event['title'],event['summary'],
                    event['url'],event.get('published_at'),event['received_at'],json.dumps(event['source_symbols']),
                    json.dumps(event['related_symbols']),event['priority'],'x',event['raw_text'],
                    json.dumps(meta,ensure_ascii=False),event.get('edited_at')))
                news_id,status = cur.lastrowid,'inserted'
                if alert:
                    self.conn.execute('''INSERT OR IGNORE INTO alerts
                        (dedupe_key,news_id,title,body,severity,created_at,next_attempt_at) VALUES (?,?,?,?,?,?,?)''',
                        ('x:'+root+':fast',news_id,alert['title'],alert['body'],alert['severity'],utcnow(),utcnow()))
            for identity in ids:
                self.conn.execute('INSERT OR IGNORE INTO x_post_aliases VALUES (?,?)',(identity,news_id))
            return news_id,status

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

    def put_session_quote(self, quote: Quote) -> bool:
        if (quote.session not in (*SESSIONS, 'unknown') or not math.isfinite(quote.price)
                or quote.price <= 0 or not math.isfinite(quote.received_at)
                or (quote.quote_at is not None and not math.isfinite(quote.quote_at))):
            return False
        with self.lock, self.conn:
            cur = self.conn.execute('''INSERT INTO session_quotes VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,session) DO UPDATE SET price=excluded.price,
                currency=excluded.currency, source=excluded.source, quote_at=excluded.quote_at,
                received_at=excluded.received_at, trade_status=excluded.trade_status
                WHERE (excluded.quote_at IS NOT NULL AND
                       (session_quotes.quote_at IS NULL OR excluded.quote_at > session_quotes.quote_at))
                   OR (excluded.quote_at IS NULL AND session_quotes.quote_at IS NULL
                       AND excluded.received_at > session_quotes.received_at)''',
                (quote.symbol,quote.session,quote.price,quote.currency,quote.source,
                 quote.quote_at,quote.received_at,quote.trade_status))
            if not cur.rowcount:
                return False
            latest = self.conn.execute('SELECT max(quote_at) FROM session_quotes WHERE symbol=?', (quote.symbol,)).fetchone()[0]
            if latest is None or (quote.quote_at is not None and quote.quote_at >= latest):
                self.conn.execute('''INSERT INTO quotes VALUES (?,?,?,?) ON CONFLICT(symbol)
                    DO UPDATE SET price=excluded.price,observed_at=excluded.observed_at,source=excluded.source''',
                    (quote.symbol,quote.price,datetime.fromtimestamp(quote.received_at,timezone.utc).isoformat(),quote.source))
            return True

    def session_quotes(self, symbol: str | None = None) -> list[dict]:
        with self.lock:
            sql = 'SELECT * FROM session_quotes' + (' WHERE symbol=?' if symbol else '') + ' ORDER BY symbol,session'
            return [dict(r) for r in self.conn.execute(sql, (symbol,) if symbol else ())]

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
