#!/bin/sh
# Copies the web pages into the site's document root and gives PHP (which runs as the "web" user)
# read access to the database login. Called by run-crawl.sh after every git pull.
cd "$(dirname "$0")" || exit 1
PUBLIC=${CSFD_PUBLIC:-/home/public}
PROTECTED=${CSFD_PROTECTED:-/home/protected}
CONFIG=${CSFD_CONFIG:-$HOME/csfd-config.ini}

cp -R web/. "$PUBLIC/"

WEB_CONFIG="$PROTECTED/csfd-web.ini"
if [ ! -f "$WEB_CONFIG" ] || [ "$CONFIG" -nt "$WEB_CONFIG" ]; then
  # Only the [mysql] and [web] sections; the scrape.do token stays private.
  python3 - "$CONFIG" "$WEB_CONFIG" <<'PY'
import configparser, sys
source = configparser.ConfigParser()
source.read(sys.argv[1], encoding="utf-8")
target = configparser.ConfigParser()
for section in ("mysql", "web"):
    if source.has_section(section):
        target[section] = dict(source[section])
with open(sys.argv[2], "w", encoding="utf-8") as f:
    target.write(f)
PY
  chgrp web "$WEB_CONFIG" 2>/dev/null
  chmod 640 "$WEB_CONFIG"
fi
