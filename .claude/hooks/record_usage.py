#!/usr/bin/env python3
"""Stop hook: records how many tokens this Claude Code session used.

Reads the session's local transcript (plus its subagents' transcripts), sums
the API usage of every request once, and writes the totals to
<USAGE_DIR>/sessions/<session id>.json. Then rebuilds <USAGE_DIR>/usage.md,
a table of all recorded sessions sorted by cost. No model is involved.

USAGE_DIR defaults to /mnt/project-files/usage; when that folder's parent does
not exist (a session outside the project), the hook does nothing.
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

USAGE_DIR = os.environ.get("USAGE_DIR", "/mnt/project-files/usage")

# List prices in USD per million tokens, derived from the costs Claude Code
# itself reports in these sessions. Cache reads and writes are priced separately.
PRICES = {
    "claude-opus-5-5": {"in": 4, "out": 20, "read": 0.2, "w5m": 5, "w1h": 8},
    "claude-fable-5-1": {"in": 10, "out": 50, "read": 0.25, "w5m": 12.5, "w1h": 20},
    "claude-haiku-5-5": {"in": 0.1, "out": 0.5, "read": 0.005, "w5m": 0.125, "w1h": 0.2},
}
WEB_SEARCH_USD = 0.01


def base_model(name):
    name = name.split("[")[0]
    for known in PRICES:
        if name.startswith(known):
            return known
    return name


def transcript_files(transcript_path):
    files = [transcript_path]
    session_dir = transcript_path[: -len(".jsonl")]
    files += sorted(glob.glob(os.path.join(session_dir, "**", "*.jsonl"), recursive=True))
    return [f for f in files if os.path.isfile(f)]


def read_usage(files):
    requests = {}  # message id -> (model, usage); one request spans several lines
    title = None
    first = last = None
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line and '"aiTitle"' not in line and '"customTitle"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") in ("ai-title", "custom-title"):
                    title = entry.get("customTitle") or entry.get("aiTitle") or title
                    continue
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message") or {}
                usage = message.get("usage")
                if not usage or message.get("model") == "<synthetic>":
                    continue
                key = message.get("id") or entry.get("requestId") or entry.get("uuid")
                requests[key] = (message.get("model", "?"), usage)
                stamp = entry.get("timestamp")
                if stamp:
                    first = min(first or stamp, stamp)
                    last = max(last or stamp, stamp)
    return requests, title, first, last


def summarize(requests):
    models = {}
    for model, usage in requests.values():
        m = models.setdefault(base_model(model), {
            "requests": 0, "input": 0, "output": 0, "cache_read": 0,
            "cache_write_5m": 0, "cache_write_1h": 0, "web_searches": 0})
        cache = usage.get("cache_creation") or {}
        write_1h = cache.get("ephemeral_1h_input_tokens", 0) or 0
        write_5m = cache.get("ephemeral_5m_input_tokens")
        if write_5m is None:
            write_5m = (usage.get("cache_creation_input_tokens", 0) or 0) - write_1h
        m["requests"] += 1
        m["input"] += usage.get("input_tokens", 0) or 0
        m["output"] += usage.get("output_tokens", 0) or 0
        m["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
        m["cache_write_5m"] += write_5m or 0
        m["cache_write_1h"] += write_1h
        m["web_searches"] += (usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
    total = 0.0
    unpriced = []
    for name, m in models.items():
        price = PRICES.get(name)
        if not price:
            m["cost_usd"] = None
            unpriced.append(name)
            continue
        m["cost_usd"] = round((
            m["input"] * price["in"] + m["output"] * price["out"]
            + m["cache_read"] * price["read"] + m["cache_write_5m"] * price["w5m"]
            + m["cache_write_1h"] * price["w1h"]) / 1e6 + m["web_searches"] * WEB_SEARCH_USD, 4)
        total += m["cost_usd"]
    return models, round(total, 4), unpriced


def tokens(m):
    return m["input"] + m["output"] + m["cache_read"] + m["cache_write_5m"] + m["cache_write_1h"]


def write_report():
    rows = []
    for path in glob.glob(os.path.join(USAGE_DIR, "sessions", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                rows.append(json.load(f))
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda r: r.get("cost_usd") or 0, reverse=True)
    total = sum(r.get("cost_usd") or 0 for r in rows)
    lines = [
        "# Token usage per thread",
        "",
        "Written by `.claude/hooks/record_usage.py` after every turn. Cost is the API list price,",
        "a measure of share, not what the subscription bills. Updated "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + ".",
        "",
        f"Total: **${total:,.2f}** across {len(rows)} sessions.",
        "",
        "| Thread | Session | Cost (USD) | Share | Output tokens | All tokens | Requests | First | Last |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in rows:
        models = r.get("models", {}).values()
        cost = r.get("cost_usd") or 0
        lines.append("| {} | {} | {:,.2f}{} | {:.1f} % | {:,} | {:,} | {:,} | {} | {} |".format(
            (r.get("title") or "?").replace("|", "/"), r["session_id"], cost,
            " *" if r.get("unpriced_models") else "", 100 * cost / total if total else 0,
            sum(m["output"] for m in models), sum(tokens(m) for m in models),
            sum(m["requests"] for m in models), (r.get("first") or "")[:16], (r.get("last") or "")[:16]))
    if any(r.get("unpriced_models") for r in rows):
        lines += ["", "\\* includes a model without a known price, counted as 0."]
    lines.append("")
    tmp = os.path.join(USAGE_DIR, ".usage.md.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, os.path.join(USAGE_DIR, "usage.md"))


def main():
    try:
        hook = json.load(sys.stdin)
    except ValueError:
        hook = {}
    transcript = hook.get("transcript_path") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not os.path.isdir(os.path.dirname(USAGE_DIR)):
        return
    os.makedirs(os.path.join(USAGE_DIR, "sessions"), exist_ok=True)
    if transcript and os.path.isfile(transcript):
        session_id = (os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
                      or hook.get("session_id") or os.path.basename(transcript)[: -len(".jsonl")])
        requests, title, first, last = read_usage(transcript_files(transcript))
        models, cost, unpriced = summarize(requests)
        record = {"session_id": session_id, "title": title, "first": first, "last": last,
                  "cost_usd": cost, "unpriced_models": unpriced, "models": models,
                  "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        out = os.path.join(USAGE_DIR, "sessions", session_id + ".json")
        old = None
        if os.path.isfile(out):
            try:
                with open(out, encoding="utf-8") as f:
                    old = json.load(f)
            except (OSError, ValueError):
                pass
        # A resumed session may start from a shorter local transcript; never record less than before.
        if not old or (old.get("cost_usd") or 0) <= cost:
            tmp = out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=1)
            os.replace(tmp, out)
    write_report()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # a usage report must never break a turn
        print(f"record_usage: {e}", file=sys.stderr)
