<?php
// Browser for the scraped ČSFD ratings database.
require __DIR__ . '/lib.php';

require_password();
$view = $_GET['view'] ?? 'overview';
$id = (int)($_GET['id'] ?? 0);
$offset = (page_number() - 1) * PAGE_SIZE;

switch ($view)
{
  case 'users': users_page($offset); break;
  case 'user': user_page($id, $offset); break;
  case 'films': films_page($offset); break;
  case 'film': film_page($id, $offset); break;
  case 'search': search_page(trim($_GET['q'] ?? '')); break;
  default: overview_page();
}
footer_html();

function overview_page(): void
{
  header_html('Přehled');
  $statuses = query('SELECT status, COUNT(*) AS users, SUM(rating_count) AS ratings FROM users GROUP BY status');
  $ratings = array_sum(array_column($statuses, 'ratings'));
  $existing = 0;
  foreach ($statuses as $s)
    if ($s['status'] != 3)
      $existing += $s['users'];
  $next = (int)(value("SELECT value FROM crawl_state WHERE name='next_user_id'") ?? 1);
  $films = value('SELECT COUNT(*) FROM films');
  $credits = value('SELECT credits_remaining FROM crawl_runs WHERE credits_remaining IS NOT NULL ORDER BY id DESC LIMIT 1');
  $statsAt = value("SELECT value FROM crawl_state WHERE name='film_stats_at'");
  ?>
  <div class="tiles">
    <div><b><?= n($ratings) ?></b>hodnocení</div>
    <div><b><?= n($existing) ?></b>uživatelů</div>
    <div><b><?= n($films) ?></b>filmů a seriálů</div>
    <div><b><?= n($next - 1) ?></b>prošlých ID (z asi 445 000)</div>
    <div><b><?= $credits === null ? '?' : n($credits) ?></b>kreditů scrape.do</div>
  </div>
  <h2>Uživatelé podle stavu</h2>
  <table>
    <tr><th>Stav</th><th class="num">Uživatelů</th><th class="num">Hodnocení</th></tr>
    <?php foreach ($statuses as $s): ?>
      <tr><td><?= h(STATUS_NAMES[$s['status']] ?? $s['status']) ?></td><td class="num"><?= n($s['users']) ?></td><td class="num"><?= n($s['ratings']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <h2>Poslední běhy</h2>
  <table>
    <tr><th>Začátek</th><th>Konec</th><th class="num">Požadavků</th><th class="num">Uživatelů</th><th class="num">Hodnocení</th><th class="num">Kredity zbývají</th><th>Proč skončil</th></tr>
    <?php foreach (query('SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 15') as $r): ?>
      <tr><td><?= h($r['started_at']) ?></td><td><?= h($r['finished_at'] ?? 'běží') ?></td><td class="num"><?= n($r['requests']) ?></td>
        <td class="num"><?= n($r['users_done']) ?></td><td class="num"><?= n($r['ratings_saved']) ?></td>
        <td class="num"><?= $r['credits_remaining'] === null ? '' : n($r['credits_remaining']) ?></td><td><?= h($r['stop_reason']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <p class="note">Statistiky filmů přepočítány: <?= $statsAt ? date('j. n. Y H:i', (int)$statsAt) : 'zatím ne' ?> (každých 6 hodin).</p>
  <?php
}

function users_page(int $offset): void
{
  header_html('Uživatelé');
  $total = (int)value('SELECT COUNT(*) FROM users WHERE status=2');
  echo pager($total);
  $rows = query('SELECT id AS user_id, name, slug, rating_count, scraped_at FROM users WHERE status=2
                 ORDER BY rating_count DESC, id LIMIT ' . PAGE_SIZE . ' OFFSET ' . $offset);
  ?>
  <table>
    <tr><th class="num">#</th><th>Uživatel</th><th class="num">ID</th><th class="num">Hodnocení</th><th>Staženo</th></tr>
    <?php foreach ($rows as $i => $r): ?>
      <tr><td class="num"><?= $offset + $i + 1 ?></td><td><?= user_link($r) ?></td><td class="num"><?= (int)$r['user_id'] ?></td>
        <td class="num"><?= n($r['rating_count']) ?></td><td><?= h($r['scraped_at']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <?php
  echo pager($total);
}

function user_page(int $id, int $offset): void
{
  $user = query('SELECT * FROM users WHERE id=?', [$id])[0] ?? null;
  if (!$user)
  {
    header_html('Uživatel nenalezen');
    return;
  }
  header_html($user['name'] ?? "Uživatel $id");
  $csfd = 'https://www.csfd.cz/uzivatel/' . $id . ($user['slug'] ? '-' . $user['slug'] : '') . '/hodnoceni/';
  echo '<p>' . n($user['rating_count']) . ' hodnocení · stav ' . h(STATUS_NAMES[$user['status']] ?? '') .
       ' · staženo ' . h($user['scraped_at'] ?? '–') . ' · <a href="' . h($csfd) . '">profil na ČSFD</a></p>';
  distribution(query('SELECT stars, COUNT(*) AS count FROM ratings WHERE user_id=? GROUP BY stars', [$id]));

  $total = (int)value('SELECT COUNT(*) FROM ratings WHERE user_id=?', [$id]);
  echo pager($total);
  $rows = query('SELECT r.film_id, r.stars, r.rated_on, f.title, f.year, f.kind, f.slug, f.parent_id, s.average, s.rating_count
                 FROM ratings r JOIN films f ON f.id=r.film_id LEFT JOIN film_stats s ON s.film_id=r.film_id
                 WHERE r.user_id=? ORDER BY r.rated_on DESC, r.film_id LIMIT ' . PAGE_SIZE . ' OFFSET ' . $offset, [$id]);
  ?>
  <table>
    <tr><th>Film</th><th>Typ</th><th>Hodnocení</th><th class="num">Průměr všech</th><th>Datum</th></tr>
    <?php foreach ($rows as $r): ?>
      <tr><td><?= film_link($r) ?></td><td><?= h($r['kind']) ?></td><td class="stars"><?= stars((int)$r['stars']) ?></td>
        <td class="num"><?= average($r) ?></td><td><?= h($r['rated_on']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <?php
  echo pager($total);
}

function films_page(int $offset): void
{
  header_html('Filmy');
  $sort = ($_GET['sort'] ?? '') === 'average' ? 'average' : 'count';
  $min = max(1, (int)($_GET['min'] ?? ($sort === 'average' ? 100 : 1)));
  $kind = $_GET['kind'] ?? 'film';
  $where = 's.rating_count >= ?' . ($kind !== '' ? ' AND f.kind = ?' : '');
  $params = $kind !== '' ? [$min, $kind] : [$min];
  ?>
  <form class="filters">
    <input type="hidden" name="view" value="films">
    Řadit <select name="sort"><option value="count">podle počtu hodnocení</option>
      <option value="average" <?= $sort === 'average' ? 'selected' : '' ?>>podle průměru</option></select>
    typ <select name="kind"><option value="">vše</option>
      <?php foreach (query('SELECT kind, COUNT(*) AS c FROM films GROUP BY kind ORDER BY c DESC LIMIT 15') as $k): ?>
        <option <?= $k['kind'] === $kind ? 'selected' : '' ?>><?= h($k['kind']) ?></option>
      <?php endforeach ?></select>
    min. hodnocení <input name="min" type="number" value="<?= $min ?>" min="1">
    <button>Zobrazit</button>
  </form>
  <?php
  $total = (int)value("SELECT COUNT(*) FROM film_stats s JOIN films f ON f.id=s.film_id WHERE $where", $params);
  if (!value('SELECT COUNT(*) FROM film_stats'))
    echo '<p class="note">Statistiky filmů se ještě nepřepočítaly, objeví se po dalším běhu scraperu.</p>';
  echo pager($total);
  $order = $sort === 'average' ? 's.average DESC, s.rating_count DESC' : 's.rating_count DESC, s.average DESC';
  $rows = query("SELECT f.id AS film_id, f.title, f.year, f.kind, f.slug, f.parent_id, s.rating_count, s.average
                 FROM film_stats s JOIN films f ON f.id=s.film_id WHERE $where
                 ORDER BY $order LIMIT " . PAGE_SIZE . ' OFFSET ' . $offset, $params);
  ?>
  <table>
    <tr><th class="num">#</th><th>Film</th><th>Typ</th><th class="num">Hodnocení</th><th class="num">Průměr</th></tr>
    <?php foreach ($rows as $i => $r): ?>
      <tr><td class="num"><?= $offset + $i + 1 ?></td><td><?= film_link($r) ?></td><td><?= h($r['kind']) ?></td>
        <td class="num"><?= n($r['rating_count']) ?></td><td class="num"><?= average($r) ?></td></tr>
    <?php endforeach ?>
  </table>
  <?php
  echo pager($total);
}

function film_page(int $id, int $offset): void
{
  $film = query('SELECT f.*, p.title AS parent_title FROM films f LEFT JOIN films p ON p.id=f.parent_id WHERE f.id=?', [$id])[0] ?? null;
  if (!$film)
  {
    header_html('Film nenalezen');
    return;
  }
  header_html($film['title'] . ($film['year'] ? ' (' . $film['year'] . ')' : ''));
  $csfd = csfd_film_url($id, $film['slug'], $film['parent_id']);
  $distribution = query('SELECT stars, COUNT(*) AS count FROM ratings WHERE film_id=? GROUP BY stars', [$id]);
  $total = array_sum(array_column($distribution, 'count'));
  $sum = 0;
  foreach ($distribution as $d)
    $sum += $d['stars'] * $d['count'];
  echo '<p>' . h($film['kind']) . ($film['parent_title'] ? ' · patří k <a href="' . h(url(['view' => 'film', 'id' => $film['parent_id']])) . '">' . h($film['parent_title']) . '</a>' : '') .
       ' · ' . n($total) . ' hodnocení' . ($total ? ' · průměr ' . number_format($sum / $total, 2, ',', '') . ' (' . round($sum / $total * 20) . ' %)' : '') .
       ' · <a href="' . h($csfd) . '">na ČSFD</a></p>';
  distribution($distribution);

  echo pager($total);
  $rows = query('SELECT r.user_id, r.stars, r.rated_on, u.name FROM ratings r JOIN users u ON u.id=r.user_id
                 WHERE r.film_id=? ORDER BY r.rated_on DESC, r.user_id LIMIT ' . PAGE_SIZE . ' OFFSET ' . $offset, [$id]);
  ?>
  <table>
    <tr><th>Uživatel</th><th>Hodnocení</th><th>Datum</th></tr>
    <?php foreach ($rows as $r): ?>
      <tr><td><?= user_link($r) ?></td><td class="stars"><?= stars((int)$r['stars']) ?></td><td><?= h($r['rated_on']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <?php
  echo pager($total);
}

function search_page(string $q): void
{
  header_html('Hledání: ' . $q);
  if (mb_strlen($q) < 2)
  {
    echo '<p>Zadej aspoň 2 znaky.</p>';
    return;
  }
  $like = '%' . addcslashes($q, '%_\\') . '%';
  $films = query('SELECT f.id AS film_id, f.title, f.year, f.kind, f.slug, f.parent_id, s.rating_count, s.average FROM films f
                  LEFT JOIN film_stats s ON s.film_id=f.id WHERE f.title LIKE ?
                  ORDER BY s.rating_count DESC LIMIT 50', [$like]);
  $users = query('SELECT id AS user_id, name, rating_count FROM users WHERE name LIKE ? ORDER BY rating_count DESC LIMIT 50', [$like]);
  ?>
  <h2>Filmy</h2>
  <table>
    <tr><th>Film</th><th>Typ</th><th class="num">Hodnocení</th><th class="num">Průměr</th></tr>
    <?php foreach ($films as $r): ?>
      <tr><td><?= film_link($r) ?></td><td><?= h($r['kind']) ?></td><td class="num"><?= n($r['rating_count']) ?></td><td class="num"><?= average($r) ?></td></tr>
    <?php endforeach ?>
  </table>
  <h2>Uživatelé</h2>
  <table>
    <tr><th>Uživatel</th><th class="num">Hodnocení</th></tr>
    <?php foreach ($users as $r): ?>
      <tr><td><?= user_link($r) ?></td><td class="num"><?= n($r['rating_count']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <?php
}

function average(array $row): string
{
  if ($row['average'] === null)
    return '';
  return number_format((float)$row['average'], 2, ',', '') . ' <small>(' . n($row['rating_count']) . ')</small>';
}

function distribution(array $rows): void
{
  $counts = array_fill(0, 6, 0);
  foreach ($rows as $r)
    $counts[(int)$r['stars']] = (int)$r['count'];
  $max = max(1, max($counts));
  echo '<div class="distribution">';
  for ($s = 5; $s >= 0; $s--)
    echo '<div><span class="stars">' . stars($s) . '</span><span class="bar" style="width:' . round($counts[$s] / $max * 100) . '%"></span>' . n($counts[$s]) . '</div>';
  echo '</div>';
}
