"""Exports the database to gzipped CSV files for training the model on another computer."""

import csv
import gzip
import logging
import os

import pymysql.cursors

log = logging.getLogger("csfd")

TABLES = {
    "ratings.csv.gz": ("user_id,film_id,stars", "SELECT user_id, film_id, stars FROM ratings"),
    "films.csv.gz": ("film_id,parent_id,slug,title,year,kind",
                     "SELECT id, parent_id, slug, title, year, kind FROM films"),
    "users.csv.gz": ("user_id,slug,name,rating_count", "SELECT id, slug, name, rating_count FROM users WHERE status=2"),
}


def export(connection, directory):
    """Writes every table in TABLES to directory; each file appears only once it is complete."""
    os.makedirs(directory, exist_ok=True)
    for name, (header, sql) in TABLES.items():
        path = os.path.join(directory, name)
        rows = 0
        with gzip.open(path + ".tmp", "wt", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header.split(","))
            # An unbuffered cursor streams the rows instead of loading the whole table into memory.
            with connection.cursor(pymysql.cursors.SSCursor) as cursor:
                cursor.execute(sql)
                for row in cursor:
                    writer.writerow(row)
                    rows += 1
        os.replace(path + ".tmp", path)
        log.info("Exported %s rows to %s", rows, path)
