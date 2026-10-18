"""MariaDB access."""

import os

import pymysql

WAITING, IN_PROGRESS, DONE, MISSING, FAILED = range(5)

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def connect(config):
    section = config["mysql"]
    return pymysql.connect(
        host=section.get("host", "localhost"),
        port=section.getint("port", 3306),
        user=section["user"],
        password=section["password"],
        database=section["database"],
        charset="utf8mb4",
        autocommit=True,
    )


def create_schema(connection):
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        statements = [s.strip() for s in f.read().split(";")]
    with connection.cursor() as cursor:
        for statement in statements:
            lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
            if "".join(lines).strip():
                cursor.execute("\n".join(lines))


def get_state(connection, name, default=None):
    with connection.cursor() as cursor:
        cursor.execute("SELECT value FROM crawl_state WHERE name=%s", (name,))
        row = cursor.fetchone()
    return row[0] if row else default


def set_state(connection, name, value):
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO crawl_state (name, value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE value=VALUES(value)",
            (name, str(value)),
        )


def add_users(connection, user_ids):
    with connection.cursor() as cursor:
        cursor.executemany("INSERT IGNORE INTO users (id) VALUES (%s)", [(i,) for i in user_ids])


def requeue_user(connection, user_id):
    """Scrape the user again from the first page (keeps ratings already stored, updates changed ones)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id) VALUES (%s) ON DUPLICATE KEY UPDATE status=%s, pages_done=0, error=NULL",
            (user_id, WAITING),
        )


def reset_interrupted(connection):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE users SET status=%s WHERE status=%s", (WAITING, IN_PROGRESS))


def claim_waiting_user(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, pages_done FROM users WHERE status=%s ORDER BY id LIMIT 1", (WAITING,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE users SET status=%s WHERE id=%s", (IN_PROGRESS, row[0]))
    return row


def save_page(connection, user_id, page, page_number):
    """Stores one parsed ratings page and records it as done for the user."""
    with connection.cursor() as cursor:
        if page.ratings:
            cursor.executemany(
                "INSERT INTO films (id, parent_id, slug, title, year, kind) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE parent_id=VALUES(parent_id), slug=VALUES(slug), title=VALUES(title), "
                "year=VALUES(year), kind=VALUES(kind)",
                [(r.film_id, r.parent_id, r.slug, r.title[:300], r.year, r.kind[:40]) for r in page.ratings],
            )
            cursor.executemany(
                "INSERT INTO ratings (user_id, film_id, stars, rated_on) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE stars=VALUES(stars), rated_on=VALUES(rated_on)",
                [(user_id, r.film_id, r.stars, r.rated_on) for r in page.ratings],
            )
        cursor.execute(
            "UPDATE users SET slug=COALESCE(%s, slug), name=COALESCE(%s, name), page_count=%s, pages_done=%s "
            "WHERE id=%s",
            (page.user_slug, page.user_name, page.page_count, page_number, user_id),
        )


def finish_user(connection, user_id, status, error=None):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE users SET status=%s, error=%s, scraped_at=NOW(), "
            "rating_count=(SELECT COUNT(*) FROM ratings WHERE user_id=%s) WHERE id=%s",
            (status, error[:255] if error else None, user_id, user_id),
        )


def release_user(connection, user_id):
    """Puts an unfinished user back in the queue; the next run continues after pages_done."""
    with connection.cursor() as cursor:
        cursor.execute("UPDATE users SET status=%s WHERE id=%s", (WAITING, user_id))


def start_run(connection):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO crawl_runs (started_at) VALUES (NOW())")
        return cursor.lastrowid


def finish_run(connection, run_id, stats, fetcher, stop_reason):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE crawl_runs SET finished_at=NOW(), requests=%s, credits_used=%s, credits_remaining=%s, "
            "users_done=%s, ratings_saved=%s, stop_reason=%s WHERE id=%s",
            (fetcher.requests, fetcher.credits_used, fetcher.credits_remaining, stats["users"], stats["ratings"],
             stop_reason[:255], run_id),
        )


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
