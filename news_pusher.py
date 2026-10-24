#!/usr/bin/env python3
"""RSS -> DingTalk headline pusher. Pure stdlib, runs on GitHub Actions or locally.

Webhook/secret are read from DD_WEBHOOK / DD_SECRET env vars, never from
config.json. Dedup state lives in state.json; only pushed items are marked
seen, so a failed push is retried on the next run.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "state.json")

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def fetch_feed(url, timeout, user_agent):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _text(parent, tag):
    el = parent.find(tag)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def parse_feed(xml_bytes):
    """Return list of (guid, title, link, pub_raw) from RSS 2.0 or Atom."""
    root = ET.fromstring(xml_bytes)
    items = []
    if root.tag == ATOM_NS + "feed":
        for entry in root.findall(ATOM_NS + "entry"):
            title = _text(entry, ATOM_NS + "title")
            link_el = entry.find(ATOM_NS + "link")
            link = link_el.get("href", "") if link_el is not None else ""
            guid = _text(entry, ATOM_NS + "id") or link or title
            pub = _text(entry, ATOM_NS + "updated") or _text(entry, ATOM_NS + "published")
            items.append((guid, title, link, pub))
    else:
        for item in root.findall(".//item"):
            title = _text(item, "title")
            link = _text(item, "link")
            guid = _text(item, "guid") or link or title
            items.append((guid, title, link, _text(item, "pubDate")))
    return items


def fetch_sina_roll(feed, timeout, user_agent):
    """Fetch 新浪财经滚动新闻 JSON API, return items in RSS-like tuples."""
    params = dict(feed.get("params", {}))
    url = feed["url"] + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = []
    for it in data.get("result", {}).get("data", []):
        link = it.get("url", "")
        title = it.get("title", "")
        ctime = it.get("ctime", 0)
        pub = ""
        if ctime:
            pub = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(int(ctime)))
        items.append((link or title, title, link, pub))
    return items


def translate_title(text, timeout, user_agent):
    """Translate via Google's free web endpoint; return original on failure."""
    url = (
        "https://translate.googleapis.com/translate_a/single"
        "?client=gtx&sl=auto&tl=zh-CN&dt=t&q=" + urllib.parse.quote(text)
    )
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
        return translated or text
    except Exception as e:
        print(f"translate failed for '{text[:40]}': {e}")
        return text


def parse_time(raw):
    if not raw:
        return 0.0
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def build_signed_url(webhook, secret):
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={timestamp}&sign={sign}"


def push_to_dingtalk(webhook, secret, text, dry_run):
    payload = json.dumps(
        {"msgtype": "markdown", "markdown": {"title": "新闻头条", "text": text}}
    ).encode("utf-8")
    if dry_run:
        print(text)
        return True
    url = build_signed_url(webhook, secret)
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"push failed: {e}", file=sys.stderr)
        return False
    if result.get("errcode") != 0:
        print(f"dingtalk error: {result}", file=sys.stderr)
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true", help="print the message instead of pushing"
    )
    args = parser.parse_args()

    config = load_config()
    webhook = os.environ.get("DD_WEBHOOK", "")
    secret = os.environ.get("DD_SECRET", "")
    if not args.dry_run and (not webhook or not secret):
        print("DD_WEBHOOK / DD_SECRET env vars not set", file=sys.stderr)
        return 1

    state = load_state()
    seen = state.setdefault("seen", {})
    max_new = int(config.get("max_new_per_run", 20))
    max_seen = int(config.get("max_seen_per_feed", 200))
    timeout = int(config.get("request_timeout", 15))
    user_agent = config.get("user_agent", "news-pusher/1.0")

    new_items = []
    for feed in config["feeds"]:
        name, url = feed["name"], feed["url"]
        try:
            if feed.get("type") == "sina_roll":
                items = fetch_sina_roll(feed, timeout, user_agent)
            else:
                items = parse_feed(fetch_feed(url, timeout, user_agent))
        except Exception as e:
            print(f"[skip] {name}: {e}")
            continue
        seen_guids = set(seen.get(url, []))
        for guid, title, link, pub in items:
            if guid and guid not in seen_guids:
                new_items.append(
                    (parse_time(pub), url, name, guid, title, link,
                     feed.get("translate", False))
                )
        print(f"[ok] {name}: {len(items)} items, "
              f"{sum(1 for g, *_ in items if g and g not in seen_guids)} new")

    new_items.sort(key=lambda x: x[0], reverse=True)
    batch = new_items[:max_new]

    if not batch:
        print("no new headlines")
        return 0

    lines = [f"### 新闻头条 {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for _, _, name, _, title, link, do_translate in batch:
        show = translate_title(title, timeout, user_agent) if do_translate else title
        lines.append(f"- **{name}** [{show}]({link})" if link else f"- **{name}** {show}")
    text = "\n".join(lines)

    if not push_to_dingtalk(webhook, secret, text, args.dry_run):
        return 1

    if not args.dry_run:
        for _, url, _, guid, _, _, _ in batch:
            lst = seen.setdefault(url, [])
            if guid not in lst:
                lst.append(guid)
        for url in seen:
            if len(seen[url]) > max_seen:
                seen[url] = seen[url][-max_seen:]
        save_state(state)
        print(f"pushed {len(batch)} new headline(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
