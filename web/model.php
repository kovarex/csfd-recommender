<?php
// The trained model (written by ml/publish.py, uploaded by update-model.sh) and the same maths as ml/recommend.py:
// a user's taste vector is computed from their ratings against the film vectors, then every film is scored.
// Vectors stay in the file's binary string and are unpacked one row at a time, which keeps PHP's memory low.

function model_path(): string
{
  return config()['web']['model'] ?? '/home/protected/csfd-model.bin';
}

function load_model(string $path): ?array
{
  $data = @file_get_contents($path);
  if ($data === false)
    return null;
  $end = strpos($data, "\n");
  $model = json_decode(substr($data, 0, $end), true);
  $offset = $end + 1;
  $films = $model['films'];
  $users = $model['users'];
  $k = $model['factors'];
  $take = function (int $bytes) use ($data, &$offset): string
  {
    $part = substr($data, $offset, $bytes);
    $offset += $bytes;
    return $part;
  };
  $unpack = fn(string $format, string $bytes): array => $bytes === '' ? [] : array_values(unpack($format . '*', $bytes));
  $model['film_ids'] = $unpack('V', $take(4 * $films));
  $model['film_bias'] = $unpack('g', $take(4 * $films));
  $model['film_counts'] = $unpack('V', $take(4 * $films));
  $model['film_average'] = $unpack('g', $take(4 * $films));
  $model['film_flags'] = $unpack('C', $take($films));
  $model['film_vectors'] = $take(4 * $films * $k);
  $model['user_ids'] = $unpack('V', $take(4 * $users));
  $model['user_counts'] = $unpack('V', $take(4 * $users));
  $model['user_vectors'] = $take(4 * $users * $k);
  $model['film_index'] = array_flip($model['film_ids']);
  return $model;
}

/** Row $i of a vector block as a 0-based array of floats. */
function vector_row(array $model, string $block, int $i): array
{
  $k = $model['factors'];
  return array_values(unpack("g$k", $model[$block], 4 * $k * $i));
}

/** Least-squares taste vector and bias from [film ID => stars]; null when none of the films is in the model. */
function fold_in(array $model, array $ratings): ?array
{
  $k = $model['factors'];
  $size = $k + 1;
  $a = array_fill(0, $size * $size, 0.0);
  $b = array_fill(0, $size, 0.0);
  $n = 0;
  foreach ($ratings as $filmId => $stars)
  {
    $i = $model['film_index'][$filmId] ?? null;
    if ($i === null)
      continue;
    $n++;
    $q = vector_row($model, 'film_vectors', $i);
    $q[] = 1.0;
    $target = $stars - $model['mean'] - $model['film_bias'][$i];
    for ($r = 0; $r < $size; $r++)
    {
      $qr = $q[$r];
      $b[$r] += $qr * $target;
      $row = $r * $size;
      for ($c = 0; $c <= $r; $c++)
        $a[$row + $c] += $qr * $q[$c];
    }
  }
  if ($n === 0)
    return null;
  // Same regularisation as in training (see fold_in in ml/recommend.py).
  for ($r = 0; $r < $size; $r++)
  {
    $a[$r * $size + $r] += ($r < $k ? $model['reg'] : 1e-3) * $n;
    for ($c = 0; $c < $r; $c++)
      $a[$c * $size + $r] = $a[$r * $size + $c];
  }
  $solution = solve($a, $b, $size);
  return ['vector' => array_slice($solution, 0, $k), 'bias' => $solution[$k], 'films' => $n];
}

/** Solves the linear system a·x = b (a is size × size, row by row) by Gaussian elimination. */
function solve(array $a, array $b, int $size): array
{
  for ($col = 0; $col < $size; $col++)
  {
    $pivot = $col;
    for ($r = $col + 1; $r < $size; $r++)
      if (abs($a[$r * $size + $col]) > abs($a[$pivot * $size + $col]))
        $pivot = $r;
    if ($pivot !== $col)
    {
      for ($c = 0; $c < $size; $c++)
        [$a[$col * $size + $c], $a[$pivot * $size + $c]] = [$a[$pivot * $size + $c], $a[$col * $size + $c]];
      [$b[$col], $b[$pivot]] = [$b[$pivot], $b[$col]];
    }
    $diagonal = $a[$col * $size + $col];
    for ($r = $col + 1; $r < $size; $r++)
    {
      $factor = $a[$r * $size + $col] / $diagonal;
      if ($factor == 0.0)
        continue;
      for ($c = $col; $c < $size; $c++)
        $a[$r * $size + $c] -= $factor * $a[$col * $size + $c];
      $b[$r] -= $factor * $b[$col];
    }
  }
  $x = array_fill(0, $size, 0.0);
  for ($r = $size - 1; $r >= 0; $r--)
  {
    $sum = $b[$r];
    for ($c = $r + 1; $c < $size; $c++)
      $sum -= $a[$r * $size + $c] * $x[$c];
    $x[$r] = $sum / $a[$r * $size + $r];
  }
  return $x;
}

function clip_stars(float $stars): float
{
  return max(0.0, min(5.0, $stars));
}

/**
 * For every film: [index => [predicted stars, cosine between the film's and the user's vector]]. The cosine is 1
 * for a film that matches the taste exactly, -1 for the opposite, regardless of how good the film is.
 */
function score_films(array $model, array $taste): array
{
  $u = $taste['vector'];
  $k = $model['factors'];
  $norm = sqrt(array_sum(array_map(fn($x) => $x * $x, $u))) ?: 1e-9;
  $base = $model['mean'] + $taste['bias'];
  $scores = [];
  $count = count($model['film_ids']);
  for ($i = 0; $i < $count; $i++)
  {
    $v = unpack("g$k", $model['film_vectors'], 4 * $k * $i);
    $dot = 0.0;
    $length = 0.0;
    for ($j = 0; $j < $k; $j++)
    {
      $x = $v[$j + 1];
      $dot += $x * $u[$j];
      $length += $x * $x;
    }
    $scores[$i] = [$base + $model['film_bias'][$i] + $dot, $dot / (sqrt($length) * $norm ?: 1e-9)];
  }
  return $scores;
}

/** [user ID => cosine] for every user in the model except $exceptId. */
function score_users(array $model, array $taste, int $exceptId): array
{
  $u = $taste['vector'];
  $k = $model['factors'];
  $norm = sqrt(array_sum(array_map(fn($x) => $x * $x, $u))) ?: 1e-9;
  $scores = [];
  foreach ($model['user_ids'] as $i => $userId)
  {
    if ($userId === $exceptId)
      continue;
    $v = unpack("g$k", $model['user_vectors'], 4 * $k * $i);
    $dot = 0.0;
    $length = 0.0;
    for ($j = 0; $j < $k; $j++)
    {
      $x = $v[$j + 1];
      $dot += $x * $u[$j];
      $length += $x * $x;
    }
    $scores[$userId] = $dot / (sqrt($length) * $norm ?: 1e-9);
  }
  return $scores;
}
