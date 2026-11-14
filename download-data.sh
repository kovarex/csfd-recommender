#!/bin/sh
# On the PC (WSL): downloads the database export that NFS publishes weekly into ~/csfd-data over HTTPS, with no SSH
# and no password. The address is the one from the project notes, ending in /current/; it is remembered after the
# first run:
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
mkdir -p "$DATA"
cd "$DATA"
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
# Ratings files of an older export that this one no longer has (and the single file of an export over SSH).
for f in ratings*.csv.gz; do grep -q " $f\$" manifest.txt || rm -f "$f"; done
echo "Done: $DATA ($(du -sh . | cut -f1))"
