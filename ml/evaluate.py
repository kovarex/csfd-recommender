"""How well the model guesses ratings a user already gave, as if it didn't know them.

The user's ratings are split into parts (5 by default); each part is hidden in turn, the user's taste is folded in
from the rest, and the hidden ratings are predicted. The errors are compared with simple guesses, so it shows what the
taste vector adds, and a table shows the guesses for each number of stars actually given. For a fair test, train with
the users left out (`ml.train --exclude-users 63015`), otherwise the film vectors have seen their ratings.

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


def random_users(ratings, model, count, rng, min_ratings=50):
    counts = ratings.loc[ratings["film_id"].isin(model["film_ids"]), "user_id"].value_counts()
    return [int(i) for i in rng.choice(counts[counts >= min_ratings].index.to_numpy(), count, replace=False)]


def evaluate_users(model, ratings, user_ids, folds, rng, skipped=print):
    """For each user with enough ratings of films the model knows: (user_id, their ratings, stars, guesses)."""
    film_index = pd.Series(np.arange(len(model["film_ids"])), index=model["film_ids"])
    ratings = ratings[ratings["user_id"].isin(user_ids) & ratings["film_id"].isin(film_index.index)]
    results = []
    for user_id, mine in ratings.groupby("user_id", sort=False):
        if len(mine) < folds * 2:
            skipped(f"User {user_id}: only {len(mine)} ratings of films the model knows, skipped")
            continue
        stars = mine["stars"].to_numpy(np.float32)
        results.append((user_id, mine, stars,
                        cross_validate(model, film_index[mine["film_id"]].to_numpy(), stars, folds, rng)))
    return results


def metrics(stars, values):
    """RMSE, MAE, rank agreement (Spearman) and the user's average over the tenth guessed highest."""
    error = values - stars
    rank = pd.Series(values).rank().corr(pd.Series(stars).rank())  # Spearman, without scipy
    top = max(1, len(stars) // 10)
    return (float(np.sqrt((error ** 2).mean())), float(np.abs(error).mean()), float(rank),
            float(stars[np.argsort(-values, kind="stable")[:top]].mean()))


def report(stars, guesses):
    print(f"  {'':32}  RMSE   MAE  pořadí*  top 10 %**")
    for name, values in guesses.items():
        rmse, mae, rank, best = metrics(stars, values)
        print(f"  {name:32}  {rmse:.3f}  {mae:.3f}  {rank:7.3f}  {best:.2f}")
    print(f"  * shoda pořadí odhadů se skutečnými hodnoceními (Spearman, 1 = stejné pořadí)\n"
          f"  ** skutečný průměr u desetiny filmů, které odhad dal nejvýš (celkový průměr {stars.mean():.2f})")
    error = guesses["model"] - stars
    print(f"  Odhad modelu proti skutečnosti: průměrná odchylka {error.mean():+.2f}, směrodatná odchylka "
          f"{error.std():.2f}; v rozmezí ±0,5 hvězdy {(np.abs(error) <= 0.5).mean():.0%}, ±1 {(np.abs(error) <= 1).mean():.0%}")
    print(f"  {'skutečně':>9}  {'počet':>6}  {'průměrný odhad':>14}  {'odchylka':>8}  {'směr. odch.':>11}")
    for star in range(6):
        given = stars == star
        if given.any():
            e = guesses["model"][given] - star
            label = "odpad" if star == 0 else "★" * star
            print(f"  {label:>9}  {given.sum():6}  {guesses['model'][given].mean():14.2f}  {e.mean():+8.2f}"
                  f"  {e.std():11.2f}")


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
    rng = np.random.default_rng(args.seed)
    user_ids = (random_users(ratings, model, args.random, rng) if args.random
                else [int(i) for i in args.users.split(",")])
    results = evaluate_users(model, ratings, user_ids, args.folds, rng)
    if len(user_ids) <= 10:
        for user_id, mine, stars, guesses in results:
            print(f"\nUser {user_id}: {len(stars)} ratings, each guessed from the other {args.folds - 1}/{args.folds}")
            report(stars, guesses)
            if args.misses:
                print("  Nejvíc vedle (odhad → skutečnost):")
                for i in np.argsort(-np.abs(guesses["model"] - stars))[:args.misses]:
                    film = films.loc[mine["film_id"].iloc[i]]
                    print(f"    {guesses['model'][i]:.1f} → {stars[i]:.0f}  {film['title']} ({film['year']}, "
                          f"{film['kind']})")
    if len(results) > 1:
        print(f"\nAll {len(results)} users together:")
        report(np.concatenate([r[2] for r in results]),
               {name: np.concatenate([r[3][name] for r in results]) for name in results[0][3]})


if __name__ == "__main__":
    main()
