"""Lists the users whose taste vector is closest to and farthest from a user's ("taste twins" and opposites).

Closeness is the cosine of the angle between the taste vectors: 1 the same taste, 0 unrelated, -1 the opposite. It
ignores how strict a user is (the bias), so a user who rates everything one star lower can still be a twin. For each
listed user it also shows how many films you both rated and by how many stars you differ on them on average.

    python3 -m ml.twins --data ~/csfd-data --model model.npz --user 63015
"""

import argparse
import os

import numpy as np

from .data import csfd_user_url, load_ratings, load_users
from .recommend import cosine, taste


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=os.path.expanduser("~/csfd-data"))
    parser.add_argument("--model", default="model.npz")
    parser.add_argument("--user", type=int, required=True, help="numeric ČSFD user ID")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-ratings", type=int, default=200,
                        help="only users with at least this many ratings; vectors of users with few are mostly noise")
    args = parser.parse_args()

    model = dict(np.load(args.model))
    ratings = load_ratings(args.data)
    users = load_users(args.data)
    mine, _, vector, _ = taste(model, ratings, args.user)

    counts = ratings["user_id"].value_counts().reindex(model["user_ids"]).fillna(0).to_numpy()
    candidates = (counts >= args.min_ratings) & (model["user_ids"] != args.user)
    similarity = cosine(model["user_vectors"], vector)
    order = np.argsort(np.where(candidates, -similarity, np.inf))
    closest = order[:args.top]
    farthest = order[:candidates.sum()][::-1][:args.top]

    my_stars = mine.set_index("film_id")["stars"]
    for title, rows in (("Nejpodobnější vkus", closest), ("Nejodlišnější vkus", farthest)):
        print(f"\n{title}\nshoda  hodnocení  společných  rozdíl")
        for i in rows:
            user_id = model["user_ids"][i]
            theirs = ratings[ratings["user_id"] == user_id].set_index("film_id")["stars"]
            common = my_stars.index.intersection(theirs.index)
            difference = (my_stars[common] - theirs[common]).abs().mean() if len(common) else float("nan")
            user = users.loc[user_id] if user_id in users.index else None
            name = user["name"] if user is not None else "?"
            url = csfd_user_url(user_id, user["slug"]) if user is not None else ""
            print(f"{similarity[i]:+5.2f}  {int(counts[i]):9}  {len(common):10}  {difference:6.2f}  {name}  {url}")


if __name__ == "__main__":
    main()
