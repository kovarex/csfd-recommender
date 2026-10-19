"""Trains a matrix factorisation model on the exported ratings.

A rating is predicted as  mean + user bias + film bias + user vector · film vector.  The vectors are each user's and
film's "taste"; users who rate alike end up with similar vectors. Part of the ratings is held out to measure the error
(RMSE in stars) on ratings the model never saw.

    python3 -m ml.train --data ~/csfd-data --out model.npz
"""

import argparse
import math
import os
import time

import numpy as np
import torch

from .data import load_ratings


class Model(torch.nn.Module):
    def __init__(self, users, films, factors, mean):
        super().__init__()
        self.mean = mean
        self.user_vectors = torch.nn.Embedding(users, factors)
        self.film_vectors = torch.nn.Embedding(films, factors)
        self.user_bias = torch.nn.Embedding(users, 1)
        self.film_bias = torch.nn.Embedding(films, 1)
        for embedding in (self.user_vectors, self.film_vectors):
            torch.nn.init.normal_(embedding.weight, std=0.01)
        for embedding in (self.user_bias, self.film_bias):
            torch.nn.init.zeros_(embedding.weight)

    def forward(self, users, films):
        p, q = self.user_vectors(users), self.film_vectors(films)
        prediction = (self.mean + self.user_bias(users).squeeze(1) + self.film_bias(films).squeeze(1)
                      + (p * q).sum(1))
        # L2 on the rows used in this batch, so rarely seen users and films are pulled towards zero (the average).
        penalty = (p.pow(2).sum(1) + q.pow(2).sum(1)).mean()
        bias_penalty = (self.user_bias(users).pow(2) + self.film_bias(films).pow(2)).mean()
        return prediction, penalty, bias_penalty


def index(values):
    """Maps IDs to 0..n-1; returns the sorted unique IDs and each value's index."""
    ids, positions = np.unique(values, return_inverse=True)
    return ids, positions.astype(np.int64)


def filter_ratings(ratings, min_user_ratings, min_film_ratings):
    # A few rounds, because dropping films can push users under the limit and the other way round.
    for _ in range(3):
        film_counts = ratings["film_id"].map(ratings["film_id"].value_counts())
        user_counts = ratings["user_id"].map(ratings["user_id"].value_counts())
        ratings = ratings[(film_counts >= min_film_ratings) & (user_counts >= min_user_ratings)]
    return ratings


def rmse(model, users, films, stars, batch):
    model.eval()
    total = 0.0
    with torch.no_grad():
        for start in range(0, len(stars), batch):
            prediction, _, _ = model(users[start:start + batch], films[start:start + batch])
            total += (prediction.clamp(0, 5) - stars[start:start + batch]).pow(2).sum().item()
    model.train()
    return math.sqrt(total / max(1, len(stars)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=os.path.expanduser("~/csfd-data"), help="directory with the CSV export")
    parser.add_argument("--out", default="model.npz")
    parser.add_argument("--factors", type=int, default=64, help="length of the taste vectors")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=100_000)
    parser.add_argument("--lr", type=float, default=0.005)
    parser.add_argument("--reg", type=float, default=0.05, help="L2 regularisation of the vectors")
    parser.add_argument("--bias-reg", type=float, default=0.01, help="L2 regularisation of the biases")
    parser.add_argument("--holdout", type=float, default=0.05, help="share of ratings held out for validation")
    parser.add_argument("--min-user-ratings", type=int, default=5)
    parser.add_argument("--min-film-ratings", type=int, default=5)
    parser.add_argument("--patience", type=int, default=3, help="stop after this many epochs without improvement")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    started = time.time()
    ratings = load_ratings(args.data)
    print(f"Loaded {len(ratings):,} ratings in {time.time() - started:.0f} s")
    ratings = filter_ratings(ratings, args.min_user_ratings, args.min_film_ratings)
    user_ids, users = index(ratings["user_id"].to_numpy())
    film_ids, films = index(ratings["film_id"].to_numpy())
    stars = ratings["stars"].to_numpy(np.float32)
    print(f"Training on {len(stars):,} ratings, {len(user_ids):,} users, {len(film_ids):,} films, device {args.device}")

    rng = np.random.default_rng(args.seed)
    held_out = rng.random(len(stars)) < args.holdout
    device = torch.device(args.device)
    to_tensor = lambda a: torch.from_numpy(a).to(device)
    train_users, train_films, train_stars = (to_tensor(a[~held_out]) for a in (users, films, stars))
    test_users, test_films, test_stars = (to_tensor(a[held_out]) for a in (users, films, stars))

    mean = float(train_stars.mean())
    print(f"Baseline (everyone gets the mean {mean:.2f}): RMSE {math.sqrt(((test_stars - mean) ** 2).mean()):.4f}")
    model = Model(len(user_ids), len(film_ids), args.factors, mean).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    best, best_state, stale = float("inf"), None, 0
    for epoch in range(1, args.epochs + 1):
        order = torch.randperm(len(train_stars), device=device)
        for start in range(0, len(order), args.batch):
            batch = order[start:start + args.batch]
            prediction, penalty, bias_penalty = model(train_users[batch], train_films[batch])
            loss = (prediction - train_stars[batch]).pow(2).mean() + args.reg * penalty + args.bias_reg * bias_penalty
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        error = rmse(model, test_users, test_films, test_stars, args.batch * 10)
        print(f"Epoch {epoch}: validation RMSE {error:.4f} ({time.time() - started:.0f} s)")
        if error < best - 1e-4:
            best, stale = error, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
            if stale >= args.patience:
                break

    film_counts = np.bincount(films, minlength=len(film_ids))
    np.savez_compressed(
        args.out,
        mean=np.float32(mean),
        reg=np.float32(args.reg),
        user_ids=user_ids,
        user_vectors=best_state["user_vectors.weight"].numpy(),
        user_bias=best_state["user_bias.weight"].numpy()[:, 0],
        film_ids=film_ids,
        film_vectors=best_state["film_vectors.weight"].numpy(),
        film_bias=best_state["film_bias.weight"].numpy()[:, 0],
        film_counts=film_counts.astype(np.int32),
        validation_rmse=np.float32(best),
    )
    print(f"Best validation RMSE {best:.4f}; saved {args.out}")


if __name__ == "__main__":
    main()
