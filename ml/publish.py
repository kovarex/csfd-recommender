"""Writes the trained model in the binary format the web pages read (web/model.php).

The web computes each user's taste vector from their ratings in the database, like ml.recommend, so it only needs
the films' vectors and, for the "similar users" list, the vectors of users with enough ratings. Rarely rated films
are left out: they would rarely be recommended and they make the file and the page slower.

    python3 -m ml.publish --data ~/csfd-data --model model.npz --out web-model.bin

Format: one line of JSON (the header, ending with a newline), then little-endian arrays in this order:
film_ids uint32[films], film_bias float32[films], film_counts uint32[films], film_average float32[films] (the
prediction for an average viewer), film_flags uint8[films] (1 = may be recommended, i.e. not a season, episode or
music video), film_vectors float32[films × factors], user_ids uint32[users], user_counts uint32[users],
user_vectors float32[users × factors].
"""

import argparse
import json
import os
import time

import numpy as np

from .data import load_films, load_ratings
from .recommend import DEFAULT_EXCLUDED_KINDS


def publish(model, films, user_counts, out, min_film_ratings, min_user_ratings, trained_at):
    keep = model["film_counts"] >= min_film_ratings
    average = (model["mean"] + model["user_bias"].mean() + model["film_bias"]
               + model["film_vectors"] @ model["user_vectors"].mean(0))
    kinds = films["kind"].reindex(model["film_ids"]).fillna("").to_numpy()
    flags = ~np.isin(kinds, DEFAULT_EXCLUDED_KINDS.split(","))
    counts = user_counts.reindex(model["user_ids"]).fillna(0).to_numpy()
    keep_users = counts >= min_user_ratings
    header = {
        "version": 1,
        "factors": int(model["film_vectors"].shape[1]),
        "films": int(keep.sum()),
        "users": int(keep_users.sum()),
        "mean": float(model["mean"]),
        "reg": float(model["reg"]),
        "validation_rmse": float(model["validation_rmse"]),
        "ratings": int(model["film_counts"].sum()),
        "trained_at": int(trained_at),
    }
    with open(out + ".tmp", "wb") as f:
        f.write(json.dumps(header).encode() + b"\n")
        for array, dtype in (
                (model["film_ids"][keep], "<u4"),
                (model["film_bias"][keep], "<f4"),
                (model["film_counts"][keep], "<u4"),
                (average[keep], "<f4"),
                (flags[keep], "u1"),
                (model["film_vectors"][keep], "<f4"),
                (model["user_ids"][keep_users], "<u4"),
                (counts[keep_users], "<u4"),
                (model["user_vectors"][keep_users], "<f4")):
            f.write(np.ascontiguousarray(array, dtype=dtype).tobytes())
    os.replace(out + ".tmp", out)
    return header


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=os.path.expanduser("~/csfd-data"))
    parser.add_argument("--model", default="model.npz")
    parser.add_argument("--out", default="web-model.bin")
    parser.add_argument("--min-film-ratings", type=int, default=30)
    parser.add_argument("--min-user-ratings", type=int, default=200, help="users offered as similar or opposite")
    args = parser.parse_args()

    model = dict(np.load(args.model))
    user_counts = load_ratings(args.data)["user_id"].value_counts()
    header = publish(model, load_films(args.data), user_counts, args.out, args.min_film_ratings,
                     args.min_user_ratings, os.path.getmtime(args.model) if os.path.exists(args.model) else time.time())
    print(f"Wrote {args.out}: {header['films']:,} films, {header['users']:,} users, "
          f"{os.path.getsize(args.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
