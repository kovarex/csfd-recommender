#!/bin/sh
# Cron entry point (NFS Scheduled Task, every hour, as "me"): updates the code from GitHub, deploys the web pages,
# refreshes film stats when they are older than 6 hours, then scrapes for 55 minutes.
cd "$(dirname "$0")" || exit 1
mkdir -p "$HOME/logs"
LOG="$HOME/logs/csfd-crawl.log"
git pull -q --ff-only >> "$LOG" 2>&1 || echo "$(date) git pull failed, running the current code" >> "$LOG"
python3 -m pip install -q --user -r requirements.txt >> "$LOG" 2>&1
sh deploy-web.sh >> "$LOG" 2>&1
python3 -m csfd.crawl film-stats --max-age-hours 6 >> "$LOG" 2>&1
exec python3 -m csfd.crawl run --minutes 55 >> "$LOG" 2>&1
