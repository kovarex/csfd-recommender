<?php
// Browser for the scraped ČSFD ratings database.
require __DIR__ . '/lib.php';
require __DIR__ . '/model.php';

require_password();
$view = $_GET['view'] ?? 'overview';
$id = (int)($_GET['id'] ?? 0);
$offset = (page_number() - 1) * PAGE_SIZE;

switch ($view)
{
  case 'users': users_page($offset); break;
  case 'user': user_page($id, $offset); break;
  case 'recommend': recommend_page($id); break;
  case 'films': films_page($offset); break;
  case 'film': film_page($id, $offset); break;
  case 'search': search_page(trim($_GET['q'] ?? '')); break;
  case 'series': series_page($offset); break;
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
  $statsStartedAt = value("SELECT value FROM crawl_state WHERE name='film_stats_started_at'");
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
      <tr><td><?= h(local_time($r['started_at'])) ?></td><td><?= $r['finished_at'] ? h(local_time($r['finished_at'])) : ($r['stop_reason'] ? '?' : 'běží') ?></td><td class="num"><?= n($r['requests']) ?></td>
        <td class="num"><?= n($r['users_done']) ?></td><td class="num"><?= n($r['ratings_saved']) ?></td>
        <td class="num"><?= $r['credits_remaining'] === null ? '' : n($r['credits_remaining']) ?></td><td><?= h($r['stop_reason']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <p class="note">Statistiky filmů přepočítány: <?= $statsAt ? h(local_time($statsAt)) . ', další přepočet po ' . h(local_time($statsAt + 4 * 3600)) : 'zatím ne' ?> (každé 4 hodiny, s nejbližším během).<?=
    $statsStartedAt > $statsAt ? ' Přepočet spuštěný ' . h(local_time($statsStartedAt)) . ' zatím nedoběhl, pokračuje po bězích: ' .
      h(value("SELECT value FROM crawl_state WHERE name='film_stats_progress'")) . '.' : '' ?></p>
  <?php foreach (query("SELECT name, value FROM crawl_state WHERE name LIKE 'error\\_%' ORDER BY name") as $e): ?>
    <p class="note">Poslední chyba kroku <?= h(substr($e['name'], 6)) ?>: <?= h($e['value']) ?>
      <?= $e['name'] === 'error_film-stats' ? 'Stihl: ' . h(value("SELECT value FROM crawl_state WHERE name='film_stats_progress'")) : '' ?></p>
  <?php endforeach ?>
  <?php foreach (query("SELECT name, value FROM crawl_state WHERE name LIKE 'running\\_%' ORDER BY name") as $e):
    if (time() - (int)$e['value'] > 5 * 60): ?>
    <p class="note">Krok <?= h(substr($e['name'], 8)) ?> spuštěný <?= h(local_time((int)$e['value'])) ?> nedoběhl (asi ho ukončil časový limit).
      <?= $e['name'] === 'running_film-stats' ? 'Stihl: ' . h(value("SELECT value FROM crawl_state WHERE name='film_stats_progress'")) : '' ?></p>
  <?php endif; endforeach ?>
  <?php
}

function users_page(int $offset): void
{
  header_html('Uživatelé');
  $columns = ['name' => 'name', 'id' => 'id', 'ratings' => 'rating_count', 'scraped' => 'scraped_at'];
  [$sort, $direction] = sort_param(array_keys($columns), 'ratings', 'desc');
  $q = trim($_GET['q'] ?? '');
  $where = 'status=2';
  $params = [];
  if ($q !== '')
  {
    // A number finds that user ID, anything else searches names.
    $where .= ctype_digit($q) ? ' AND (id=? OR name LIKE ?)' : ' AND name LIKE ?';
    $like = '%' . addcslashes($q, '%_\\') . '%';
    $params = ctype_digit($q) ? [$q, $like] : [$like];
  }
  ?>
  <form class="filters">
    <input type="hidden" name="view" value="users">
    <input type="hidden" name="sort" value="<?= h($sort) ?>"><input type="hidden" name="dir" value="<?= h($direction) ?>">
    <input name="q" class="wide" placeholder="Jméno nebo ID" value="<?= h($q) ?>" autofocus> <button>Hledat</button>
    <?php if ($q !== ''): ?><a href="<?= h(url(['view' => 'users', 'sort' => $sort, 'dir' => $direction])) ?>">zrušit</a><?php endif ?>
  </form>
  <?php
  $total = (int)value("SELECT COUNT(*) FROM users WHERE $where", $params);
  echo '<p class="note">' . n($total) . ' uživatelů</p>';
  echo pager($total);
  $order = $columns[$sort] . ' ' . strtoupper($direction) . ($sort === 'id' ? '' : ', id');
  $rows = query("SELECT id AS user_id, name, slug, rating_count, scraped_at FROM users WHERE $where
                 ORDER BY $order LIMIT " . PAGE_SIZE . ' OFFSET ' . $offset, $params);
  ?>
  <table>
    <tr><th class="num">#</th><?= sort_header('Uživatel', 'name', 'asc') ?><?= sort_header('ID', 'id', 'asc', 'num') ?>
      <?= sort_header('Hodnocení', 'ratings', 'desc', 'num') ?><?= sort_header('Staženo', 'scraped', 'desc') ?></tr>
    <?php foreach ($rows as $i => $r): ?>
      <tr><td class="num"><?= $offset + $i + 1 ?></td><td><?= user_link($r) ?></td><td class="num"><?= (int)$r['user_id'] ?></td>
        <td class="num"><?= n($r['rating_count']) ?></td><td><?= h(local_time($r['scraped_at'])) ?></td></tr>
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
       ' · staženo ' . h(local_time($user['scraped_at'])) . ' · <a href="' . h($csfd) . '">profil na ČSFD</a>' .
       ' · <b><a href="' . h(url(['view' => 'recommend', 'id' => $id])) . '">Doporučení</a></b></p>';
  distribution(query('SELECT stars, COUNT(*) AS count FROM ratings WHERE user_id=? GROUP BY stars', [$id]));

  $total = (int)value('SELECT COUNT(*) FROM ratings WHERE user_id=?', [$id]);
  echo pager($total);
  $rows = query('SELECT r.film_id, r.stars, r.rated_on, f.title, f.year, f.kind, f.slug, f.parent_id, s.average, s.rating_count, s.csfd_percent, s.csfd_count
                 FROM ratings r JOIN films f ON f.id=r.film_id LEFT JOIN film_stats s ON s.film_id=r.film_id
                 WHERE r.user_id=? ORDER BY r.rated_on DESC, r.film_id LIMIT ' . PAGE_SIZE . ' OFFSET ' . $offset, [$id]);
  ?>
  <table>
    <tr><th>Film</th><th>Typ</th><th>Hodnocení</th><?= RATING_HEADERS ?><th>Datum</th></tr>
    <?php foreach ($rows as $r): ?>
      <tr><td><?= film_link($r) ?></td><td><?= h($r['kind']) ?></td><td class="stars"><?= stars((int)$r['stars']) ?></td>
        <?= rating_cells($r) ?><td><?= h($r['rated_on']) ?></td></tr>
    <?php endforeach ?>
  </table>
  <?php
  echo pager($total);
}

/** Kinds for the film list's filter, cached by the film stats refresh; counted from films before the first one. */
function film_kinds(): array
{
  $cached = value("SELECT value FROM crawl_state WHERE name='film_kinds'");
  if ($cached !== null)
    return json_decode($cached, true);
  return array_column(query('SELECT kind, COUNT(*) AS c FROM films GROUP BY kind ORDER BY c DESC LIMIT 15'), 'kind');
}

function films_page(int $offset): void
{
  header_html('Filmy');
  $sort = ($_GET['sort'] ?? '') === 'average' ? 'average' : 'count';
  $min = max(1, (int)($_GET['min'] ?? ($sort === 'average' ? 100 : 1)));
  $kind = $_GET['kind'] ?? 'film';
  $genre = isset(GENRE_NAMES[$_GET['genre'] ?? '']) ? $_GET['genre'] : '';
  $where = 's.rating_count >= ?' . ($kind !== '' ? ' AND s.kind = ?' : '');
  $params = $kind !== '' ? [$min, $kind] : [$min];
  if ($genre !== '')
  {
    $where .= ' AND s.film_id IN (SELECT film_id FROM film_genres WHERE genre = ?)';
    $params[] = $genre;
  }
  ?>
  <form class="filters">
    <input type="hidden" name="view" value="films">
    Řadit <select name="sort"><option value="count">podle našeho počtu hodnocení</option>
      <option value="average" <?= $sort === 'average' ? 'selected' : '' ?>>podle našeho průměru</option></select>
    typ <select name="kind"><option value="">vše</option>
      <?php foreach (film_kinds() as $k): ?>
        <option <?= $k === $kind ? 'selected' : '' ?>><?= h($k) ?></option>
      <?php endforeach ?></select>
    žánr <select name="genre"><option value="">vše</option>
      <?php foreach (GENRE_NAMES as $key => $name): ?>
        <option value="<?= h($key) ?>" <?= $key === $genre ? 'selected' : '' ?>><?= h($name) ?></option>
      <?php endforeach ?></select>
    min. hodnocení <input name="min" type="number" value="<?= $min ?>" min="1">
    <button>Zobrazit</button>
  </form>
  <?php
  $total = (int)value("SELECT COUNT(*) FROM film_stats s WHERE $where", $params);
  if (!value('SELECT 1 FROM film_stats LIMIT 1'))
    echo '<p class="note">Statistiky filmů se ještě nepřepočítaly, objeví se po dalším běhu scraperu.</p>';
  echo pager($total);
  // Ties are broken by the ID, which is part of every index, so the order is read from an index without sorting.
  $order = $sort === 'average' ? 's.average DESC, s.film_id DESC' : 's.rating_count DESC, s.film_id DESC';
  $rows = query("SELECT f.id AS film_id, f.title, f.year, f.kind, f.slug, f.parent_id, s.rating_count, s.average, s.csfd_percent, s.csfd_count
                 FROM film_stats s JOIN films f ON f.id=s.film_id WHERE $where
                 ORDER BY $order LIMIT " . PAGE_SIZE . ' OFFSET ' . $offset, $params);
  ?>
  <table>
    <tr><th class="num">#</th><th>Film</th><th>Typ</th><?= RATING_HEADERS ?></tr>
    <?php foreach ($rows as $i => $r): ?>
      <tr><td class="num"><?= $offset + $i + 1 ?></td><td><?= film_link($r) ?></td><td><?= h($r['kind']) ?></td>
        <?= rating_cells($r) ?></tr>
    <?php endforeach ?>
  </table>
  <?php
  echo pager($total);
}

function recommend_page(int $id): void
{
  $user = query('SELECT * FROM users WHERE id=?', [$id])[0] ?? null;
  $name = $user['name'] ?? "Uživatel $id";
  header_html("Doporučení: $name");
  $model = load_model(model_path());
  if (!$model)
  {
    echo '<p class="note">Model ještě není nahraný. Nahraje ho update-model.sh z PC po natrénování.</p>';
    return;
  }
  $list = isset(RECOMMEND_LISTS[$_GET['list'] ?? '']) ? $_GET['list'] : 'best';
  $genre = isset(GENRE_NAMES[$_GET['genre'] ?? '']) ? $_GET['genre'] : '';
  $min = max(1, (int)($_GET['min'] ?? 100));
  // Kinds are checkboxes; k=1 marks a submitted form, so unchecking everything isn't mistaken for the defaults.
  $allKinds = array_values(array_unique(array_merge(DEFAULT_RECOMMEND_KINDS, film_kinds())));
  $kinds = isset($_GET['k']) ? array_values(array_intersect($allKinds, (array)($_GET['kind'] ?? []))) : DEFAULT_RECOMMEND_KINDS;
  $series = (int)($_GET['series'] ?? 0);
  // Documentaries are off by default, like in ČSFD's rankings: their few viewers seek them out and rate them highly,
  // so the model rates them above almost everything for everyone.
  $documentaries = !empty($_GET['docs']) || $genre === 'dokumentarni';
  $filters = ['genre' => $genre, 'min' => $_GET['min'] ?? null, 'docs' => $documentaries ? 1 : null]
             + (isset($_GET['k']) ? ['k' => 1, 'kind' => $kinds] : []);
  $tabs = [];
  foreach (RECOMMEND_LISTS as $key => [$label])
    $tabs[] = $key === $list ? '<b>' . h($label) . '</b>'
                             : '<a href="' . h(url(['view' => 'recommend', 'id' => $id, 'list' => $key] + $filters)) . '">' . h($label) . '</a>';
  echo '<p><a href="' . h(url(['view' => 'user', 'id' => $id])) . '">← hodnocení uživatele</a></p>';
  echo '<p class="filters">' . implode(' · ', $tabs) . '</p>';
  echo '<p class="note">' . h(RECOMMEND_LISTS[$list][1]) . '</p>';

  $started = microtime(true);
  $ratings = [];
  foreach (query('SELECT film_id, stars FROM ratings WHERE user_id=?', [$id]) as $r)
    $ratings[(int)$r['film_id']] = (int)$r['stars'];
  $taste = fold_in($model, $ratings);
  if (!$taste || $taste['films'] < 10)
  {
    echo '<p class="note">Na doporučení je potřeba aspoň 10 hodnocení filmů, které model zná.</p>';
    return;
  }

  if ($series)
    series_episodes($model, $taste, $id, $ratings, $series);
  elseif ($list === 'users')
    similar_users($model, $taste, $id);
  else
  {
    ?>
    <form class="filters">
      <input type="hidden" name="view" value="recommend"><input type="hidden" name="id" value="<?= $id ?>">
      <input type="hidden" name="list" value="<?= h($list) ?>"><input type="hidden" name="k" value="1">
      <span class="kinds">typ
        <?php foreach ($allKinds as $k): ?>
          <label><input type="checkbox" name="kind[]" value="<?= h($k) ?>" <?= in_array($k, $kinds, true) ? 'checked' : '' ?>> <?= h($k) ?></label>
        <?php endforeach ?></span>
      <span class="kinds"><label><input type="checkbox" name="docs" value="1" <?= $documentaries ? 'checked' : '' ?>> dokumenty</label></span>
      žánr <select name="genre"><option value="">vše</option>
        <?php foreach (GENRE_NAMES as $key => $genreName): ?>
          <option value="<?= h($key) ?>" <?= $key === $genre ? 'selected' : '' ?>><?= h($genreName) ?></option>
        <?php endforeach ?></select>
      min. hodnocení <input name="min" type="number" value="<?= $min ?>" min="1">
      <button>Zobrazit</button>
    </form>
    <?php
    recommended_films($model, $taste, $id, $list, $ratings, $genre, $kinds, $min, $documentaries);
  }
  echo '<p class="note">Model natrénován ' . h(local_time($model['trained_at'])) . ' na ' . n($model['ratings']) .
       ' hodnoceních (průměrná chyba odhadu ' . number_format($model['validation_rmse'], 2, ',', '') . ' hvězdičky). ' .
       'Vkus spočítán z ' . n($taste['films']) . ' hodnocení za ' . number_format(microtime(true) - $started, 1, ',', '') . ' s.</p>';
}

function recommended_films(array $model, array $taste, int $userId, string $list, array $ratings, string $genre,
                           array $kinds, int $min, bool $documentaries): void
{
  $idSet = fn(string $sql, string $value): array => array_flip(array_map('intval', array_column(query($sql, [$value]), 'film_id')));
  $inGenre = $genre !== '' ? $idSet('SELECT film_id FROM film_genres WHERE genre=?', $genre) : null;
  $hidden = $documentaries ? [] : $idSet('SELECT film_id FROM film_genres WHERE genre=?', 'dokumentarni');
  $ofKind = allowed_films($kinds);
  $scores = score_films($model, $taste);
  $ranked = [];
  foreach ($scores as $i => [$predicted, $cosine])
  {
    $filmId = $model['film_ids'][$i];
    if ($model['film_counts'][$i] < $min || isset($ratings[$filmId])
        || ($inGenre !== null && !isset($inGenre[$filmId])) || ($ofKind !== null && !isset($ofKind[$filmId]))
        || isset($hidden[$filmId]))
      continue;
    $difference = clip_stars($predicted) - clip_stars($model['film_average'][$i]);
    $ranked[$i] = ['best' => $predicted, 'above' => $difference, 'below' => -$difference, 'match' => $cosine,
                   'opposite' => -$cosine][$list];
  }
  arsort($ranked);
  $top = array_slice($ranked, 0, 50, true);
  if (!$top)
  {
    echo '<p class="note">Žádné filmy neodpovídají filtru.</p>';
    return;
  }
  prediction_table($model, $scores, array_keys($top), $ratings, $userId);
}

/**
 * [film ID => true] for the chosen kinds. ANTHOLOGY_EPISODE stands for the episodes of anthologies (see
 * ANTHOLOGY_MIN_SPREAD); such series are then offered by episode instead of as a whole.
 */
function allowed_films(array $kinds): array
{
  $real = array_values(array_diff($kinds, [ANTHOLOGY_EPISODE]));
  $allowed = $real ? array_flip(array_map('intval', array_column(query('SELECT film_id FROM film_stats WHERE kind IN (' .
    implode(',', array_fill(0, count($real), '?')) . ')', $real), 'film_id'))) : [];
  if (in_array(ANTHOLOGY_EPISODE, $kinds, true))
  {
    $anthologies = array_merge(EXTRA_ANTHOLOGIES, array_map('intval', array_column(query(
      "SELECT s.series_id FROM series_stats s JOIN film_genres g ON g.film_id=s.series_id AND g.genre='antologie'
       WHERE s.users >= ? AND s.spread >= ?", [ANTHOLOGY_MIN_USERS, ANTHOLOGY_MIN_SPREAD]), 'series_id')));
    $anthologies = array_values(array_diff($anthologies, NOT_ANTHOLOGIES));
    if ($anthologies)
    {
      $list = implode(',', $anthologies);
      foreach (query("SELECT id FROM films WHERE kind='epizoda' AND (parent_id IN ($list)
                      OR parent_id IN (SELECT id FROM films WHERE parent_id IN ($list)))") as $r)
        $allowed[(int)$r['id']] = true;
      foreach ($anthologies as $seriesId)
        unset($allowed[$seriesId]);
    }
  }
  return $allowed;
}

/** Episodes of a series ranked by the prediction for the user, including the ones they rated. */
function series_episodes(array $model, array $taste, int $userId, array $ratings, int $seriesId): void
{
  $series = query('SELECT id AS film_id, title, year, kind, slug, parent_id FROM films WHERE id=?', [$seriesId])[0] ?? null;
  if (!$series)
    return;
  echo '<h2>Epizody: ' . film_link($series) . '</h2>';
  // Episodes hang either right under the series or under its seasons.
  $episodes = array_map('intval', array_column(query(
    "SELECT id FROM films WHERE (parent_id=? OR parent_id IN (SELECT id FROM films WHERE parent_id=?)) AND kind <> 'série'",
    [$seriesId, $seriesId]), 'id'));
  $scores = score_films($model, $taste);
  $ranked = [];
  foreach ($episodes as $filmId)
  {
    $i = $model['film_index'][$filmId] ?? null;
    if ($i !== null)
      $ranked[$i] = $scores[$i][0];
  }
  arsort($ranked);
  if (!$ranked)
  {
    echo '<p class="note">Model nezná žádnou epizodu tohoto seriálu (zná jen epizody s aspoň 30 hodnoceními).</p>';
    return;
  }
  prediction_table($model, $scores, array_keys($ranked), $ratings, $userId);
  if (count($ranked) < count($episodes))
    echo '<p class="note">' . n(count($ranked)) . ' z ' . n(count($episodes)) . ' epizod; ostatní mají méně než 30 hodnocení.</p>';
}

/** Table of films (model indexes, in order) with the predictions; series get a link to their episodes. */
function prediction_table(array $model, array $scores, array $indexes, array $ratings, int $userId): void
{
  $ids = array_map(fn($i) => $model['film_ids'][$i], $indexes);
  $films = [];
  foreach (query('SELECT f.id AS film_id, f.title, f.year, f.kind, f.slug, f.parent_id, s.average, s.rating_count, s.csfd_percent, s.csfd_count
                  FROM films f LEFT JOIN film_stats s ON s.film_id=f.id WHERE f.id IN (' . implode(',', $ids) . ')') as $f)
    $films[(int)$f['film_id']] = $f;
  $rated = array_intersect_key($ratings, array_flip($ids)) !== [];
  ?>
  <table>
    <tr><th class="num">#</th><th>Film</th><th>Typ</th><?= $rated ? '<th>Tvoje hodnocení</th>' : '' ?><?= RATING_HEADERS ?>
      <th class="num">Odhad pro tebe</th><th class="num">Průměrný divák</th><th class="num">Rozdíl</th><th class="num">Shoda vkusu</th></tr>
    <?php $rank = 0; foreach ($indexes as $i): $filmId = $model['film_ids'][$i]; $f = $films[$filmId] ?? null; if (!$f) continue;
      [$predicted, $cosine] = $scores[$i]; $you = clip_stars($predicted); $average = clip_stars($model['film_average'][$i]); ?>
      <tr><td class="num"><?= ++$rank ?></td><td><?= film_link($f) ?></td>
        <td><?= h($f['kind']) ?><?= $f['kind'] === 'seriál' ? ' <small>(<a href="' . h(url(['view' => 'recommend', 'id' => $userId, 'series' => $filmId])) . '">epizody</a>)</small>' : '' ?></td>
        <?= $rated ? '<td class="stars">' . (isset($ratings[$filmId]) ? stars($ratings[$filmId]) : '') . '</td>' : '' ?>
        <?= rating_cells($f) ?><td class="num"><?= percent($you) ?></td><td class="num"><?= percent($average) ?></td>
        <td class="num"><?= sprintf('%+d', round(($you - $average) * 20)) ?>&nbsp;%</td>
        <td class="num"><?= number_format($cosine, 2, ',', '') ?></td></tr>
    <?php endforeach ?>
  </table>
  <p class="note">Procenta jako na ČSFD (hvězdičky × 20). Shoda vkusu je od −1 (opačný vkus) do 1 (přesně tvůj).</p>
  <?php
}

function similar_users(array $model, array $taste, int $id): void
{
  $scores = score_users($model, $taste, $id);
  arsort($scores);
  $closest = array_slice($scores, 0, 20, true);
  $farthest = array_reverse(array_slice($scores, -20, null, true), true);
  $ids = array_merge(array_keys($closest), array_keys($farthest));
  if (!$ids)
    return;
  $users = [];
  foreach (query('SELECT id AS user_id, name, rating_count FROM users WHERE id IN (' . implode(',', array_map('intval', $ids)) . ')') as $u)
    $users[(int)$u['user_id']] = $u;
  foreach (['Nejpodobnější vkus' => $closest, 'Nejodlišnější vkus' => $farthest] as $title => $group)
  {
    echo '<h2>' . h($title) . '</h2>';
    ?>
    <table>
      <tr><th>Uživatel</th><th class="num">Hodnocení</th><th class="num">Shoda vkusu</th><th class="num">Společných filmů</th>
        <th class="num">Průměrný rozdíl</th></tr>
      <?php foreach ($group as $userId => $cosine):
        $common = query('SELECT COUNT(*) AS c, AVG(ABS(CAST(a.stars AS SIGNED) - CAST(b.stars AS SIGNED))) AS d FROM ratings a
                         JOIN ratings b ON b.user_id=? AND b.film_id=a.film_id WHERE a.user_id=?', [$userId, $id])[0];
        $u = $users[$userId] ?? ['user_id' => $userId, 'name' => null, 'rating_count' => 0]; ?>
        <tr><td><?= user_link($u) ?> <small>(<a href="<?= h(url(['view' => 'recommend', 'id' => $userId])) ?>">doporučení</a>)</small></td>
          <td class="num"><?= n($u['rating_count']) ?></td><td class="num"><?= number_format($cosine, 2, ',', '') ?></td>
          <td class="num"><?= n($common['c']) ?></td>
          <td class="num"><?= $common['c'] ? number_format((float)$common['d'], 2, ',', '') . ' ★' : '' ?></td></tr>
      <?php endforeach ?>
    </table>
    <?php
  }
}

function series_page(int $offset): void
{
  header_html('Seriály podle rozptylu hodnocení epizod');
  echo '<p class="note">Jak rozdílně hodnotí stejný divák jednotlivé epizody (směrodatná odchylka jeho hodnocení ve hvězdičkách, ' .
       'průměr přes diváky s aspoň 3 epizodami). Vysoká hodnota znamená, že epizody se posuzují každá zvlášť, třeba u antologií.</p>';
  $min = max(1, (int)($_GET['min'] ?? 50));
  [$sort, $direction] = sort_param(['spread', 'users', 'episodes'], 'spread', 'desc');
  ?>
  <form class="filters"><input type="hidden" name="view" value="series">
    min. diváků <input name="min" type="number" value="<?= $min ?>" min="1"><button>Zobrazit</button></form>
  <?php
  $total = (int)value('SELECT COUNT(*) FROM series_stats WHERE users >= ?', [$min]);
  echo pager($total);
  $rows = query("SELECT t.series_id AS film_id, t.users, t.episodes, t.spread, f.title, f.year, f.kind, f.slug, f.parent_id,
                        s.average, s.rating_count, s.csfd_percent, s.csfd_count
                 FROM series_stats t JOIN films f ON f.id=t.series_id LEFT JOIN film_stats s ON s.film_id=t.series_id
                 WHERE t.users >= ? ORDER BY t.$sort $direction, t.series_id LIMIT " . PAGE_SIZE . ' OFFSET ' . $offset, [$min]);
  ?>
  <table>
    <tr><th>Seriál</th><?= RATING_HEADERS ?><?= sort_header('Rozptyl', 'spread', 'desc', 'num') ?><?= sort_header('Diváků', 'users', 'desc', 'num') ?>
      <?= sort_header('Epizod', 'episodes', 'desc', 'num') ?></tr>
    <?php foreach ($rows as $r): ?>
      <tr><td><?= film_link($r) ?></td><?= rating_cells($r) ?><td class="num"><?= number_format((float)$r['spread'], 2, ',', '') ?></td>
        <td class="num"><?= n($r['users']) ?></td><td class="num"><?= n($r['episodes']) ?></td></tr>
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
  $csfdRating = query('SELECT csfd_percent, csfd_count FROM film_pages WHERE film_id=? AND csfd_percent IS NOT NULL', [$id])[0] ?? null;
  $genres = array_map(fn($g) => h(GENRE_NAMES[$g['genre']] ?? $g['genre']),
                     query('SELECT genre FROM film_genres WHERE film_id=? ORDER BY genre', [$id]));
  echo '<p>' . h($film['kind']) . ($genres ? ' · ' . implode(', ', $genres) : '') . ($film['parent_title'] ? ' · patří k <a href="' . h(url(['view' => 'film', 'id' => $film['parent_id']])) . '">' . h($film['parent_title']) . '</a>' : '') .
       ($csfdRating ? ' · ČSFD ' . (int)$csfdRating['csfd_percent'] . "\u{00a0}% z " . n($csfdRating['csfd_count']) . ' hodnocení' : '') .
       ' · naše ' . ($total ? percent($sum / $total) . ' z ' : '') . n($total) . ' hodnocení' .
       ' · <a href="' . h($csfd) . '">na ČSFD</a></p>';
  $seriesStats = query('SELECT users, episodes, spread FROM series_stats WHERE series_id=?', [$id])[0] ?? null;
  if ($seriesStats)
    echo '<p class="note">Epizody: ' . n($seriesStats['episodes']) . ' hodnocených, stejný divák je hodnotí v průměru s rozptylem ' .
         number_format((float)$seriesStats['spread'], 2, ',', '') . ' ★ (' . n($seriesStats['users']) . ' diváků s aspoň 3 epizodami, ' .
         '<a href="' . h(url(['view' => 'series'])) . '">srovnání seriálů</a>).</p>';
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

/**
 * [condition, parameters] of the title search over film_stats (rated films) joined with films. Its full-text index
 * finds the films whose words start with the searched ones, then LIKE keeps those containing the text as typed:
 * LIKE over films.title alone took over a minute on NFS. Words under 3 letters aren't in the index
 * (innodb_ft_min_token_size), so a search made only of those uses LIKE alone.
 */
function title_search(string $q, string $like): array
{
  // film_stats gets its titles and the index at its next rebuild after the 26 Oct 2026 update.
  // SHOW INDEX, not information_schema: that is slow on the shared NFS database server.
  $indexed = (bool)query("SHOW INDEX FROM film_stats WHERE Key_name='title'");
  if (!$indexed)
    return ['f.title LIKE ?', [$like]];
  preg_match_all('/[\p{L}\p{N}]{3,}/u', $q, $words);
  if (!$words[0])
    return ['s.title LIKE ?', [$like]];
  return ['MATCH(s.title) AGAINST(? IN BOOLEAN MODE) AND s.title LIKE ?',
          [implode(' ', array_map(fn($w) => "+$w*", $words[0])), $like]];
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
  $started = microtime(true);
  [$condition, $params] = title_search($q, $like);
  $films = query("SELECT f.id AS film_id, f.title, f.year, f.kind, f.slug, f.parent_id, s.rating_count, s.average, s.csfd_percent, s.csfd_count
                  FROM film_stats s JOIN films f ON f.id=s.film_id WHERE $condition
                  ORDER BY s.rating_count DESC LIMIT 50", $params);
  $filmsDone = microtime(true);
  $users = query('SELECT id AS user_id, name, rating_count FROM users WHERE name LIKE ? ORDER BY rating_count DESC LIMIT 50', [$like]);
  printf("<!-- films %.2f s (%s), users %.2f s -->\n", $filmsDone - $started, h($condition), microtime(true) - $filmsDone);
  ?>
  <h2>Filmy</h2>
  <table>
    <tr><th>Film</th><th>Typ</th><?= RATING_HEADERS ?></tr>
    <?php foreach ($films as $r): ?>
      <tr><td><?= film_link($r) ?></td><td><?= h($r['kind']) ?></td><?= rating_cells($r) ?></tr>
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

/** Cells under RATING_HEADERS: ČSFD's rating (from the film's ČSFD page) and ours (from the scraped ratings). */
function rating_cells(array $row): string
{
  $csfd = isset($row['csfd_percent']) ? (int)$row['csfd_percent'] . "\u{00a0}% <small>(" . n($row['csfd_count']) . ')</small>' : '–';
  $ours = isset($row['average']) ? percent($row['average']) . ' <small>(' . n($row['rating_count']) . ')</small>' : '–';
  return "<td class=\"num\">$csfd</td><td class=\"num\">$ours</td>";
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
