<?php
// Shared helpers for the database browser.

const STATUS_NAMES = [0 => 'čeká', 1 => 'stahuje se', 2 => 'hotovo', 3 => 'neexistuje', 4 => 'chyba'];
const PAGE_SIZE = 100;

function config(): array
{
  static $config = null;
  if ($config === null)
  {
    $path = getenv('CSFD_WEB_CONFIG') ?: '/home/protected/csfd-web.ini';
    $config = @parse_ini_file($path, true, INI_SCANNER_RAW);
    if ($config === false)
      fail("Config $path not found. It is created by deploy-web.sh.");
  }
  return $config;
}

function db(): mysqli
{
  static $db = null;
  if ($db === null)
  {
    $c = config()['mysql'];
    mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
    $db = new mysqli($c['host'] ?? 'localhost', $c['user'], $c['password'], $c['database'], (int)($c['port'] ?? 3306));
    $db->set_charset('utf8mb4');
  }
  return $db;
}

function query(string $sql, array $params = []): array
{
  $statement = db()->prepare($sql);
  if ($params)
    $statement->bind_param(str_repeat('s', count($params)), ...array_map('strval', $params));
  $statement->execute();
  $result = $statement->get_result();
  return $result ? $result->fetch_all(MYSQLI_ASSOC) : [];
}

function value(string $sql, array $params = [])
{
  $rows = query($sql, $params);
  return $rows ? reset($rows[0]) : null;
}

function require_password(): void
{
  // Optional: with no password configured the pages are public (the user's choice, 18 Oct 2026).
  $password = config()['web']['password'] ?? '';
  if ($password === '' || hash_equals($password, $_SERVER['PHP_AUTH_PW'] ?? ''))
    return;
  header('WWW-Authenticate: Basic realm="ČSFD recommender"');
  http_response_code(401);
  exit('Přihlášení je potřeba.');
}

function fail(string $message): void
{
  http_response_code(500);
  exit(htmlspecialchars($message));
}

function h($text): string
{
  return htmlspecialchars((string)$text, ENT_QUOTES);
}

function n($number): string
{
  return number_format((float)$number, 0, ',', "\u{00a0}");
}

function stars(int $stars): string
{
  return $stars === 0 ? 'odpad' : str_repeat('★', $stars) . str_repeat('☆', 5 - $stars);
}

function url(array $params): string
{
  return '?' . http_build_query(array_filter($params, fn($v) => $v !== null && $v !== ''));
}

function page_number(): int
{
  return max(1, (int)($_GET['p'] ?? 1));
}

function pager(int $total): string
{
  $pages = max(1, (int)ceil($total / PAGE_SIZE));
  $current = min(page_number(), $pages);
  if ($pages == 1)
    return '';
  $links = [];
  foreach ([1, $current - 1, $current + 1, $pages] as $p)
    if ($p >= 1 && $p <= $pages && $p != $current)
      $links[$p] = '<a href="' . h(url(['p' => $p] + $_GET)) . '">' . $p . '</a>';
  ksort($links);
  return '<p class="pager">Stránka ' . $current . ' z ' . n($pages) . ' &nbsp; ' . implode(' ', $links) . '</p>';
}

function csfd_film_url(int $id, string $slug, $parentId): string
{
  return 'https://www.csfd.cz/film/' . ($parentId ? (int)$parentId . '/' : '') . $id . '-' . $slug . '/prehled/';
}

function film_link(array $row): string
{
  $title = h($row['title']) . ($row['year'] ? ' (' . (int)$row['year'] . ')' : '');
  return '<a href="' . h(url(['view' => 'film', 'id' => $row['film_id']])) . '">' . $title . '</a>' .
         ' <small>(<a href="' . h(csfd_film_url((int)$row['film_id'], $row['slug'], $row['parent_id'])) . '">csfd</a>)</small>';
}

function user_link(array $row): string
{
  return '<a href="' . h(url(['view' => 'user', 'id' => $row['user_id']])) . '">' . h($row['name'] ?? '#' . $row['user_id']) . '</a>';
}

function header_html(string $title): void
{
  ?><!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title><?= h($title) ?> · ČSFD recommender</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<nav>
  <a href="?">Přehled</a>
  <a href="?view=users">Uživatelé</a>
  <a href="?view=films">Filmy</a>
  <form><input type="hidden" name="view" value="search"><input name="q" placeholder="Hledat film nebo uživatele" value="<?= h($_GET['q'] ?? '') ?>"></form>
</nav>
<main>
<h1><?= h($title) ?></h1>
<?php
}

function footer_html(): void
{
  echo "</main>\n</body>\n</html>\n";
}
