#!/bin/sh
# One-time setup on NearlyFreeSpeech. Run over SSH:
#   cd /home/private && git clone https://github.com/kovarex/csfd-recommender.git && sh csfd-recommender/setup-nfs.sh
set -e
cd "$(dirname "$0")"
CONFIG="$HOME/csfd-config.ini"

python3 -m pip install -q --user -r requirements.txt

if [ ! -f "$CONFIG" ]; then
  printf "scrape.do token: "; read -r TOKEN
  printf "MySQL host (e.g. csfd.db): "; read -r DB_HOST
  printf "MySQL user: "; read -r DB_USER
  stty -echo; printf "MySQL password: "; read -r DB_PASSWORD; stty echo; echo
  printf "MySQL database [csfd]: "; read -r DB_NAME
  umask 077
  cat > "$CONFIG" <<CONFIG_END
[scrape_do]
token = $TOKEN

[mysql]
host = $DB_HOST
user = $DB_USER
password = $DB_PASSWORD
database = ${DB_NAME:-csfd}

[crawl]
rate = 2
workers = 3
min_credits = 1000
max_missing_streak = 20000
CONFIG_END
  echo "Saved $CONFIG"
fi

python3 -m csfd.crawl add 63015
echo "Test run, 1 minute..."
python3 -m csfd.crawl run --minutes 1
python3 -m csfd.crawl stats
echo
echo "Done. Now add a Scheduled Task (every hour) with the command: $(pwd)/run-crawl.sh"
