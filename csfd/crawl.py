"""Scrapes ČSFD user ratings into MariaDB. Meant to run from cron; every run continues where the last one stopped.

Users are processed in this order: users added by hand or interrupted earlier, then every user ID from 1 upwards.
"""

import argparse
import configparser
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from .fetch import Blocked, FetchError, NotFound, ScrapeDoFetcher, ratings_url
from .parse import NotRatingsPage, parse_ratings_page

DEFAULT_CONFIG = os.path.expanduser("~/csfd-config.ini")
log = logging.getLogger("csfd")


class StopCrawl(Exception):
    pass


class Crawler:
    def __init__(self, config, fetcher, connect=None):
        self.config = config
        self.fetcher = fetcher
        self.connect = connect or (lambda: db.connect(config))
        crawl = config["crawl"] if config.has_section("crawl") else {}
        self.workers = int(crawl.get("workers", 3))
        self.min_credits = int(crawl.get("min_credits", 1000))
        # Enumeration stops after this many user IDs in a row that don't exist.
        self.max_missing_streak = int(crawl.get("max_missing_streak", 20000))
        self.claim_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.stats = {"users": 0, "ratings": 0}
        self.stop_reason = None
        self.deadline = None

    def run(self, minutes):
        self.deadline = time.monotonic() + minutes * 60
        main = self.connect()
        db.create_schema(main)
        with main.cursor() as cursor:
            # Only one crawl at a time, in case cron starts a run while the previous one is still going.
            cursor.execute("SELECT GET_LOCK('csfd_crawl', 0)")
            if not cursor.fetchone()[0]:
                log.info("Another crawl is running, exiting.")
                main.close()
                return None
        db.reset_interrupted(main)
        run_id = db.start_run(main)
        self.main = main
        try:
            with ThreadPoolExecutor(self.workers) as pool:
                for future in [pool.submit(self.worker) for _ in range(self.workers)]:
                    future.result()
        finally:
            reason = self.stop_reason or "time limit"
            db.finish_run(main, run_id, self.stats, self.fetcher, reason)
            log.info("Stopped (%s): %s users, %s ratings, %s requests, %s credits used, %s left", reason,
                     self.stats["users"], self.stats["ratings"], self.fetcher.requests,
                     self.fetcher.credits_used, self.fetcher.credits_remaining)
            main.close()
        return reason

    def should_stop(self):
        if self.stop_reason:
            return True
        if time.monotonic() > self.deadline:
            return True
        remaining = self.fetcher.credits_remaining
        if remaining is not None and remaining < self.min_credits:
            self.stop_reason = f"credits below {self.min_credits}"
            return True
        return False

    def claim(self):
        """Next (user_id, pages_done) to scrape, or None when there is nothing left."""
        with self.claim_lock:
            row = db.claim_waiting_user(self.main)
            if row:
                return row
            next_id = int(db.get_state(self.main, "next_user_id", 1))
            missing_streak = int(db.get_state(self.main, "missing_streak", 0))
            if missing_streak >= self.max_missing_streak:
                return None
            db.set_state(self.main, "next_user_id", next_id + 1)
            db.add_users(self.main, [next_id])
            row = db.claim_waiting_user(self.main)
            return row

    def worker(self):
        connection = self.connect()
        try:
            while not self.should_stop():
                claimed = self.claim()
                if not claimed:
                    self.stop_reason = self.stop_reason or "nothing left to scrape"
                    return
                self.scrape_user(connection, *claimed)
        except Exception as e:
            self.stop_reason = self.stop_reason or f"error: {e}"
            log.exception("Worker failed")
        finally:
            connection.close()

    def scrape_user(self, connection, user_id, pages_done):
        page_number = pages_done + 1
        try:
            while True:
                if self.should_stop():
                    db.release_user(connection, user_id)
                    return
                page = parse_ratings_page(self.fetcher.get(ratings_url(user_id, page_number)))
                db.save_page(connection, user_id, page, page_number)
                with self.stats_lock:
                    self.stats["ratings"] += len(page.ratings)
                if page_number >= page.page_count or not page.ratings:
                    break
                page_number += 1
        except NotFound:
            db.finish_user(connection, user_id, db.MISSING)
            self.count_missing(+1)
            return
        except Blocked as e:
            db.release_user(connection, user_id)
            self.stop_reason = f"blocked by ČSFD at {e}"
            return
        except (FetchError, NotRatingsPage) as e:
            log.warning("User %s failed: %s", user_id, e)
            db.finish_user(connection, user_id, db.FAILED, str(e))
            return
        except BaseException:
            db.release_user(connection, user_id)
            raise
        db.finish_user(connection, user_id, db.DONE)
        self.count_missing(None)
        with self.stats_lock:
            self.stats["users"] += 1

    def count_missing(self, delta):
        with self.claim_lock:
            streak = int(db.get_state(self.main, "missing_streak", 0))
            db.set_state(self.main, "missing_streak", streak + 1 if delta else 0)


def load_config(path):
    config = configparser.ConfigParser()
    if not config.read(path, encoding="utf-8"):
        raise SystemExit(f"Config file {path} not found (see config.example.ini).")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("CSFD_CONFIG", DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="scrape until the time limit, credit limit or the end")
    run.add_argument("--minutes", type=float, default=50)
    add = sub.add_parser("add", help="queue users (numeric IDs) to be scraped first, e.g. yourself")
    add.add_argument("user_ids", type=int, nargs="+")
    sub.add_parser("stats", help="show what is in the database")
    film_stats = sub.add_parser("film-stats", help="rebuild per-film rating counts and averages for the web pages")
    film_stats.add_argument("--max-age-hours", type=float, default=0,
                            help="only rebuild when the last rebuild is older than this")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)

    if args.command == "run":
        fetcher = ScrapeDoFetcher(config["scrape_do"]["token"], rate=float(config["crawl"].get("rate", 2)))
        Crawler(config, fetcher).run(args.minutes)
    elif args.command == "add":
        connection = db.connect(config)
        db.create_schema(connection)
        for user_id in args.user_ids:
            db.requeue_user(connection, user_id)
        print(f"Queued {len(args.user_ids)} user(s).")
    elif args.command == "film-stats":
        connection = db.connect(config)
        db.create_schema(connection)
        last = int(db.get_state(connection, "film_stats_at", 0))
        if time.time() - last >= args.max_age_hours * 3600:
            db.refresh_film_stats(connection)
            print("Film stats rebuilt.")
    elif args.command == "stats":
        connection = db.connect(config)
        db.create_schema(connection)
        users, films, ratings, runs = db.stats(connection)
        names = {db.WAITING: "waiting", db.IN_PROGRESS: "in progress", db.DONE: "done", db.MISSING: "missing",
                 db.FAILED: "failed"}
        for status, (count, rating_count) in sorted(users.items()):
            print(f"users {names[status]}: {count}")
        print(f"films: {films}\nratings: {ratings}\nnext user id: {db.get_state(connection, 'next_user_id', 1)}")
        for run in runs:
            print("run", *run)


if __name__ == "__main__":
    main()
