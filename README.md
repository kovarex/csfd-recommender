# ČSFD recommender

Movie recommendations from [ČSFD](https://www.csfd.cz) ratings: give it your ratings and it suggests films
that people who rate like you liked.

Status: step 1, the scraper. It downloads users' public rating pages through [scrape.do](https://scrape.do)
into MariaDB. The model (matrix factorisation trained on a desktop PC) and a web page for recommendations come next.

## How the scraper works

- Every user's ratings live at `https://www.csfd.cz/uzivatel/<id>/hodnoceni/`, 50 per page.
- `python3 -m csfd.crawl run` goes through users added by hand first, then every user ID from 1 upwards.
  Missing IDs (404) are recorded and skipped; enumeration stops after `max_missing_streak` missing IDs in a row.
- Progress is saved after every page, so each run continues exactly where the last one stopped.
  A run stops at its time limit, when scrape.do credits drop below `min_credits`, or when ČSFD serves an
  anti-bot challenge (so no credits are wasted).
- Only one run at a time (MySQL `GET_LOCK`), so an hourly cron job can't overlap itself.
- Every run is logged in the `crawl_runs` table (requests, credits used and left, users, ratings).

Measured 18 Oct 2026: 1 scrape.do credit per page, no challenge, ~2 pages/s with `rate = 2`
(about 100 ratings per second, 50 per credit).

## Web pages

`web/` is a small PHP browser of the database: overview (counts, crawl runs, credits), users by number of ratings,
a user's ratings, films by number of ratings or average, a film's ratings, search. `deploy-web.sh` (run by
`run-crawl.sh` every hour) copies it to `/home/public` and writes the database login for PHP to
`/home/protected/csfd-web.ini` (readable by the web server, without the scrape.do token).
The pages are public; set `[web] password` in `~/csfd-config.ini` to put them behind a password.

Film counts and averages come from the `film_stats` table, rebuilt every 6 hours (`python3 -m csfd.crawl film-stats`).

## Database

`schema.sql` (created automatically): `users`, `films` (incl. series, seasons, episodes with `parent_id`),
`ratings` (`stars` 0 = odpad, 1–5), `crawl_state`, `crawl_runs`.

## Setup on NearlyFreeSpeech

1. Create a MariaDB database `csfd` and a user for it (a new MySQL process, or a new database on an existing one).
2. Over SSH, once:
   ```
   cd /home/private && git clone https://github.com/kovarex/csfd-recommender.git && sh csfd-recommender/setup-nfs.sh
   ```
   It installs PyMySQL, asks for the scrape.do token and the MySQL login (saved to `~/csfd-config.ini`, readable only by you),
   creates the tables and does a 1-minute test run.
3. In the NFS member UI, site → **Scheduled Tasks** → **Add a Scheduled Task**: command
   `/home/private/csfd-recommender/run-crawl.sh`, every hour.

Deploying is pull-based: every hourly run first does `git pull`, so whatever is pushed to `master` is live within an hour.
The log goes to `~/logs/csfd-crawl.log`.

## Tests

Need a MariaDB database `csfd_test` (user `csfd`, password `csfd` by default, see `tests/test_crawl.py`):

```
pip install -r requirements.txt
python3 -m unittest -v
```
