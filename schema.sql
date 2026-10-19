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
  kind VARCHAR(40) NOT NULL
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
  KEY rating_count (rating_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
