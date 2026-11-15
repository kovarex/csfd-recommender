"""How well the model guesses ratings a user already gave, as if it didn't know them.

The user's ratings are split into parts (5 by default); each part is hidden in turn, the user's taste is folded in
from the rest, and the hidden ratings are predicted. The errors are compared with simple guesses, so it shows what the
taste vector adds. For a fair test, train with the user left out (`ml.train --exclude-users 63015`), otherwise the
film vectors have seen their ratings.

    python3 -m ml.evaluate --data ~/csfd-data --model model.npz --users 63015
    python3 -m ml.evaluate --data ~/csfd-data --model model.npz --random 1000
"""

import argparse
import os

import numpy as np
import pandas as pd

from .data import load_films, load_ratings
from .recommend import fold_in


def cross_validate(model, positions, stars, folds, rng):
    """Predictions of every rating from the user's other ratings, and the guesses to compare with."""
    part = rng.permutation(len(stars)) % folds
    predicted, film_average, user_adjusted = (np.empty(len(stars), np.float32) for _ in range(3))
    base = model["mean"] + model["film_bias"][positions]
    for k in range(folds):
        hidden, known = part == k, part != k
        vector, bias = fold_in(model, positions[known], stars[known])
        predicted[hidden] = base[hidden] + bias + model["film_vectors"][positions[hidden]] @ vector
        film_average[hidden] = base[hidden]
        user_adjusted[hidden] = base[hidden] + (stars[known] - base[known]).mean()
    return {name: np.clip(values, 0, 5) for name, values in
            (("model", predicted), ("film average", film_average), ("film average + your strictness", user_adjusted))}


def report(stars, guesses):
    print(f"  {'':32}  RMSE   MAE  pořadí*  top 10 % průměr**")
    top = max(1, len(stars) // 10)
    for name, values in guesses.items():
        error = values - stars
        rank = pd.Series(values).rank().corr(pd.Series(stars).rank())  # Spearman, without scipy
        best = stars[np.argsort(-values, kind="stable")[:top]].mean()
        print(f"  {name:32}  {np.sqrt((error ** 2).mean()):.3f}  {np.abs(error).mean():.3f}  {rank:7.3f}"
              f"  {best:.2f}")
    print(f"  * shoda pořadí odhadů s tvými hodnoceními (Spearman, 1 = stejné pořadí)\n"
          f"  ** tvůj průměr u desetiny filmů, které odhad dal nejvýš (tvůj celkový průměr {stars.mean():.2f})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=os.path.expanduser("~/csfd-data"))
    parser.add_argument("--model", default="model.npz")
    parser.add_argument("--users", default="63015", help="numeric ČSFD user IDs, comma separated")
    parser.add_argument("--random", type=int, default=0,
                        help="instead of --users, this many random users with at least 50 ratings (only the total "
                             "is printed); a measure of the model for everyone")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--misses", type=int, default=10, help="show this many of the biggest misses per user")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    model = dict(np.load(args.model))
    ratings = load_ratings(args.data)
    films = load_films(args.data)
    film_index = pd.Series(np.arange(len(model["film_ids"])), index=model["film_ids"])
    all_stars, all_guesses = [], []
    rng = np.random.default_rng(args.seed)
    if args.random:
        counts = ratings.loc[ratings["film_id"].isin(film_index.index), "user_id"].value_counts()
        user_ids = rng.choice(counts[counts >= 50].index.to_numpy(), args.random, replace=False)
    else:
        user_ids = [int(i) for i in args.users.split(",")]
    verbose = len(user_ids) <= 10
    for user_id in user_ids:
        mine = ratings[(ratings["user_id"] == user_id) & ratings["film_id"].isin(film_index.index)]
        if len(mine) < args.folds * 2:
            print(f"User {user_id}: only {len(mine)} ratings of films the model knows, skipped")
            continue
        positions = film_index[mine["film_id"]].to_numpy()
        stars = mine["stars"].to_numpy(np.float32)
        guesses = cross_validate(model, positions, stars, args.folds, rng)
        all_stars.append(stars)
        all_guesses.append(guesses)
        if not verbose:
            continue
        print(f"\nUser {user_id}: {len(stars)} ratings, each guessed from the other {args.folds - 1}/{args.folds}")
        report(stars, guesses)
        if args.misses:
            print("  Nejvíc vedle (odhad → tvoje hodnocení):")
            for i in np.argsort(-np.abs(guesses["model"] - stars))[:args.misses]:
                film = films.loc[mine["film_id"].iloc[i]]
                print(f"    {guesses['model'][i]:.1f} → {stars[i]:.0f}  {film['title']} ({film['year']}, {film['kind']})")
    if len(all_stars) > 1:
        print(f"\nAll {len(all_stars)} users together:")
        report(np.concatenate(all_stars), {name: np.concatenate([g[name] for g in all_guesses])
                                           for name in all_guesses[0]})


if __name__ == "__main__":
    main()
