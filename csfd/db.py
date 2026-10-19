"""MariaDB access."""

import functools
import logging
import os
import time

import pymysql

WAITING, IN_PROGRESS, DONE, MISSING, FAILED = range(5)

# MySQL server has gone away, lost connection during query, can't connect.
LOST_CONNECTION_ERRORS = (2003, 2006, 2013)
# Deadlock and lock wait timeout: parallel workers upserting the same films. Every write is an idempotent upsert,
# so the statement can simply be repeated.
LOCK_ERRORS = (1205, 1213)
RECONNECT_ATTEMPTS = 5

log = logging.getLogger("csfd")

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def connect(config):
    """Connects to the configured database, creating it first if it doesn't exist yet."""
    section = config["mysql"]
    settings = dict(
        host=section.get("host", "localhost"),
        port=section.getint("port", 3306),
        user=section["user"],
        password=section["password"],
        charset="utf8mb4",
        autocommit=True,
    )
    name = section["database"]
    with pymysql.connect(**settings) as server:
        with server.cursor() as cursor:
            cursor.execute("CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                           % name.replace("`", "``"))
    return pymysql.connect(database=name, **settings)


def reconnecting(function):
    """Retries a database call after a deadlock, or after reconnecting when the server dropped the connection (both
    seen on NFS)."""

    @functools.wraps(function)
    def wrapper(connection, *args, **kwargs):
        for attempt in range(RECONNECT_ATTEMPTS):
            try:
                return function(connection, *args, **kwargs)
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
                code = e.args[0] if e.args else None
                if code in LOCK_ERRORS and attempt < RECONNECT_ATTEMPTS - 1:
                    log.info("Database lock conflict (%s), retrying", e)
                    time.sleep(0.2 * (attempt + 1))
                    continue
                lost = isinstance(e, pymysql.err.InterfaceError) or code in LOST_CONNECTION_ERRORS
                if not lost or attempt == RECONNECT_ATTEMPTS - 1:
                    raise
                log.warning("Database connection lost (%s), reconnecting", e)
                time.sleep(2 ** attempt)
                try:
                    connection.ping(reconnect=True)
                except pymysql.err.Error:
                    pass

    return wrapper


@reconnecting
def create_schema(connection):
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        statements = [s.strip() for s in f.read().split(";")]
    with connection.cursor() as cursor:
        for statement in statements:
            lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
            if "".join(lines).strip():
                cursor.execute("\n".join(lines))
    ensure_indexes(connection)


# Indexes added after the first deployment; create_schema adds them to existing databases.
EXTRA_INDEXES = [
    ("users", "rating_count", "ALTER TABLE users ADD KEY rating_count (rating_count)"),
    ("users", "name", "ALTER TABLE users ADD KEY name (name)"),
]


# Columns widened after the first deployment: (table, column, length, ALTER statement).
WIDENED_COLUMNS = [
    ("films", "slug", 1000, "ALTER TABLE films MODIFY slug VARCHAR(1000) NOT NULL"),
]


@reconnecting
def ensure_indexes(connection):
    with connection.cursor() as cursor:
        for table, column, length, ddl in WIDENED_COLUMNS:
            cursor.execute("SELECT character_maximum_length FROM information_schema.columns WHERE "
                           "table_schema=DATABASE() AND table_name=%s AND column_name=%s", (table, column))
            row = cursor.fetchone()
            if row and row[0] < length:
                cursor.execute(ddl)
        for table, name, ddl in EXTRA_INDEXES:
            cursor.execute("SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() "
                           "AND table_name=%s AND index_name=%s LIMIT 1", (table, name))
            if not cursor.fetchone():
                cursor.execute(ddl)


@reconnecting
def refresh_film_stats(connection):
    """Rebuilds film_stats from ratings and swaps it in atomically."""
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS film_stats_new")
        cursor.execute("CREATE TABLE film_stats_new LIKE film_stats")
        cursor.execute("INSERT INTO film_stats_new (film_id, rating_count, average) "
                       "SELECT film_id, COUNT(*), AVG(stars) FROM ratings GROUP BY film_id")
        cursor.execute("RENAME TABLE film_stats TO film_stats_old, film_stats_new TO film_stats")
        cursor.execute("DROP TABLE film_stats_old")
    set_state(connection, "film_stats_at", int(time.time()))


@reconnecting
def get_state(connection, name, default=None):
    with connection.cursor() as cursor:
        cursor.execute("SELECT value FROM crawl_state WHERE name=%s", (name,))
        row = cursor.fetchone()
    return row[0] if row else default


@reconnecting
def set_state(connection, name, value):
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO crawl_state (name, value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE value=VALUES(value)",
            (name, str(value)),
        )


@reconnecting
def add_users(connection, user_ids):
    with connection.cursor() as cursor:
        cursor.executemany("INSERT IGNORE INTO users (id) VALUES (%s)", [(i,) for i in user_ids])


@reconnecting
def requeue_user(connection, user_id):
    """Scrape the user again from the first page (keeps ratings already stored, updates changed ones)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, priority) VALUES (%s, 1) "
            "ON DUPLICATE KEY UPDATE status=%s, priority=1, pages_done=0, error=NULL",
            (user_id, WAITING),
        )


@reconnecting
def reset_interrupted(connection):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE users SET status=%s WHERE status=%s", (WAITING, IN_PROGRESS))


@reconnecting
def claim_waiting_user(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, pages_done FROM users WHERE status=%s ORDER BY priority DESC, id LIMIT 1", (WAITING,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE users SET status=%s WHERE id=%s", (IN_PROGRESS, row[0]))
    return row


@reconnecting
def save_page(connection, user_id, page, page_number):
    """Stores one parsed ratings page and records it as done for the user."""
    # Writing films in ID order makes parallel workers lock shared rows in the same order, which avoids most deadlocks.
    ratings = sorted(page.ratings, key=lambda r: r.film_id)
    with connection.cursor() as cursor:
        if ratings:
            cursor.executemany(
                "INSERT INTO films (id, parent_id, slug, title, year, kind) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE parent_id=VALUES(parent_id), slug=VALUES(slug), title=VALUES(title), "
                "year=VALUES(year), kind=VALUES(kind)",
                [(r.film_id, r.parent_id, r.slug[:1000], r.title[:300], r.year, r.kind[:40]) for r in ratings],
            )
            cursor.executemany(
                "INSERT INTO ratings (user_id, film_id, stars, rated_on) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE stars=VALUES(stars), rated_on=VALUES(rated_on)",
                [(user_id, r.film_id, r.stars, r.rated_on) for r in ratings],
            )
        cursor.execute(
            "UPDATE users SET slug=COALESCE(%s, slug), name=COALESCE(%s, name), page_count=%s, pages_done=%s "
            "WHERE id=%s",
            (page.user_slug and page.user_slug[:100], page.user_name and page.user_name[:100], page.page_count, page_number,
             user_id),
        )


@reconnecting
def finish_user(connection, user_id, status, error=None):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE users SET status=%s, error=%s, scraped_at=NOW(), "
            "rating_count=(SELECT COUNT(*) FROM ratings WHERE user_id=%s) WHERE id=%s",
            (status, error[:255] if error else None, user_id, user_id),
        )


@reconnecting
def release_user(connection, user_id):
    """Puts an unfinished user back in the queue; the next run continues after pages_done."""
    with connection.cursor() as cursor:
        cursor.execute("UPDATE users SET status=%s WHERE id=%s", (WAITING, user_id))


@reconnecting
def start_run(connection):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO crawl_runs (started_at) VALUES (NOW())")
        return cursor.lastrowid


@reconnecting
def finish_run(connection, run_id, stats, fetcher, stop_reason):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE crawl_runs SET finished_at=NOW(), requests=%s, credits_used=%s, credits_remaining=%s, "
            "users_done=%s, ratings_saved=%s, stop_reason=%s WHERE id=%s",
            (fetcher.requests, fetcher.credits_used, fetcher.credits_remaining, stats["users"], stats["ratings"],
             stop_reason[:255], run_id),
        )


@reconnecting
def stats(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT status, COUNT(*), SUM(rating_count) FROM users GROUP BY status")
        users = {row[0]: (row[1], int(row[2] or 0)) for row in cursor.fetchall()}
        cursor.execute("SELECT COUNT(*) FROM films")
        films = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ratings")
        ratings = cursor.fetchone()[0]
        cursor.execute(
            "SELECT started_at, finished_at, requests, credits_used, credits_remaining, users_done, ratings_saved, "
            "stop_reason FROM crawl_runs ORDER BY id DESC LIMIT 5"
        )
        runs = cursor.fetchall()
    return users, films, ratings, runs
