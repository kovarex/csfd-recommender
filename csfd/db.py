"""MariaDB access."""

import functools
import json
import logging
import os
import sys
import time

import pymysql

WAITING, IN_PROGRESS, DONE, MISSING, FAILED = range(5)
# users.priority: queued by hand or as a priority user (scraped whole), and scraped again to pick up new ratings, which
# stops at the first page that brings nothing new (ČSFD lists ratings newest first).
PRIORITY, REFRESH = 1, 2

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
    ("films", "parent_id", "ALTER TABLE films ADD KEY parent_id (parent_id)"),
    ("film_stats", "kind_count", ["ALTER TABLE film_stats ADD COLUMN kind VARCHAR(40) NOT NULL DEFAULT '', "
                                  "ADD KEY kind_count (kind, rating_count), ADD KEY kind_average (kind, average), "
                                  "ADD KEY average (average)",
                                  "UPDATE film_stats s JOIN films f ON f.id = s.film_id SET s.kind = f.kind"]),
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
                for statement in [ddl] if isinstance(ddl, str) else ddl:
                    cursor.execute(statement)


# Users and films per query when rebuilding film_stats. The NFS database drops long-running queries, and one GROUP BY
# over all ratings got too long once there were ~11 M of them.
FILM_STATS_USER_STEP = 5000
FILM_STATS_FILM_STEP = 20000


@reconnecting
def run_statement(connection, sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


# film_stats' secondary indexes (as in schema.sql), built after the rows are written.
FILM_STATS_KEYS = {"rating_count": "rating_count", "average": "average", "kind_count": "kind, rating_count",
                   "kind_average": "kind, average"}


def kill_leftover_film_stats_queries(connection):
    """A rebuild killed by NFS can leave its statement running in the database, holding film_stats_new; the next
    rebuild would then wait for it until it is killed too."""
    for (thread,) in run_statement(
            connection, "SELECT id FROM information_schema.processlist WHERE id <> CONNECTION_ID() "
                        "AND user = SUBSTRING_INDEX(CURRENT_USER(), '@', 1) AND info LIKE '%%film\\_stats\\_new%%'"):
        log.warning("Killing a leftover film stats query (thread %s)", thread)
        try:
            run_statement(connection, f"KILL QUERY {int(thread)}")
        except pymysql.err.Error as e:
            log.warning("Could not kill it: %s", e)


def refresh_film_stats(connection):
    """Rebuilds film_stats from ratings and swaps it in atomically. Sums are read in short queries and added up here,
    so the database only writes the final rows."""
    set_state(connection, "film_stats_started_at", int(time.time()))
    kill_leftover_film_stats_queries(connection)
    counts, sums = {}, {}
    started = time.time()
    (low, high), = run_statement(connection, "SELECT MIN(user_id), MAX(user_id) FROM ratings")
    starts = range(low or 0, (high or -1) + 1, FILM_STATS_USER_STEP)
    for number, start in enumerate(starts, 1):
        # Plain reads of a primary key range: with a GROUP BY per range (probably a temporary table each time) the
        # rebuild was killed after 4 minutes on 16 M ratings on NFS, while series-stats reading every rating
        # finished within its 3 minutes.
        for film_id, stars in run_statement(
                connection, "SELECT film_id, stars FROM ratings WHERE user_id BETWEEN %s AND %s",
                (start, start + FILM_STATS_USER_STEP - 1)):
            counts[film_id] = counts.get(film_id, 0) + 1
            sums[film_id] = sums.get(film_id, 0) + stars
        if number % 10 == 0:
            set_state(connection, "film_stats_progress", f"{number}/{len(starts)} ranges in {time.time() - started:.0f} s")
    progress = lambda phase: set_state(connection, "film_stats_progress", f"{phase} ({time.time() - started:.0f} s)")
    progress(f"read {len(starts)} ranges, reading kinds")
    # Only the rated films' kinds are kept (and each kind string once): all films' kinds may not fit the task's memory.
    kinds = {}
    (low, high), = run_statement(connection, "SELECT MIN(id), MAX(id) FROM films")
    for start in range(low or 0, (high or -1) + 1, FILM_STATS_FILM_STEP):
        for film_id, kind in run_statement(connection, "SELECT id, kind FROM films WHERE id BETWEEN %s AND %s",
                                           (start, start + FILM_STATS_FILM_STEP - 1)):
            if film_id in counts:
                kinds[film_id] = sys.intern(kind or "")
    rows = [(film_id, count, round(sums[film_id] / count, 3), kinds.get(film_id, ""))
            for film_id, count in sorted(counts.items())]

    with connection.cursor() as cursor:
        # Fail instead of waiting for a lock until NFS kills the task.
        cursor.execute("SET SESSION lock_wait_timeout = 60")
        progress("creating film_stats_new")
        cursor.execute("DROP TABLE IF EXISTS film_stats_new")
        cursor.execute("CREATE TABLE film_stats_new LIKE film_stats")
        # Writing 150 k rows into the table with its four secondary indexes was stopped by the 5 minute limit on
        # NFS. The rows go in by primary key in one transaction and the indexes are built afterwards, in one pass.
        cursor.execute("ALTER TABLE film_stats_new " + ", ".join(f"DROP KEY {key}" for key in FILM_STATS_KEYS))
        connection.begin()
        for start in range(0, len(rows), 10000):
            progress(f"writing rows {start:,}/{len(rows):,}")
            cursor.executemany("INSERT INTO film_stats_new (film_id, rating_count, average, kind) "
                               "VALUES (%s, %s, %s, %s)", rows[start:start + 10000])
        connection.commit()
        progress("building indexes")
        cursor.execute("ALTER TABLE film_stats_new " + ", ".join(f"ADD KEY {key} ({columns})"
                                                                for key, columns in FILM_STATS_KEYS.items()))
        progress("swapping tables")
        cursor.execute("RENAME TABLE film_stats TO film_stats_old, film_stats_new TO film_stats")
        cursor.execute("DROP TABLE film_stats_old")

    # The kinds for the film list's filter, so the page doesn't count them on every load.
    per_kind = {}
    for _, _, _, kind in rows:
        per_kind[kind] = per_kind.get(kind, 0) + 1
    top = sorted(per_kind, key=lambda kind: (-per_kind[kind], kind))[:15]
    while len(json.dumps(top, ensure_ascii=False)) > 255:  # crawl_state.value is VARCHAR(255)
        top.pop()
    set_state(connection, "film_kinds", json.dumps(top, ensure_ascii=False))
    set_state(connection, "film_stats_at", int(time.time()))
    set_state(connection, "film_stats_progress", f"done in {time.time() - started:.0f} s")


def refresh_series_stats(connection):
    """Rebuilds series_stats from the episode ratings. Ratings are read in short queries per range of users and the
    episodes picked out here: joining films in the query made MariaDB scan all episode ratings for every range."""
    episode_series, season_series = {}, {}
    (low, high), = run_statement(connection, "SELECT MIN(id), MAX(id) FROM films")
    for start in range(low or 0, (high or -1) + 1, FILM_STATS_FILM_STEP):
        for film_id, parent_id, kind in run_statement(
                connection, "SELECT id, parent_id, kind FROM films WHERE kind IN ('série', 'epizoda') "
                            "AND parent_id IS NOT NULL AND id BETWEEN %s AND %s", (start, start + FILM_STATS_FILM_STEP - 1)):
            (season_series if kind == "série" else episode_series)[film_id] = parent_id
    episode_series = {episode: season_series.get(parent, parent) for episode, parent in episode_series.items()}
    spread_sums, users, episodes = {}, {}, {}
    (low, high), = run_statement(connection, "SELECT MIN(user_id), MAX(user_id) FROM ratings")
    for start in range(low or 0, (high or -1) + 1, FILM_STATS_USER_STEP):
        per_user = {}  # (user, series) -> [count, sum, sum of squares]
        for user_id, film_id, stars in run_statement(
                connection, "SELECT user_id, film_id, stars FROM ratings WHERE user_id BETWEEN %s AND %s",
                (start, start + FILM_STATS_USER_STEP - 1)):
            series = episode_series.get(film_id)
            if series is None:
                continue
            episodes.setdefault(series, set()).add(film_id)
            sums = per_user.setdefault((user_id, series), [0, 0, 0])
            sums[0] += 1
            sums[1] += stars
            sums[2] += stars * stars
        for (_, series), (count, total, squares) in per_user.items():
            if count >= 3:
                spread_sums[series] = spread_sums.get(series, 0.0) + max(0.0, squares / count - (total / count) ** 2) ** 0.5
                users[series] = users.get(series, 0) + 1
    rows = [(series, users[series], len(episodes[series]), round(spread_sums[series] / users[series], 3))
            for series in sorted(users)]
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION lock_wait_timeout = 60")
        cursor.execute("DROP TABLE IF EXISTS series_stats_new")
        cursor.execute("CREATE TABLE series_stats_new LIKE series_stats")
        for start in range(0, len(rows), 10000):
            cursor.executemany("INSERT INTO series_stats_new (series_id, users, episodes, spread) VALUES (%s, %s, %s, %s)",
                               rows[start:start + 10000])
        cursor.execute("RENAME TABLE series_stats TO series_stats_old, series_stats_new TO series_stats")
        cursor.execute("DROP TABLE series_stats_old")
    set_state(connection, "series_stats_at", int(time.time()))


@reconnecting
def save_genres(connection, film_genres):
    """Replaces film_genres with {film ID: set of genres}, swapping the new table in atomically."""
    rows = [(film, genre) for film, genres in film_genres.items() for genre in genres]
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION lock_wait_timeout = 60")
        cursor.execute("DROP TABLE IF EXISTS film_genres_new")
        cursor.execute("CREATE TABLE film_genres_new LIKE film_genres")
        # Like film_stats: inserting into the table with its secondary index was stopped by the time limit on NFS.
        cursor.execute("ALTER TABLE film_genres_new DROP KEY genre")
        connection.begin()
        for start in range(0, len(rows), 10000):
            cursor.executemany("INSERT INTO film_genres_new (film_id, genre) VALUES (%s, %s)", rows[start:start + 10000])
        connection.commit()
        cursor.execute("ALTER TABLE film_genres_new ADD KEY genre (genre, film_id)")
        # ČSFD's own genres win where its page was read; "antologie" stays, since ČSFD has no such genre.
        cursor.execute("DELETE n FROM film_genres_new n JOIN film_pages p ON p.film_id = n.film_id "
                       "WHERE n.genre <> 'antologie'")
        cursor.execute("INSERT IGNORE INTO film_genres_new (film_id, genre) SELECT film_id, genre FROM csfd_genres")
        cursor.execute("RENAME TABLE film_genres TO film_genres_old, film_genres_new TO film_genres")
        cursor.execute("DROP TABLE film_genres_old")
    set_state(connection, "genres_at", int(time.time()))


@reconnecting
def films_without_genres(connection, min_ratings, limit):
    """Films with at least min_ratings ratings whose ČSFD page wasn't read yet, the best rated first: those are the
    ones the recommendations show at the top."""
    return [row[0] for row in run_statement(
        connection, "SELECT s.film_id FROM film_stats s LEFT JOIN film_pages p ON p.film_id = s.film_id "
                    "WHERE s.rating_count >= %s AND p.film_id IS NULL ORDER BY s.average DESC LIMIT %s",
        (min_ratings, limit))]


@reconnecting
def save_film_genres(connection, pages):
    """Stores [(film ID, genres)] read from the films' ČSFD pages, in one transaction (single writes are slow on NFS).
    In film_genres, which the web reads, they replace Wikidata's (except "antologie", which ČSFD doesn't have)."""
    if not pages:
        return
    ids = [film_id for film_id, _ in pages]
    rows = [(film_id, genre) for film_id, genres in pages for genre in genres]
    marks = ",".join(["%s"] * len(ids))
    connection.begin()
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM csfd_genres WHERE film_id IN ({marks})", ids)
            cursor.execute(f"DELETE FROM film_genres WHERE film_id IN ({marks}) AND genre <> 'antologie'", ids)
            if rows:
                cursor.executemany("INSERT INTO csfd_genres (film_id, genre) VALUES (%s, %s)", rows)
                cursor.executemany("INSERT IGNORE INTO film_genres (film_id, genre) VALUES (%s, %s)", rows)
            cursor.executemany("INSERT INTO film_pages (film_id, fetched_at) VALUES (%s, NOW()) "
                               "ON DUPLICATE KEY UPDATE fetched_at=NOW()", [(film_id,) for film_id in ids])
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@reconnecting
def get_state(connection, name, default=None):
    with connection.cursor() as cursor:
        cursor.execute("SELECT value FROM crawl_state WHERE name=%s", (name,))
        row = cursor.fetchone()
    return row[0] if row else default


@reconnecting
def clear_state(connection, name):
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM crawl_state WHERE name=%s", (name,))


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
def prioritize_users(connection, user_ids, rescrape_hours=None):
    """Moves users to the front of the queue unless they are already scraped; with rescrape_hours, also users
    scraped longer ago than that, so their new ratings show up. Safe to repeat on every run."""
    with connection.cursor() as cursor:
        # Constants in the text: executemany can't take parameters after VALUES (...).
        cursor.executemany(f"INSERT INTO users (id, priority) VALUES (%s, {PRIORITY}) ON DUPLICATE KEY UPDATE "
                           f"priority = IF(status = {DONE} OR priority = {REFRESH}, priority, {PRIORITY})",
                           [(user_id,) for user_id in user_ids])
        if rescrape_hours is not None and user_ids:
            cursor.execute(f"UPDATE users SET status=%s, pages_done=0, priority=%s, error=NULL "
                           f"WHERE id IN ({','.join(['%s'] * len(user_ids))}) AND status=%s "
                           f"AND scraped_at < NOW() - INTERVAL %s HOUR",
                           (WAITING, REFRESH, *user_ids, DONE, rescrape_hours))


@reconnecting
def save_fans(connection, user_id, fan_ids):
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM fans WHERE user_id=%s", (user_id,))
        cursor.executemany("INSERT INTO fans (user_id, fan_id) VALUES (%s, %s)", [(user_id, fan) for fan in fan_ids])


@reconnecting
def get_fans(connection, user_ids):
    if not user_ids:
        return []
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT DISTINCT fan_id FROM fans WHERE user_id IN ({','.join(['%s'] * len(user_ids))}) "
                       f"ORDER BY fan_id", user_ids)
        return [row[0] for row in cursor.fetchall()]


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
        cursor.execute("SELECT id, pages_done, priority FROM users WHERE status=%s ORDER BY priority DESC, id LIMIT 1",
                       (WAITING,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE users SET status=%s WHERE id=%s", (IN_PROGRESS, row[0]))
    return row


@reconnecting
def save_page(connection, user_id, page, page_number):
    """Stores one parsed ratings page and records it as done for the user. Returns how many of its ratings were new
    or changed."""
    changed = 0
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
            changed = cursor.executemany(
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
    return changed


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
def close_killed_runs(connection):
    """Marks runs that never recorded their end; called while holding the crawl lock, so none of them is running."""
    with connection.cursor() as cursor:
        cursor.execute("UPDATE crawl_runs SET stop_reason='killed before it could finish' "
                       "WHERE finished_at IS NULL AND stop_reason IS NULL")


@reconnecting
def update_run(connection, run_id, stats, fetcher, stop_reason=None):
    """Saves a run's counters; with stop_reason also its end. Called every minute too, so a killed run keeps them."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE crawl_runs SET requests=%s, credits_used=%s, credits_remaining=%s, users_done=%s, ratings_saved=%s"
            + (", finished_at=NOW(), stop_reason=%s" if stop_reason else "") + " WHERE id=%s",
            (fetcher.requests, fetcher.credits_used, fetcher.credits_remaining, stats["users"], stats["ratings"])
            + ((stop_reason[:255],) if stop_reason else ()) + (run_id,),
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
