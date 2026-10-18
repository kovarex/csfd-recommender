#!/bin/sh
# Cron entry point (NFS Scheduled Task, every hour, as "me"): updates the code from GitHub, deploys the web pages,
# refreshes film stats when they are older than 6 hours, then scrapes. NFS kills scheduled tasks after 15 minutes,
# so a run scrapes for 12 minutes by default (CSFD_MINUTES overrides it, e.g. when run as a daemon).
cd "$(dirname "$0")" || exit 1
mkdir -p "$HOME/logs"
LOG="$HOME/logs/csfd-crawl.log"
git pull -q --ff-only >> "$LOG" 2>&1 || echo "$(date) git pull failed, running the current code" >> "$LOG"
python3 -m pip install -q --user -r requirements.txt >> "$LOG" 2>&1
sh deploy-web.sh >> "$LOG" 2>&1
python3 -m csfd.crawl film-stats --max-age-hours 6 >> "$LOG" 2>&1
exec python3 -m csfd.crawl run --minutes "${CSFD_MINUTES:-12}" >> "$LOG" 2>&1
