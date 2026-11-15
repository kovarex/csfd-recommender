#!/bin/sh
# On the PC (WSL): downloads the database export that NFS publishes weekly into ~/csfd-data over HTTPS (ratings.csv.gz,
# films.csv.gz, genres.csv.gz, users.csv.gz), with no SSH and no password. The address is the one from the project
# notes, ending in /current/; it is remembered after the first run:
#   ./download-data.sh https://csfd-recommender.com/data/<hash>/current/
#   ./download-data.sh
# Files already downloaded whole are skipped, so an interrupted download continues when run again.
set -e
DATA=${CSFD_DATA:-$HOME/csfd-data}
URL_FILE="$HOME/.csfd-data-url"
[ -n "$1" ] && echo "$1" > "$URL_FILE"
if [ ! -s "$URL_FILE" ]; then
  echo "Run once with the address of the export: ./download-data.sh https://csfd-recommender.com/data/<hash>/current/"
  exit 1
fi
URL=$(cat "$URL_FILE")
URL=${URL%/}
# The export comes in parts (NFS can't write it in one run); they are kept in parts/ so that the next download only
# fetches what changed, and joined into one ratings.csv.gz next to films, genres and users.
PARTS="$DATA/parts"
mkdir -p "$PARTS"
cd "$PARTS"
curl -fsS "$URL/manifest.txt" -o manifest.txt
head -1 manifest.txt
missing() { grep -v '^#' manifest.txt | while read -r size name; do
  [ "$(stat -c %s "$name" 2>/dev/null)" = "$size" ] || echo "$name"; done; }
echo "Downloading $(missing | wc -l) of $(grep -vc '^#' manifest.txt) files..."
# NFS sends each connection slowly to Europe, so 8 files at once.
missing | xargs -r -P 8 -I{} curl -fsS --retry 5 "$URL/{}" -o "{}"
if [ -n "$(missing)" ]; then
  echo "Not downloaded whole: $(missing | tr '\n' ' ')- run again"
  exit 1
fi
# Parts of an older export that this one no longer has.
for f in ratings-*.csv.gz; do grep -q " $f\$" manifest.txt || rm -f "$f"; done
if ! cmp -s manifest.txt joined-manifest.txt || [ ! -f "$DATA/ratings.csv.gz" ]; then
  echo "Joining the ratings into $DATA/ratings.csv.gz..."
  # The header of the first part only; gzip -1 because it's fast and the file stays about the same size.
  { first=1; for f in ratings-*.csv.gz; do
      if [ -n "$first" ]; then zcat "$f"; first=; else zcat "$f" | tail -n +2; fi
    done; } | gzip -1 > "$DATA/ratings.csv.gz.tmp"
  mv "$DATA/ratings.csv.gz.tmp" "$DATA/ratings.csv.gz"
  cp films.csv.gz genres.csv.gz users.csv.gz "$DATA/"
  cp manifest.txt joined-manifest.txt
fi
# Parts downloaded straight into the data folder by an older version of this script.
rm -f "$DATA"/ratings-*.csv.gz "$DATA/manifest.txt"
echo "Done: $DATA/ratings.csv.gz ($(du -h "$DATA/ratings.csv.gz" | cut -f1)), films, genres, users"
