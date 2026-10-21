-- ČSFD ratings database (MariaDB / MySQL).

CREATE TABLE IF NOT EXISTS users (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  slug VARCHAR(100) NULL,
  name VARCHAR(100) NULL,
  -- 0 = waiting, 1 = in progress, 2 = done, 3 = does not exist, 4 = failed
  status TINYINT NOT NULL DEFAULT 0,
  -- Users added by hand are scraped before the rest.
  priority TINYINT NOT NULL DEFAULT 0,
  page_count INT UNSIGNED NULL,
  pages_done INT UNSIGNED NOT NULL DEFAULT 0,
  rating_count INT UNSIGNED NOT NULL DEFAULT 0,
  scraped_at DATETIME NULL,
  error VARCHAR(255) NULL,
  KEY queue (status, priority, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS films (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  -- For an episode or a season, the series it belongs to.
  parent_id INT UNSIGNED NULL,
  slug VARCHAR(1000) NOT NULL,
  title VARCHAR(300) NOT NULL,
  year SMALLINT UNSIGNED NULL,
  kind VARCHAR(40) NOT NULL,
  KEY parent_id (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ratings (
  user_id INT UNSIGNED NOT NULL,
  film_id INT UNSIGNED NOT NULL,
  -- 0 = "odpad" (trash), 1-5 stars
  stars TINYINT UNSIGNED NOT NULL,
  rated_on DATE NULL,
  PRIMARY KEY (user_id, film_id),
  KEY film_id (film_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- How differently the same viewer rates a series' episodes, rebuilt by `python3 -m csfd.crawl series-stats`: in a
-- series with one story people rate the episodes alike, in an anthology every episode is judged on its own.
CREATE TABLE IF NOT EXISTS series_stats (
  series_id INT UNSIGNED NOT NULL PRIMARY KEY,
  -- Viewers who rated at least 3 of its episodes, and the episodes rated by anyone.
  users INT UNSIGNED NOT NULL,
  episodes INT UNSIGNED NOT NULL,
  -- Average over those viewers of the standard deviation of their episode ratings, in stars.
  spread DECIMAL(5,3) NOT NULL,
  KEY users (users)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Broad genres from Wikidata (csfd/genres.py), also for films we have no ratings of yet.
CREATE TABLE IF NOT EXISTS film_genres (
  film_id INT UNSIGNED NOT NULL,
  genre VARCHAR(20) NOT NULL,
  PRIMARY KEY (film_id, genre),
  KEY genre (genre, film_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Genres from the films' own ČSFD pages (Wikidata has none for many films), kept apart so the weekly Wikidata
-- download, which replaces film_genres, can add them back. Genre names are ČSFD's URL slugs without dashes.
CREATE TABLE IF NOT EXISTS csfd_genres (
  film_id INT UNSIGNED NOT NULL,
  genre VARCHAR(20) NOT NULL,
  PRIMARY KEY (film_id, genre)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Films whose ČSFD page was read for csfd_genres.
CREATE TABLE IF NOT EXISTS film_pages (
  film_id INT UNSIGNED NOT NULL PRIMARY KEY,
  fetched_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- The fan clubs of the priority users (priority-users.txt), scraped first too: users who added them to favourites.
CREATE TABLE IF NOT EXISTS fans (
  user_id INT UNSIGNED NOT NULL,
  fan_id INT UNSIGNED NOT NULL,
  PRIMARY KEY (user_id, fan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crawl_state (
  name VARCHAR(50) NOT NULL PRIMARY KEY,
  value VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crawl_runs (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  requests INT UNSIGNED NOT NULL DEFAULT 0,
  credits_used INT UNSIGNED NOT NULL DEFAULT 0,
  credits_remaining INT UNSIGNED NULL,
  users_done INT UNSIGNED NOT NULL DEFAULT 0,
  ratings_saved INT UNSIGNED NOT NULL DEFAULT 0,
  stop_reason VARCHAR(255) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-film aggregates for browsing, rebuilt by `python3 -m csfd.crawl film-stats`.
CREATE TABLE IF NOT EXISTS film_stats (
  film_id INT UNSIGNED NOT NULL PRIMARY KEY,
  rating_count INT UNSIGNED NOT NULL,
  -- Average of stars 0-5.
  average DECIMAL(4,3) NOT NULL,
  -- Copied from films, so the film list can filter and sort by an index without joining films.
  kind VARCHAR(40) NOT NULL DEFAULT '',
  KEY rating_count (rating_count),
  KEY average (average),
  KEY kind_count (kind, rating_count),
  KEY kind_average (kind, average)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
