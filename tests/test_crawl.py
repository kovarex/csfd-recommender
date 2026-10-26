import configparser
import os
import tempfile
import unittest
import unittest.mock
from datetime import date
from decimal import Decimal

import pymysql

from csfd import db
from csfd.crawl import Crawler, read_fans, read_film_genres
from csfd.fetch import Blocked, ChallengeNotSolved, NotFound, ScrapeDoFetcher, film_url, profile_url
from csfd.parse import NotRatingsPage, Rating, RatingsPage, parse_fanclub, parse_film_genres, parse_ratings_page

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_config():
    config = configparser.ConfigParser()
    config.read_dict({
        "mysql": {
            "host": os.environ.get("CSFD_TEST_DB_HOST", "localhost"),
            "user": os.environ.get("CSFD_TEST_DB_USER", "csfd"),
            "password": os.environ.get("CSFD_TEST_DB_PASSWORD", "csfd"),
            "database": os.environ.get("CSFD_TEST_DB", "csfd_test"),
        },
        "crawl": {"workers": "2", "min_credits": "100", "max_missing_streak": "3"},
    })
    return config


def ratings_page(user_id, page, page_count, films):
    """A minimal ratings page in ČSFD's markup."""
    rows = "".join(
        f'<tr><td class="name"><h3 class="film-title-inline"><i class="icon icon-rounded-square red"></i>'
        f'<a href="/film/{film_id}-film-{film_id}/prehled/" class="film-title-name">Film {film_id}</a>'
        f'<span class="film-title-info"><span class="bullet"></span><span class="info">2000</span></span></h3></td>'
        f'<td class="star-rating-only"><span class="star-rating"><span class="stars {stars}"></span></span></td>'
        f'<td class="date-only date-only-with-tooltips">01.02.2020</td></tr>'
        for film_id, stars in films
    )
    pagination = "".join(f'<a href="/uzivatel/{user_id}-u/hodnoceni/?page={p}">{p}</a>' for p in range(2, page_count + 1))
    return (f'<link rel="canonical" href="https://www.csfd.cz/uzivatel/{user_id}-u{user_id}/hodnoceni/">'
            f'<title>User {user_id} | Hodnocení | ČSFD.cz</title>'
            f'<div id="snippet--ratings"><table><tbody>{rows}</tbody></table></div>'
            f'<div class="pagination">{pagination}</div>')


def profile_page(user_id, fan_ids):
    fans = "".join(f'<li><a href="/uzivatel/{fan}-fan{fan}/prehled/">fan{fan}</a></li>' for fan in fan_ids)
    return (f'<link rel="canonical" href="https://www.csfd.cz/uzivatel/{user_id}-u{user_id}/prehled/">'
            f'<a href="/uzivatel/{user_id}-u{user_id}/hodnoceni/">Hodnocení</a>'
            f'<section class="fanclub" id="snippet--fanclub"><h3>Fanklub ({len(fan_ids)})</h3><ul>{fans}</ul></section>'
            f'<section><a href="/uzivatel/99-other/prehled/">other</a></section>')


def film_page(film_id, genres):
    links = ' <span class="bullet"></span> '.join(f'<a href="/zanry/{i}-{slug}/">{slug}</a>' for i, slug in enumerate(genres))
    return (f'<link rel="canonical" href="https://www.csfd.cz/film/{film_id}-f/prehled/">'
            f'<div class="genres">{links}</div><div><a href="/zanry/99-horor/">horor</a></div>')


class FakeFetcher:
    def __init__(self, pages, credits=10000):
        self.pages = pages
        self.requests = 0
        self.credits_used = 0
        self.credits_remaining = credits
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        self.requests += 1
        self.credits_used += 1
        self.credits_remaining -= 1
        page = self.pages.get(url)
        if page is None:
            raise NotFound(url)
        if isinstance(page, Exception):
            raise page
        return page


def url(user_id, page=1):
    return f"https://www.csfd.cz/uzivatel/{user_id}/hodnoceni/" + (f"?page={page}" if page > 1 else "")


class ParseTest(unittest.TestCase):
    def test_parses_the_genres_of_a_film_page(self):
        self.assertEqual(parse_film_genres(film_page(1, ["dokumentarni", "sci-fi"])), ["dokumentarni", "scifi"])
        with self.assertRaises(NotRatingsPage):
            parse_film_genres(profile_page(1, []))

    def test_parses_the_fan_club_of_a_profile(self):
        self.assertEqual(parse_fanclub(profile_page(1, [5, 6, 5])), [5, 6])
        self.assertEqual(parse_fanclub('<link rel="canonical" href="https://www.csfd.cz/uzivatel/1-u/prehled/">'), [])
        with self.assertRaises(NotRatingsPage):
            parse_fanclub("<title>Making sure you&#39;re not a bot!</title>")

    def test_real_ratings_page(self):
        page = parse_ratings_page(fixture("user-ratings.html"))
        self.assertEqual(page.user_id, 912)
        self.assertEqual(page.user_slug, "bart")
        self.assertEqual(page.user_name, "BART!")
        self.assertEqual(page.page_count, 63)
        self.assertEqual(len(page.ratings), 50)
        first = page.ratings[0]
        self.assertEqual(first.film_id, 1811585)
        self.assertEqual(first.title, "Louis Theroux: V nitru manosféry")
        self.assertEqual(first.year, 2026)
        self.assertEqual(first.stars, 3)
        self.assertEqual(first.rated_on, date(2026, 9, 25))
        self.assertEqual(first.kind, "film")
        self.assertIn("seriál", [r.kind for r in page.ratings])
        self.assertEqual(sorted({r.stars for r in page.ratings}), [1, 2, 3, 4, 5])
        # A changed rating has a "Změněno" tooltip after its date.
        self.assertEqual(next(r for r in page.ratings if r.film_id == 1734167).rated_on, date(2026, 2, 23))
        self.assertNotIn(None, [r.rated_on for r in page.ratings])

    def test_trash_rating_is_zero(self):
        page = parse_ratings_page(ratings_page(5, 1, 1, [(10, "trash")]))
        self.assertEqual(page.ratings[0].stars, 0)

    def test_episode_keeps_parent_series(self):
        html = ratings_page(5, 1, 1, [(10, "stars-4")]).replace('/film/10-film-10/', '/film/7-serial/10-film-10/')
        rating = parse_ratings_page(html).ratings[0]
        self.assertEqual((rating.film_id, rating.parent_id), (10, 7))

    def test_other_page_is_rejected(self):
        with self.assertRaises(NotRatingsPage):
            parse_ratings_page(fixture("not-found.html"))


class FetchTest(unittest.TestCase):
    def fetcher(self, render):
        fetcher = ScrapeDoFetcher("token", rate=1000, render=render)
        fetcher.calls = []

        def request(url, wait_ms=0, render=None):
            rendering = fetcher.rendering if render is None else render
            fetcher.calls.append(rendering)
            if rendering and url not in fetcher.challenged_renders:
                return 200, "rendered page"
            if not rendering and url in fetcher.plain_ok:
                return 200, "plain page"
            if wait_ms:
                fetcher.challenged_renders.discard(url)  # Waiting longer solves the challenge on the next try.
            return 200, "<title>Making sure you&#39;re not a bot!</title>"

        fetcher.challenged_renders = set()
        fetcher.plain_ok = set()
        fetcher._request = request
        return fetcher

    def test_switches_to_rendering_on_a_bot_challenge(self):
        fetcher = self.fetcher("auto")
        self.assertEqual(fetcher.get("a"), "rendered page")
        self.assertEqual(fetcher.get("b"), "rendered page")
        self.assertEqual(fetcher.calls, [False, True, True])

    def test_retries_a_render_that_came_back_with_the_challenge(self):
        fetcher = self.fetcher("always")
        fetcher.challenged_renders.add("a")
        with unittest.mock.patch("time.sleep"):
            self.assertEqual(fetcher.get("a"), "rendered page")
        self.assertEqual(len(fetcher.calls), 3)

    def test_gives_up_on_a_page_after_ten_attempts_and_stops_after_twenty_failures(self):
        fetcher = self.fetcher("always")
        always_challenged = lambda url, wait_ms=0, render=None: (fetcher.calls.append(wait_ms),
                                                     (200, "<title>Making sure you&#39;re not a bot!</title>"))[1]
        rendered = fetcher._request
        with unittest.mock.patch("time.sleep"):
            fetcher._request = always_challenged
            with self.assertRaises(ChallengeNotSolved):
                fetcher.get("a")
            self.assertEqual(fetcher.calls, [1500 * i for i in range(10)])  # Each attempt waits longer.
            self.assertEqual(fetcher.challenges, 10)
            fetcher._request = rendered
            fetcher.get("b")  # A page that comes through takes five off.
            self.assertEqual(fetcher.challenges, 5)
            fetcher._request = always_challenged
            fetcher.calls = []
            with self.assertRaises(ChallengeNotSolved):
                fetcher.get("c")
            with self.assertRaises(Blocked):
                fetcher.get("d")
            self.assertEqual(len(fetcher.calls), 15)

    def test_switches_back_to_plain_requests_when_the_challenge_is_off(self):
        fetcher = self.fetcher("auto")
        fetcher.probe_seconds = 0
        self.assertEqual(fetcher.get("a"), "rendered page")  # Plain challenge, then rendered; probe was due later.
        fetcher.plain_ok.add("b")
        fetcher.next_probe = 0
        self.assertEqual(fetcher.get("b"), "plain page")
        self.assertFalse(fetcher.rendering)

    def test_never_render_stops_on_a_bot_challenge(self):
        with self.assertRaises(Blocked):
            self.fetcher("never").get("a")


class CrawlTest(unittest.TestCase):
    def setUp(self):
        state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(state_dir.cleanup)
        patcher = unittest.mock.patch.object(db, "DEFAULT_STATE_DIR", state_dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = test_config()
        self.connection = db.connect(self.config)
        with self.connection.cursor() as cursor:
            for table in ("ratings", "films", "users", "crawl_state", "crawl_runs", "film_stats", "film_genres", "series_stats", "fans", "csfd_genres", "film_pages"):
                cursor.execute(f"DROP TABLE IF EXISTS {table}")

    def tearDown(self):
        self.connection.close()

    def query(self, sql):
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    def crawl(self, fetcher, minutes=1):
        return Crawler(self.config, fetcher).run(minutes)

    def test_enumerates_users_and_stores_all_pages(self):
        fetcher = FakeFetcher({
            url(1): ratings_page(1, 1, 2, [(100, "stars-5"), (101, "stars-3")]),
            url(1, 2): ratings_page(1, 2, 2, [(102, "trash")]),
            url(3): ratings_page(3, 1, 1, [(100, "stars-1")]),
        })
        reason = self.crawl(fetcher)
        self.assertEqual(reason, "nothing left to scrape")
        self.assertEqual(self.query("SELECT user_id, film_id, stars FROM ratings ORDER BY user_id, film_id"),
                         ((1, 100, 5), (1, 101, 3), (1, 102, 0), (3, 100, 1)))
        self.assertEqual(self.query("SELECT id, status, rating_count, slug FROM users ORDER BY id")[:3],
                         ((1, db.DONE, 3, "u1"), (2, db.MISSING, 0, None), (3, db.DONE, 1, "u3")))
        self.assertEqual(self.query("SELECT COUNT(*) FROM films")[0][0], 3)
        self.assertEqual(self.query("SELECT requests, users_done, ratings_saved FROM crawl_runs"), ((len(fetcher.urls), 2, 4),))

    def test_enumeration_skips_users_scraped_already(self):
        db.create_schema(self.connection)
        db.add_users(self.connection, [2])
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE users SET status=%s WHERE id=2", (db.DONE,))  # Scraped first as a priority user.
        fetcher = FakeFetcher({url(1): ratings_page(1, 1, 1, [(100, "stars-5")]),
                               url(3): ratings_page(3, 1, 1, [(100, "stars-1")])})
        self.crawl(fetcher)
        self.assertIn(url(3), fetcher.urls)
        self.assertNotIn(url(2), fetcher.urls)

    def test_refresh_stops_at_the_first_page_without_new_ratings(self):
        pages = {url(1, p): ratings_page(1, p, 3, [(100 + p, "stars-4")]) for p in (1, 2, 3)}
        self.crawl(FakeFetcher(pages))
        pages[url(1)] = ratings_page(1, 1, 4, [(200, "stars-5")])  # A new rating pushes the rest a page further.
        pages[url(1, 2)] = ratings_page(1, 2, 4, [(101, "stars-4")])
        db.prioritize_users(self.connection, [1], rescrape_hours=-1)
        fetcher = FakeFetcher(pages)
        self.crawl(fetcher)
        self.assertEqual(fetcher.urls[:2], [url(1), url(1, 2)])
        self.assertNotIn(url(1, 3), fetcher.urls)
        self.assertEqual(self.query("SELECT status, rating_count FROM users WHERE id=1"), ((db.DONE, 4),))

    def test_prioritizes_fans_read_from_profiles(self):
        db.create_schema(self.connection)
        fetcher = FakeFetcher({profile_url(1): profile_page(1, [5, 6]), profile_url(2): NotFound(profile_url(2))})
        read_fans(self.connection, fetcher, [1, 2])
        self.assertEqual(db.get_fans(self.connection, [1, 2]), [5, 6])
        read_fans(self.connection, FakeFetcher({profile_url(1): profile_page(1, [6])}), [1])
        self.assertEqual(db.get_fans(self.connection, [1]), [6])

    def test_next_run_continues_where_the_last_one_stopped(self):
        pages = {url(1, p): ratings_page(1, p, 3, [(100 + p, "stars-4")]) for p in (1, 2, 3)}
        pages[url(1, 2)] = Blocked(url(1, 2))
        self.assertTrue(self.crawl(FakeFetcher(pages)).startswith("blocked"))
        self.assertEqual(self.query("SELECT status, pages_done FROM users WHERE id=1"), ((db.WAITING, 1),))

        pages[url(1, 2)] = ratings_page(1, 2, 3, [(102, "stars-4")])
        fetcher = FakeFetcher(pages)
        self.crawl(fetcher)
        self.assertEqual(fetcher.urls[0], url(1, 2))
        self.assertEqual(self.query("SELECT status, rating_count FROM users WHERE id=1"), ((db.DONE, 3),))

    def test_stops_when_credits_run_low(self):
        pages = {url(i): ratings_page(i, 1, 1, [(100, "stars-4")]) for i in range(1, 50)}
        fetcher = FakeFetcher(pages, credits=105)
        self.assertEqual(self.crawl(fetcher), "credits below 100")
        self.assertLess(fetcher.requests, 10)

    def test_reconnects_after_the_server_drops_the_connection(self):
        db.create_schema(self.connection)
        victim = db.connect(self.config)
        with self.connection.cursor() as cursor:
            cursor.execute("KILL %s", (victim.thread_id(),))
        db.set_state(victim, "x", 1)
        self.assertEqual(db.get_state(victim, "x"), "1")
        victim.close()

    def test_retries_after_a_deadlock(self):
        attempts = []

        @db.reconnecting
        def write(connection):
            attempts.append(1)
            if len(attempts) == 1:
                raise pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
            return "saved"

        self.assertEqual(write(self.connection), "saved")
        self.assertEqual(len(attempts), 2)

    def test_stores_a_film_with_a_very_long_slug(self):
        db.create_schema(self.connection)
        db.add_users(self.connection, [1])
        rating = Rating(10, None, "x" * 600, "Long", 2000, "film", 4, date(2020, 1, 1))
        db.save_page(self.connection, 1, RatingsPage(1, "u", "U", 1, [rating]), 1)
        self.assertEqual(self.query("SELECT LENGTH(slug) FROM films WHERE id=10"), ((600,),))

    def test_export_writes_every_rating(self):
        import csv, gzip, tempfile
        from csfd import export
        db.create_schema(self.connection)
        pages = {url(1): ratings_page(1, 1, 1, [(100, "stars-5")]), url(450): ratings_page(450, 1, 1, [(101, "trash")])}
        db.requeue_user(self.connection, 1)
        db.requeue_user(self.connection, 450)
        self.crawl(FakeFetcher(pages))
        with tempfile.TemporaryDirectory() as directory:
            export.export(self.connection, directory)
            with gzip.open(os.path.join(directory, "ratings.csv.gz"), "rt") as f:
                self.assertEqual(sorted(csv.reader(f))[:-1], [["1", "100", "5"], ["450", "101", "0"]])

    def test_save_genres_replaces_the_table(self):
        db.create_schema(self.connection)
        db.save_genres(self.connection, {1: {"scifi", "drama"}, 2: {"komedie"}})
        db.save_genres(self.connection, {1: {"scifi"}, 3: set()})
        self.assertEqual(self.query("SELECT film_id, genre FROM film_genres"), ((1, "scifi"),))

    def test_reads_genres_of_the_best_rated_films_from_their_pages(self):
        db.create_schema(self.connection)
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO film_stats (film_id, rating_count, average) VALUES "
                           "(1, 500, 4.9), (2, 500, 4.0), (3, 50, 5.0), (4, 500, 4.5)")
        self.assertEqual(db.films_without_genres(self.connection, 100, 10), [1, 4, 2])
        fetcher = FakeFetcher({film_url(1): film_page(1, ["dokumentarni"]), film_url(2): NotFound(film_url(2)),
                               film_url(4): Blocked(film_url(4))})
        with self.assertRaises(Blocked):
            read_film_genres(self.connection, fetcher, [1, 2, 4], 2)
        read_film_genres(self.connection, fetcher, [1, 2], 2)
        self.assertEqual(db.films_without_genres(self.connection, 100, 10), [4])
        # The weekly Wikidata download keeps ČSFD's genres and adds its own only where ČSFD wasn't read, or anthologies.
        db.save_genres(self.connection, {1: {"drama", "antologie"}, 2: {"drama"}, 4: {"drama"}})
        self.assertEqual(self.query("SELECT film_id, genre FROM film_genres ORDER BY film_id, genre"),
                         ((1, "antologie"), (1, "dokumentarni"), (4, "drama")))
        db.save_film_genres(self.connection, [(4, ["komedie"])])
        self.assertEqual(self.query("SELECT genre FROM film_genres WHERE film_id=4"), (("komedie",),))

    def test_film_stats_copy_the_kind_and_cache_the_kinds(self):
        db.create_schema(self.connection)
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO films (id, slug, title, kind) VALUES (1, 'a', 'A', 'film'), (2, 'b', 'B', 'série')")
            cursor.execute("INSERT INTO ratings (user_id, film_id, stars) VALUES (5, 1, 4), (6, 1, 2), (5, 2, 3)")
        with unittest.mock.patch.object(db, "FILM_STATS_USER_STEP", 1):  # each user in their own chunk
            db.refresh_film_stats(self.connection)
        self.assertEqual(self.query("SELECT film_id, rating_count, average, kind FROM film_stats ORDER BY film_id"),
                         ((1, 2, Decimal("3.000"), "film"), (2, 1, Decimal("3.000"), "série")))
        self.assertEqual(self.query("SELECT value FROM crawl_state WHERE name='film_kinds'"), (('["film", "série"]',),))

    def test_film_stats_get_titles_searchable_also_when_the_table_had_none(self):
        db.create_schema(self.connection)
        with self.connection.cursor() as cursor:
            cursor.execute("ALTER TABLE film_stats DROP KEY title, DROP COLUMN title")  # As before 26 Oct 2026.
            cursor.execute("INSERT INTO films (id, slug, title, kind) VALUES (1, 'a', 'Válka ve Vietnamu', 'seriál'), "
                           "(2, 'b', 'Kmotr', 'film')")
            cursor.execute("INSERT INTO ratings (user_id, film_id, stars) VALUES (5, 1, 5), (5, 2, 4)")
        db.refresh_film_stats(self.connection)
        self.assertEqual(self.query("SELECT film_id FROM film_stats WHERE MATCH(title) AGAINST('+valka* +viet*' "
                                    "IN BOOLEAN MODE)"), ((1,),))

    def test_film_stats_continue_in_the_next_run_when_out_of_time(self):
        db.create_schema(self.connection)
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO films (id, slug, title, kind) VALUES (1, 'a', 'A', 'film'), (2, 'b', 'B', 'film')")
            cursor.execute("INSERT INTO ratings (user_id, film_id, stars) VALUES (1, 1, 4), (2, 1, 2), (3, 2, 5)")
        clock = [1000.0]
        with unittest.mock.patch.object(db, "FILM_STATS_USER_STEP", 1), \
                unittest.mock.patch("time.time", lambda: clock[0]):
            reads = []
            real_run = db.run_statement.__wrapped__

            def run_statement(connection, sql, params=None):
                if sql.startswith("SELECT film_id, stars FROM ratings"):
                    reads.append(params[0])
                    clock[0] += 40  # Each range takes 40 s.
                return real_run(connection, sql, params)

            with unittest.mock.patch.object(db, "run_statement", run_statement):
                self.assertFalse(db.refresh_film_stats(self.connection, seconds=70))
                self.assertEqual(reads, [1, 2])
                # Ratings added meanwhile to a user read already don't count; the rest continues where it stopped.
                with self.connection.cursor() as cursor:
                    cursor.execute("INSERT INTO ratings (user_id, film_id, stars) VALUES (1, 2, 1)")
                with unittest.mock.patch.object(db, "FILM_STATS_WRITE_SECONDS", 0):
                    self.assertTrue(db.refresh_film_stats(self.connection, seconds=100))
                self.assertEqual(reads, [1, 2, 3])
        self.assertEqual(self.query("SELECT film_id, rating_count, average FROM film_stats ORDER BY film_id"),
                         ((1, 2, Decimal("3.000")), (2, 1, Decimal("5.000"))))
        self.assertIsNone(db.load_scan(db.scan_path(None, "film-stats")))

    def test_migration_adds_the_kind_to_old_film_stats(self):
        db.create_schema(self.connection)
        with self.connection.cursor() as cursor:
            cursor.execute("ALTER TABLE film_stats DROP KEY kind_count, DROP KEY kind_average, DROP KEY average, "
                           "DROP KEY title, DROP COLUMN kind, DROP COLUMN title")
            cursor.execute("INSERT INTO films (id, slug, title, kind) VALUES (1, 'a', 'A', 'film')")
            cursor.execute("INSERT INTO film_stats (film_id, rating_count, average) VALUES (1, 3, 4.0)")
        db.create_schema(self.connection)
        self.assertEqual(self.query("SELECT kind FROM film_stats"), (("film",),))

    def test_next_run_marks_a_killed_run(self):
        db.create_schema(self.connection)
        db.start_run(self.connection)
        self.crawl(FakeFetcher({}))
        self.assertEqual(self.query("SELECT stop_reason FROM crawl_runs ORDER BY id")[0],
                         ("killed before it could finish",))

    def test_prioritize_skips_scraped_users(self):
        db.create_schema(self.connection)
        db.add_users(self.connection, [1, 2])
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE users SET status=%s WHERE id=2", (db.DONE,))
        db.prioritize_users(self.connection, [1, 2, 7])
        db.prioritize_users(self.connection, [1, 2, 7])
        self.assertEqual(self.query("SELECT id, status, priority FROM users ORDER BY id"),
                         ((1, db.WAITING, 1), (2, db.DONE, 0), (7, db.WAITING, 1)))

    def test_prioritize_rescrapes_users_scraped_long_ago(self):
        db.create_schema(self.connection)
        db.add_users(self.connection, [1, 2])
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE users SET status=%s, pages_done=3, scraped_at=NOW() - INTERVAL 2 DAY WHERE id=1",
                           (db.DONE,))
            cursor.execute("UPDATE users SET status=%s, scraped_at=NOW() WHERE id=2", (db.DONE,))
        db.prioritize_users(self.connection, [1, 2], rescrape_hours=24)
        self.assertEqual(self.query("SELECT id, status, priority, pages_done FROM users ORDER BY id"),
                         ((1, db.WAITING, db.REFRESH, 0), (2, db.DONE, 0, 0)))
        db.prioritize_users(self.connection, [1, 2])  # The next run keeps it a refresh.
        self.assertEqual(self.query("SELECT priority FROM users WHERE id=1"), ((db.REFRESH,),))

    def test_series_stats_measure_how_differently_a_viewer_rates_episodes(self):
        db.create_schema(self.connection)
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO films (id, parent_id, slug, title, kind) VALUES (1, NULL, 's', 'S', 'seriál'), "
                           "(2, 1, 'a', 'A', 'série'), (3, 2, 'e', 'E1', 'epizoda'), (4, 2, 'e', 'E2', 'epizoda'), "
                           "(5, 1, 'e', 'E3', 'epizoda'), (6, 1, 'e', 'E4', 'epizoda')")
            # User 7 rates three episodes 1, 3, 5 (standard deviation 1.633); user 8 only two, so doesn't count.
            cursor.execute("INSERT INTO ratings (user_id, film_id, stars) VALUES (7, 3, 1), (7, 4, 3), (7, 5, 5), "
                           "(8, 3, 4), (8, 6, 4)")
        with unittest.mock.patch.object(db, "FILM_STATS_USER_STEP", 1):
            db.refresh_series_stats(self.connection)
        self.assertEqual(self.query("SELECT series_id, users, episodes, spread FROM series_stats"),
                         ((1, 1, 4, Decimal("1.633")),))

    def test_hand_added_user_goes_first_and_can_be_rescraped(self):
        pages = {url(500): ratings_page(500, 1, 1, [(100, "stars-2")])}
        db.create_schema(self.connection)
        db.add_users(self.connection, [1, 2])
        db.requeue_user(self.connection, 500)
        fetcher = FakeFetcher(pages)
        self.crawl(fetcher)
        self.assertEqual(fetcher.urls[0], url(500))

        pages[url(500)] = ratings_page(500, 1, 1, [(100, "stars-5")])
        db.requeue_user(self.connection, 500)
        self.crawl(FakeFetcher(pages))
        self.assertEqual(self.query("SELECT stars FROM ratings WHERE user_id=500"), ((5,),))


if __name__ == "__main__":
    unittest.main()
