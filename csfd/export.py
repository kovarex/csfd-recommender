"""Exports the database to gzipped CSV files for training the model on another computer."""

import csv
import gzip
import logging
import os

from . import db

log = logging.getLogger("csfd")

# File name: (header, query for one range of the key column, key column, table, key range per query).
# The NFS database drops long-running queries, so every table is read in short chunks over ranges of its key.
TABLES = {
    "ratings.csv.gz": ("user_id,film_id,stars",
                       "SELECT user_id, film_id, stars FROM ratings WHERE user_id BETWEEN %s AND %s",
                       "user_id", "ratings", 200),
    "films.csv.gz": ("film_id,parent_id,slug,title,year,kind",
                     "SELECT id, parent_id, slug, title, year, kind FROM films WHERE id BETWEEN %s AND %s",
                     "id", "films", 20000),
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


def export(connection, directory):
    """Writes every table in TABLES to directory; each file appears only once it is complete."""
    os.makedirs(directory, exist_ok=True)
    for name, (header, sql, key, table, step) in TABLES.items():
        path = os.path.join(directory, name)
        low, high = key_range(connection, table, key)
        rows = 0
        with gzip.open(path + ".tmp", "wt", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header.split(","))
            for start in range(low or 0, (high or -1) + 1, step):
                chunk = fetch(connection, sql, start, start + step - 1)
                writer.writerows(chunk)
                if rows // 1_000_000 != (rows + len(chunk)) // 1_000_000:
                    log.info("%s: %s rows so far", name, rows + len(chunk))
                rows += len(chunk)
        os.replace(path + ".tmp", path)
        log.info("Exported %s rows to %s", rows, path)
