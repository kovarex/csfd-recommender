"""Exports the database to gzipped CSV files for training the model on another computer."""

import csv
import gzip
import hashlib
import logging
import os
import shutil
import time

from . import db

log = logging.getLogger("csfd")

# File name: (header, query for one range of the key column, key column, table, key range per query).
# The NFS database drops long-running queries, so every table is read in short chunks over ranges of its key.
TABLES = {
    "ratings.csv.gz": ("user_id,film_id,stars",
                       "SELECT user_id, film_id, CONCAT(stars, IF(half, '.5', '')) FROM ratings "
                       "WHERE user_id BETWEEN %s AND %s",
                       "user_id", "ratings", 200),
    "films.csv.gz": ("film_id,parent_id,slug,title,year,kind",
                     "SELECT id, parent_id, slug, title, year, kind FROM films WHERE id BETWEEN %s AND %s",
                     "id", "films", 20000),
    "genres.csv.gz": ("film_id,genre", "SELECT film_id, genre FROM film_genres WHERE film_id BETWEEN %s AND %s",
                      "film_id", "film_genres", 20000),
    "users.csv.gz": ("user_id,slug,name,rating_count",
                     "SELECT id, slug, name, rating_count FROM users WHERE status=2 AND id BETWEEN %s AND %s",
                     "id", "users", 50000),
}


@db.reconnecting
def key_range(connection, table, key):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT MIN({key}), MAX({key}) FROM {table}")
        return cursor.fetchone()


@db.reconnecting
def fetch(connection, sql, low, high):
    with connection.cursor() as cursor:
        cursor.execute(sql, (low, high))
        return cursor.fetchall()


def write_table(connection, path, header, sql, low, high, step):
    """Writes the rows of sql for key ranges from low to high to a gzipped CSV; the file appears only once complete."""
    rows = 0
    with gzip.open(path + ".tmp", "wt", encoding="utf-8", newline="", compresslevel=6) as f:
        writer = csv.writer(f)
        writer.writerow(header.split(","))
        for start in range(low, high + 1, step):
            chunk = fetch(connection, sql, start, min(start + step - 1, high))
            writer.writerows(chunk)
            if rows // 1_000_000 != (rows + len(chunk)) // 1_000_000:
                log.info("%s: %s rows so far", os.path.basename(path), rows + len(chunk))
            rows += len(chunk)
    os.replace(path + ".tmp", path)
    return rows


def export(connection, directory):
    """Writes every table in TABLES to directory; each file appears only once it is complete."""
    os.makedirs(directory, exist_ok=True)
    for name, (header, sql, key, table, step) in TABLES.items():
        low, high = key_range(connection, table, key)
        rows = write_table(connection, os.path.join(directory, name), header, sql, low or 0,
                           high if high is not None else -1, step)
        log.info("Exported %s rows to %s", rows, name)


# User IDs per ratings file of the published export: small enough for one file to be written within a run on NFS
# (the oldest, most active IDs have up to a few million ratings per file).
RATINGS_USER_STEP = 5000


def published_name(token):
    """The published export lives in a directory named by this hash of the scrape.do token: secret, yet known on both
    sides without another password."""
    return hashlib.sha256(f"csfd-export:{token}".encode()).hexdigest()[:32]


def published_at(base):
    """When the current published export was completed (0 = none yet)."""
    path = os.path.join(base, "current", "manifest.txt")
    return os.path.getmtime(path) if os.path.exists(path) else 0


def export_step(connection, base, seconds):
    """Continues the export for download in base/next: ratings in files of RATINGS_USER_STEP user IDs, then the other
    tables and manifest.txt (size and name of every file). Writes at least one file and starts no more once seconds
    have passed, so it is spread over runs. When complete, base/next replaces base/current. Returns True when it was published."""
    deadline = time.time() + seconds
    work = os.path.join(base, "next")
    os.makedirs(work, exist_ok=True)
    high_path = os.path.join(work, "high")
    if not os.path.exists(high_path):
        _, high = key_range(connection, "ratings", "user_id")
        with open(high_path, "w") as f:
            f.write(str(high if high is not None else -1))
    with open(high_path) as f:
        high = int(f.read())
    # (file name, its table in TABLES, the key range or None for the whole table)
    files = [(f"ratings-{index:04d}.csv.gz", "ratings.csv.gz",
              (index * RATINGS_USER_STEP, min((index + 1) * RATINGS_USER_STEP - 1, high)))
             for index in range(high // RATINGS_USER_STEP + 1)]
    files += [(name, name, None) for name in TABLES if name != "ratings.csv.gz"]
    written = False
    for name, table_file, key_bounds in files:
        path = os.path.join(work, name)
        if os.path.exists(path):
            continue
        if written and time.time() > deadline:
            return False
        header, sql, key, table, step = TABLES[table_file]
        if key_bounds is None:
            low, top = key_range(connection, table, key)
            key_bounds = (low or 0, top if top is not None else -1)
        rows = write_table(connection, path, header, sql, *key_bounds, step)
        log.info("Exported %s rows to %s", rows, name)
        written = True
    with open(os.path.join(work, "manifest.txt.tmp"), "w") as f:
        f.write(f"# ČSFD export {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}: size and name of every file\n")
        for name, _, _ in files:
            f.write(f"{os.path.getsize(os.path.join(work, name))} {name}\n")
    os.replace(os.path.join(work, "manifest.txt.tmp"), os.path.join(work, "manifest.txt"))
    os.remove(high_path)
    current, old = os.path.join(base, "current"), os.path.join(base, "old")
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(current):
        os.rename(current, old)
    os.rename(work, current)
    shutil.rmtree(old, ignore_errors=True)
    return True
