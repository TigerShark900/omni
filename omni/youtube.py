"""Finds the YouTube video a browser is playing, from its title and channel.

Browsers only tell Omni the title, channel and a local picture. Looking the video up
gives YouTube's own thumbnail link (nothing to upload) and a "Watch on YouTube" button.
"""
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

CACHE_FILE = os.path.expanduser("~/.cache/omni/youtube.json")
_cache = None
_pending = set()
_lock = threading.Lock()


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"\s+", " ", s).strip()


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE) as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save():
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with _lock:
        items = dict(list(_cache.items())[-500:])  # keep the newest 500
    with open(CACHE_FILE + ".tmp", "w") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(CACHE_FILE + ".tmp", CACHE_FILE)


def _videos(data):
    out = []
    stack = [data]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                owner = "".join(r.get("text", "") for r in v.get("ownerText", {}).get("runs", []))
                out.append((v["videoId"], title, owner))
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return out


def _search(title, channel, key, log):
    found = False
    try:
        q = urllib.parse.quote(f"{title} {channel}".strip())
        req = urllib.request.Request(
            "https://www.youtube.com/results?search_query=" + q,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Accept-Language": "en", "Cookie": "SOCS=CAI"})
        html = urllib.request.urlopen(req, timeout=10).read().decode(errors="replace")
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
        if m:
            for vid, t, owner in _videos(json.loads(m.group(1))):
                if _norm(t) == _norm(title) and (not channel or _norm(owner) == _norm(channel)):
                    found = vid
                    break
        with _lock:
            _cache[key] = {"id": found, "at": int(time.time())}
        _save()
    except Exception as e:  # offline, YouTube changed its page, ...: try again later
        log("youtube lookup failed:", e)
    finally:
        with _lock:
            _pending.discard(key)


def lookup(title, channel, log=print):
    """dict with id/url/thumb if found, False if it's not a YouTube video, None while still looking."""
    if not title:
        return False
    cache = _load()
    key = _norm(title) + "\n" + _norm(channel)
    hit = cache.get(key)
    if hit is not None:
        vid = hit.get("id")
        if not vid:
            return False
        return {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}
    with _lock:
        if key not in _pending:
            _pending.add(key)
            threading.Thread(target=_search, args=(title, channel, key, log), daemon=True).start()
    return None
