#!/bin/sh
# Cron entry point (NFS Scheduled Task, every hour, as "me"): updates the code from GitHub, deploys the web pages,
# refreshes film stats when they are older than 4 hours and genres (from Wikidata) weekly, then scrapes. NFS kills
# scheduled tasks after 15 minutes, so a run ends 11 minutes after the task started by default, counting the steps
# before the scraping (CSFD_MINUTES overrides it, e.g. when run as a daemon).
# Speed: 5 requests/s with 8 parallel downloads (CSFD_RATE, CSFD_WORKERS).
START=$(date +%s)
cd "$(dirname "$0")" || exit 1
mkdir -p "$HOME/logs"
LOG="$HOME/logs/csfd-crawl.log"
git pull -q --ff-only >> "$LOG" 2>&1 || echo "$(date) git pull failed, running the current code" >> "$LOG"
python3 -m pip install -q --user -r requirements.txt >> "$LOG" 2>&1
sh deploy-web.sh >> "$LOG" 2>&1
python3 -m csfd.crawl prioritize priority-users.txt --rescrape-hours 24 >> "$LOG" 2>&1
python3 -m csfd.crawl film-stats --max-age-hours 4 >> "$LOG" 2>&1
python3 -m csfd.crawl genres --max-age-hours 168 >> "$LOG" 2>&1
LEFT=$(( ${CSFD_MINUTES:-11} * 60 - $(date +%s) + START ))
if [ "$LEFT" -lt 60 ]; then
  echo "$(date) no time left for scraping after the preparation steps" >> "$LOG"
  exit 0
fi
exec python3 -m csfd.crawl run --minutes "$(( LEFT / 60 ))" --rate "${CSFD_RATE:-5}" --workers "${CSFD_WORKERS:-8}" >> "$LOG" 2>&1
