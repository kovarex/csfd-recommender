"""Trains the model with several settings and compares them on ratings it was not shown.

Every setting is trained with the same random users (and --users) left out, then those users' ratings are guessed as
in ml.evaluate. The table in <dir>/results.md grows after each setting; settings already trained (their .npz in <dir>)
are only evaluated again, so an interrupted sweep continues where it stopped.

    python3 -m ml.sweep --data ~/csfd-data
    python3 -m ml.sweep --data ~/csfd-data --configs "základ=;vektory 128=--factors 128 --reg 0.08"
"""

import argparse
import os
import re
import subprocess
import sys
import time
import unicodedata

import numpy as np

from .data import load_ratings
from .evaluate import evaluate_users, metrics, random_users

# name = extra arguments of ml.train
DEFAULT_CONFIGS = [
    ("základ", ""),
    ("vektory 32", "--factors 32"),
    ("vektory 128", "--factors 128"),
    ("regularizace 0.02", "--reg 0.02"),
    ("regularizace 0.1", "--reg 0.1"),
    ("učení 0.002", "--lr 0.002 --patience 4"),
    ("jen uživatelé a filmy s 20+", "--min-user-ratings 20 --min-film-ratings 20"),
]


def slug(name):
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-") or "config"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=os.path.expanduser("~/csfd-data"))
    parser.add_argument("--dir", default="sweep", help="where the models, logs and results.md go")
    parser.add_argument("--configs", help='settings as "name=train arguments;name=train arguments" '
                                          '(default: a few vector lengths, regularisations and learning rates)')
    parser.add_argument("--random", type=int, default=1000, help="random users left out of training and evaluated")
    parser.add_argument("--users", default="63015", help="users also left out and shown separately (comma separated)")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    configs = ([tuple(part.split("=", 1)) for part in args.configs.split(";") if part.strip()] if args.configs
               else DEFAULT_CONFIGS)
    os.makedirs(args.dir, exist_ok=True)
    ratings = load_ratings(args.data)
    rng = np.random.default_rng(args.seed)
    listed = [int(i) for i in args.users.split(",") if i.strip()]
    candidates = {"film_ids": ratings["film_id"].unique()}
    test_users = [u for u in random_users(ratings, candidates, args.random, rng) if u not in listed]
    users_path = os.path.join(args.dir, "test-users.txt")
    with open(users_path, "w") as f:
        f.write(",".join(map(str, test_users + listed)))
    print(f"{len(test_users)} random users and {listed} are left out of training and evaluated")

    rows = []
    for name, extra in configs:
        out = os.path.join(args.dir, slug(name) + ".npz")
        log_path = os.path.join(args.dir, slug(name) + ".log")
        if not os.path.exists(out):
            print(f"\n== {name}: training ({extra or 'default settings'}), log in {log_path}")
            started = time.time()
            command = [sys.executable, "-m", "ml.train", "--data", args.data, "--out", out,
                       "--exclude-users", ",".join(map(str, test_users + listed)), *extra.split()]
            with open(log_path, "w") as log:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in process.stdout:
                    print("   " + line, end="")
                    log.write(line)
                if process.wait():
                    print(f"   training failed, see {log_path}")
                    continue
            with open(log_path, "a") as log:
                log.write(f"Training took {time.time() - started:.0f} s\n")
        with open(log_path) as log:
            text = log.read()
        validation = re.search(r"Best validation RMSE ([0-9.]+)", text)
        epochs = re.findall(r"Epoch (\d+):", text)
        took = re.search(r"Training took (\d+) s", text)
        model = dict(np.load(out))
        evaluation_rng = np.random.default_rng(args.seed)
        results = evaluate_users(model, ratings, test_users + listed, args.folds, evaluation_rng, skipped=lambda _: None)
        random_part = [r for r in results if r[0] not in listed]
        everyone = metrics(np.concatenate([r[2] for r in random_part]),
                           np.concatenate([r[3]["model"] for r in random_part]))
        row = [name, extra or "výchozí", validation.group(1) if validation else "?", epochs[-1] if epochs else "?",
               f"{int(took.group(1)) // 60} min" if took else "?", *(f"{v:.3f}" for v in everyone[:3]),
               f"{everyone[3]:.2f}"]
        for user_id in listed:
            mine = [r for r in results if r[0] == user_id]
            row += [f"{v:.3f}" for v in metrics(mine[0][2], mine[0][3]["model"])[::2]] if mine else ["–", "–"]
        rows.append(row)
        header = (["nastavení", "argumenty", "validační RMSE", "epoch", "čas", f"RMSE {len(random_part)} uživ.",
                   "MAE", "pořadí", "top 10 %"] + [f"{u}: {m}" for u in listed for m in ("RMSE", "pořadí")])
        with open(os.path.join(args.dir, "results.md"), "w") as f:
            f.write("| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n")
            for r in rows:
                f.write("| " + " | ".join(r) + " |\n")
        print(f"== {name}: " + ", ".join(f"{h} {v}" for h, v in zip(header[2:], row[2:])))
    print(f"\nTable: {os.path.join(args.dir, 'results.md')} (lower RMSE is better, higher pořadí and top 10 % are better)")


if __name__ == "__main__":
    main()
