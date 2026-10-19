"""Recommends films for a ČSFD user from a trained model.

The user's taste vector is computed from their ratings against the trained film vectors ("fold-in"), so it works for
anyone whose ratings are in the export, also users added after training, and it is the same computation the web page
will do.

    python3 -m ml.recommend --data ~/csfd-data --model model.npz --user 63015
"""

import argparse
import os

import numpy as np
import pandas as pd

from .data import csfd_url, load_films, load_genres, load_ratings

# Seasons and episodes have their own ratings on ČSFD, but recommending them next to whole films isn't useful.
DEFAULT_EXCLUDED_KINDS = "série,epizoda,hudební videoklip"


def fold_in(model, film_positions, stars):
    """Least-squares taste vector and bias of a user from their ratings of the given films (indexes into the model)."""
    q = model["film_vectors"][film_positions]
    a = np.hstack([q, np.ones((len(q), 1), np.float32)])
    target = stars - model["mean"] - model["film_bias"][film_positions]
    # Same regularisation as in training, where every rating adds reg · |vector|² to the loss.
    regularisation = float(model["reg"]) * len(stars) * np.eye(a.shape[1], dtype=np.float32)
    regularisation[-1, -1] = 1e-3 * len(stars)
    solution = np.linalg.solve(a.T @ a + regularisation, a.T @ target)
    return solution[:-1], solution[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=os.path.expanduser("~/csfd-data"))
    parser.add_argument("--model", default="model.npz")
    parser.add_argument("--user", type=int, required=True, help="numeric ČSFD user ID")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--min-ratings", type=int, default=100, help="only films with at least this many ratings")
    parser.add_argument("--exclude-kinds", default=DEFAULT_EXCLUDED_KINDS)
    parser.add_argument("--genre", help="only these genres, comma separated (e.g. scifi,fantasy; needs ml.genres)")
    args = parser.parse_args()

    model = dict(np.load(args.model))
    ratings = load_ratings(args.data)
    films = load_films(args.data)
    mine = ratings[ratings["user_id"] == args.user]
    if mine.empty:
        raise SystemExit(f"User {args.user} has no ratings in the export.")

    film_index = {film_id: i for i, film_id in enumerate(model["film_ids"])}
    known = mine[mine["film_id"].isin(film_index)]
    positions = known["film_id"].map(film_index).to_numpy()
    vector, bias = fold_in(model, positions, known["stars"].to_numpy(np.float32))
    print(f"User {args.user}: {len(mine)} ratings, {len(known)} of them on films the model knows")

    predicted = model["mean"] + bias + model["film_bias"] + model["film_vectors"] @ vector
    candidates = np.ones(len(predicted), bool)
    candidates[positions] = False
    candidates &= model["film_counts"] >= args.min_ratings
    excluded = set(filter(None, args.exclude_kinds.split(",")))
    kinds = films["kind"].reindex(model["film_ids"]).to_numpy()
    candidates &= ~np.isin(kinds, list(excluded))
    if args.genre:
        genres = load_genres(args.data)
        wanted = genres[genres["genre"].isin(args.genre.split(","))]["film_id"]
        in_genre = np.isin(model["film_ids"], wanted.to_numpy())
        print(f"{in_genre.sum():,} films the model knows are {args.genre} (genres from Wikidata)")
        candidates &= in_genre

    best = np.argsort(-np.where(candidates, predicted, -np.inf))[:args.top]
    for i in best:
        film = films.loc[model["film_ids"][i]]
        year = f" ({film['year']})" if not pd.isna(film["year"]) else ""
        print(f"{min(predicted[i], 5):4.2f} ★  {film['title']}{year}  [{film['kind']}, {model['film_counts'][i]} hodnocení]"
              f"  {csfd_url(model['film_ids'][i], film['slug'], film['parent_id'])}")


if __name__ == "__main__":
    main()
