#!/bin/sh
# Cron entry point: scrapes for 55 minutes, then exits. Run it every hour.
cd "$(dirname "$0")" || exit 1
mkdir -p "$HOME/logs"
exec python3 -m csfd.crawl run --minutes 55 >> "$HOME/logs/csfd-crawl.log" 2>&1
