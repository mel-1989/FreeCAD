# SPDX-License-Identifier: LGPL-2.1-or-later
"""PostgreSQL storage: locks, audit log, where-used index. One short connection per call, so a database
restart costs the requests in flight and nothing else."""

import contextlib
import json

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS locks (
    document  text PRIMARY KEY,
    lock_id   text NOT NULL UNIQUE,
    holder    text NOT NULL,
    payload   jsonb NOT NULL,
    taken_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS locks_document_ci ON locks (lower(document));
CREATE TABLE IF NOT EXISTS audit (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    actor     text NOT NULL,
    action    text NOT NULL,
    document  text,
    detail    jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS link_index (
    id        integer PRIMARY KEY CHECK (id = 1),
    head      text NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    document  text NOT NULL,
    object    text,
    property  text,
    file      text NOT NULL
);
CREATE INDEX IF NOT EXISTS links_document ON links (document);
CREATE TABLE IF NOT EXISTS unreadable (
    document  text PRIMARY KEY,
    reason    text NOT NULL,
    lowered   text NOT NULL
);
"""


class DbDown(Exception):
    pass


class Db:
    def __init__(self, url):
        self.url = url

    @contextlib.contextmanager
    def conn(self):
        try:
            c = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5)
        except psycopg.OperationalError as e:
            raise DbDown(str(e)) from None
        try:
            with c:
                yield c
        except psycopg.OperationalError as e:
            raise DbDown(str(e)) from None
        finally:
            c.close()

    def init(self):
        with self.conn() as c:
            c.execute(SCHEMA)

    # ---- locks ------------------------------------------------------------------------------

    @staticmethod
    def _lock_dict(row):
        info = dict(row["payload"])
        info.update(document=row["document"], holder=row["holder"], sha=row["lock_id"])
        return info

    def locks(self):
        with self.conn() as c:
            return {r["document"]: self._lock_dict(r) for r in c.execute("SELECT * FROM locks ORDER BY document")}

    def lock(self, document):
        with self.conn() as c:
            r = c.execute("SELECT * FROM locks WHERE document = %s", (document,)).fetchone()
            return self._lock_dict(r) if r else None

    def acquire(self, document, lock_id, holder, payload):
        """Atomic create-if-absent (the primary key and the case-insensitive unique index decide a race).
        Returns (True, info) or (False, existing_info)."""
        with self.conn() as c:
            r = c.execute(
                "INSERT INTO locks (document, lock_id, holder, payload) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING *",
                (document, lock_id, holder, json.dumps(payload))).fetchone()
            if r:
                c.execute("INSERT INTO audit (actor, action, document, detail) VALUES (%s, 'lock', %s, %s)",
                          (holder, document, json.dumps({"lock_id": lock_id})))
                return True, self._lock_dict(r)
            r = c.execute("SELECT * FROM locks WHERE lower(document) = lower(%s)", (document,)).fetchone()
            return False, (self._lock_dict(r) if r else None)

    def release(self, document, lock_id, actor, action="unlock", detail=None):
        """Delete the row only if it still has ``lock_id`` (None: whatever is there). True when a row went."""
        with self.conn() as c:
            if lock_id is None:
                r = c.execute("DELETE FROM locks WHERE document = %s RETURNING *", (document,)).fetchone()
            else:
                r = c.execute("DELETE FROM locks WHERE document = %s AND lock_id = %s RETURNING *",
                              (document, lock_id)).fetchone()
            if r:
                d = dict(detail or {})
                d.update(holder=r["holder"], lock_id=r["lock_id"])
                c.execute("INSERT INTO audit (actor, action, document, detail) VALUES (%s, %s, %s, %s)",
                          (actor, action, document, json.dumps(d)))
            return r is not None

    def audit(self, limit=200):
        with self.conn() as c:
            rows = c.execute("SELECT id, at, actor, action, document, detail FROM audit ORDER BY id DESC LIMIT %s",
                             (limit,)).fetchall()
        for r in rows:
            r["at"] = r["at"].isoformat(timespec="seconds")
        return rows

    def log(self, actor, action, document=None, detail=None):
        with self.conn() as c:
            c.execute("INSERT INTO audit (actor, action, document, detail) VALUES (%s, %s, %s, %s)",
                      (actor, action, document, json.dumps(detail or {})))

    # ---- where-used index -------------------------------------------------------------------

    def index_head(self):
        with self.conn() as c:
            r = c.execute("SELECT head FROM link_index WHERE id = 1").fetchone()
            return r["head"] if r else None

    def replace_index(self, head, links, unreadable):
        with self.conn() as c:
            c.execute("DELETE FROM links")
            c.execute("DELETE FROM unreadable")
            with c.cursor() as cur:
                cur.executemany("INSERT INTO links (document, object, property, file) VALUES (%s, %s, %s, %s)", links)
                cur.executemany("INSERT INTO unreadable (document, reason, lowered) VALUES (%s, %s, %s)", unreadable)
            c.execute("INSERT INTO link_index (id, head) VALUES (1, %s) "
                      "ON CONFLICT (id) DO UPDATE SET head = EXCLUDED.head", (head,))

    def all_links(self):
        with self.conn() as c:
            return (c.execute("SELECT document, object, property, file FROM links ORDER BY document, object, property")
                    .fetchall(),
                    c.execute("SELECT document, reason, lowered FROM unreadable ORDER BY document").fetchall())
