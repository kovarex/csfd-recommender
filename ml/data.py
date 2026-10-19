"""Loading the CSV export written by `python3 -m csfd.crawl export`."""

import os

import numpy as np
import pandas as pd


def load_ratings(directory):
    return pd.read_csv(os.path.join(directory, "ratings.csv.gz"),
                       dtype={"user_id": np.int32, "film_id": np.int32, "stars": np.int8})


def load_films(directory):
    films = pd.read_csv(os.path.join(directory, "films.csv.gz"), keep_default_na=False,
                        dtype={"film_id": np.int32, "slug": str, "title": str, "kind": str})
    films["parent_id"] = pd.to_numeric(films["parent_id"], errors="coerce").astype("Int32")
    films["year"] = pd.to_numeric(films["year"], errors="coerce").astype("Int32")
    return films.set_index("film_id")


def load_users(directory):
    return pd.read_csv(os.path.join(directory, "users.csv.gz"), keep_default_na=False,
                       dtype={"user_id": np.int32, "slug": str, "name": str}).set_index("user_id")


def load_genres(directory):
    """Part of the export (from the film_genres table)."""
    path = os.path.join(directory, "genres.csv.gz")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found; export again on NFS (genres are downloaded there weekly)")
    return pd.read_csv(path, dtype={"film_id": np.int32, "genre": str})


def csfd_url(film_id, slug, parent_id):
    parent = f"{int(parent_id)}/" if not pd.isna(parent_id) else ""
    return f"https://www.csfd.cz/film/{parent}{film_id}-{slug}/prehled/"
