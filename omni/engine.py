"""Turns 'what you're doing' into a Discord status, using recipes and your privacy rules."""
import glob
import json
import os
import re
import time
import tomllib

from . import library, youtube
from .sources import has_own_presence

TYPES = {"playing": 0, "listening": 2, "watching": 3, "competing": 5}
SHOW = {"name": 0, "state": 1, "details": 2}
BROWSERS = r"brave|chrom|firefox|vivaldi|zen|edge|opera|librewolf|floorp"
MEDIA_KEYS = {"player", "identity", "artist", "album", "url"}
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.expanduser("~/.config/omni")
RULES_FILE = os.path.join(CONFIG_DIR, "rules.json")          # written by the settings window
SEEN_FILE = os.path.expanduser("~/.local/share/omni/seen.json")  # apps Omni has noticed

# Omni's own Discord app. App ids are public (they only pick the name and pictures on the status
# card), so everyone can share it. Set your own client_id to use your own pictures.
SHARED_CLIENT_ID = "1569111937680154877"

DEFAULT_CONFIG = {
    "client_id": "",
    "show": "details",        # what the member list shows: name / details / state
    "fallback": False,        # show "Using <app>" for apps without a recipe
    "media": False,           # show music/video from media players
    "media_priority": 40,
    "youtube_lookup": True,   # find YouTube videos playing in the browser (thumbnail + button)
    "cover_art": True,        # upload local song/video covers (one shared file on the server)
    "window_icons": True,     # use the app's own icon when there's no other picture
    "respect_native": True,   # skip apps that already set their own Discord status
    "library": True,          # get recipes for new games from the online library
    "library_url": "",        # where the library lives (empty = Omni's GitHub)
    "privacy": {
        "blocklist": ["keepassxc", "kwalletmanager", "bitwarden", "1password", "seahorse"],  # apps that are never shown (regex, matched against class/process/name)
        "media_blocklist": [], # media players that are never shown
        "ignore_focus": [],    # extra apps that don't count as "switching away"
    },
    "apps": {},                # per-app overrides: [apps.blender] detail = "vague"
}

# Focusing these doesn't change your status (checking Discord shouldn't say "Using Discord")
ALWAYS_IGNORED = [r"^discord$", r"^vesktop$", r"plasmashell", r"^krunner$", r"kwin", r"^$",
                  r"xdg-desktop-portal", r"polkit", r"ksmserver", r"^omni$", r"spectacle"]


def load_config():
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    path = os.path.join(CONFIG_DIR, "config.toml")
    if os.path.exists(path):
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    rules = load_rules()
    for k, v in rules.get("settings", {}).items():
        cfg[k] = v
    cfg["client_id"] = str(cfg.get("client_id") or "").strip()
    cfg["shared_app"] = not cfg["client_id"]
    if cfg["shared_app"]:
        cfg["client_id"] = SHARED_CLIENT_ID
    for k, detail in rules.get("apps", {}).items():
        cfg["apps"][k] = {**cfg["apps"].get(k, {}), "detail": detail}
    return cfg


def load_rules():
    try:
        with open(RULES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_rules(rules):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = RULES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RULES_FILE)


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Recipe:
    def __init__(self, data, path):
        self.path = path
        self.name = data.get("name") or os.path.basename(path)[:-5]
        self.priority = int(data.get("priority", 50))
        self.client_id = str(data.get("client_id", "") or "")
        self.match = {k: re.compile(v, re.I) for k, v in (data.get("match") or {}).items()}
        self.activity = data.get("activity") or {}
        self.logs = data.get("log") or []
        self.buttons = data.get("buttons") or []
        self.is_media = bool(MEDIA_KEYS & set(self.match))
        self.default = data.get("default", "full")   # "hidden" = off until you switch it on
        if not self.match:
            raise ValueError("recipe has no [match] section")

    def try_match(self, variables):
        found = {}
        for key, rx in self.match.items():
            m = rx.search(variables.get(key, "") or "")
            if not m:
                return None
            found.update({k: v for k, v in m.groupdict().items() if v is not None})
        for log in self.logs:
            found.update(_read_log(log))
        return found


def _read_log(log):
    files = glob.glob(os.path.expanduser(log.get("file", "")))
    if not files:
        return {}
    newest = max(files, key=os.path.getmtime)
    try:
        with open(newest, "rb") as f:
            f.seek(max(0, os.path.getsize(newest) - 256 * 1024))
            text = f.read().decode(errors="replace")
    except OSError:
        return {}
    last = None
    for last in re.finditer(log.get("pattern", "$^"), text, re.M):
        pass
    return {k: v for k, v in last.groupdict().items() if v is not None} if last else {}


LOCAL_DIRS = (os.path.join(HERE, "recipes"), os.path.join(CONFIG_DIR, "recipes"))


def local_recipe_names():
    """File names (without .toml) of the bundled recipes and your own."""
    return {os.path.basename(p)[:-5] for d in LOCAL_DIRS for p in glob.glob(os.path.join(d, "*.toml"))}


def load_recipes(use_library=True):
    recipes, errors = {}, []
    # downloaded library recipes first, then bundled, then your own (~/.config/omni/recipes):
    # a later one with the same file name replaces the earlier one
    dirs = ((library.CACHE_DIR,) if use_library else ()) + LOCAL_DIRS
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.toml"))):
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                if d == library.CACHE_DIR:
                    problems = library.check_recipe(data)
                    if problems:
                        raise ValueError("; ".join(problems))
                recipes[os.path.basename(path)] = Recipe(data, path)
            except Exception as e:
                errors.append(f"{path}: {e}")
    return sorted(recipes.values(), key=lambda r: -r.priority), errors


def fill(template, variables):
    if not isinstance(template, str):
        return template
    return re.sub(r"\{(\w+)\}", lambda m: str(variables.get(m.group(1), "") or ""), template).strip()


def _text(s):
    """Discord wants 2-128 characters."""
    s = (s or "").strip()
    if not s:
        return None
    if len(s) == 1:
        s += " "
    return s[:128]


def _image(s):
    s = (s or "").strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s[:300]
    return s[:300] if s and "/" not in s else None  # an asset key uploaded to your Discord app


class Candidate:
    def __init__(self, key, priority, source, fields, variables, client_id="", start=None, end=None):
        self.key, self.priority, self.source = key, priority, source
        self.fields, self.variables, self.client_id = fields, variables, client_id
        self.start, self.end = start, end


class Engine:
    def __init__(self, focus, media, log=print):
        self.focus, self.media = focus, media
        self.library = library.Library(log)
        self.paused = False
        self.starts = {}        # key -> (start time, last seen)
        self.sticky = None      # the last real app you used (survives focusing Discord/desktop)
        self.icons = None       # IconHost, set by the app
        self.seen = load_seen()
        self.seen_dirty = 0
        self.reload()

    def reload(self):
        self.config = load_config()
        self.library.configure(self.config.get("library", True), self.config.get("library_url", ""))
        self.recipes, self.errors = load_recipes(self.library.enabled)
        self.local_names = local_recipe_names()
        priv = self.config["privacy"]
        self.ignore = [re.compile(p, re.I) for p in ALWAYS_IGNORED + list(priv.get("ignore_focus", []))]
        self.block = [re.compile(p, re.I) for p in priv.get("blocklist", [])]
        self.media_block = list(priv.get("media_blocklist", []))
        self.apps = {k.lower(): v for k, v in (self.config.get("apps") or {}).items()}
        if self.icons:
            self.icons.configure(self.config.get("icons"), self.config["client_id"], self.config["shared_app"])

    # ---------------------------------------------------------- focus

    def _focused_vars(self):
        v = self.focus.variables()
        ignored = any(rx.search(v["class"]) or rx.search(v["process"]) for rx in self.ignore)
        if not ignored:
            self.sticky = v
        elif self.sticky and self.sticky.get("pid") and not os.path.exists(f"/proc/{self.sticky['pid']}"):
            self.sticky = None  # the app you were using has closed
        return self.sticky

    def _app_rule(self, v, recipe_name=""):
        keys = [v.get("class", ""), v.get("process", ""), v.get("exe", ""), v.get("app", ""),
                v.get("steam_name", ""), recipe_name]
        for k in keys:
            if k and k.lower() in self.apps:
                return self.apps[k.lower()]
        return {}

    def _blocked(self, v):
        keys = [v.get(k, "") for k in ("class", "process", "exe", "app", "steam_name", "identity", "player")]
        return any(rx.search(k) for rx in self.block for k in keys if k)

    def _remember(self, key, name, kind, icon_vars):
        now = int(time.time())
        old = self.seen.get(key)
        if old and now - old.get("last", 0) < 300 and old.get("name") == name:
            return
        self.seen[key] = {"name": name, "kind": kind, "last": now,
                          "desktop": icon_vars.get("desktop", ""), "class": icon_vars.get("class", ""),
                          "process": icon_vars.get("process", ""), "steam_appid": icon_vars.get("steam_appid", "")}
        self.seen_dirty = now

    def save_seen(self):
        if not self.seen_dirty:
            return
        os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
        with open(SEEN_FILE + ".tmp", "w") as f:
            json.dump(self.seen, f, indent=1, ensure_ascii=False)
        os.replace(SEEN_FILE + ".tmp", SEEN_FILE)
        self.seen_dirty = 0

    def _window_candidate(self):
        v = self._focused_vars()
        if v and (v.get("class") or v.get("process")):
            self._remember((v.get("class") or v.get("process")).lower(), v.get("app", ""),
                           "game" if v.get("steam_appid") else "app", v)
        if not v:
            return None
        explicit = self._app_rule(v).get("detail")
        if self._blocked(v) and explicit not in ("full", "vague"):
            return None
        if self.config.get("respect_native", True) and has_own_presence(int(v.get("pid") or 0)):
            return None  # the app sets its own Discord status - stay out of the way
        self.library.check(v, self.local_names)
        for r in self.recipes:
            if r.is_media:
                continue
            found = r.try_match(v)
            if found is not None:
                allv = {**v, **found}
                self._note_default(v, r.default)
                return self._apply_rule(Candidate(f"recipe:{r.name}", r.priority, f"recipe {r.name}",
                                                  dict(r.activity, buttons=r.buttons), allv, r.client_id),
                                        v, r.name, r.default)
        default = "full" if self.config.get("fallback", False) else "hidden"
        self._note_default(v, default)
        key = f"app:{v['class'] or v['process']}"
        return self._apply_rule(Candidate(key, 0, "fallback", {"type": "playing", "details": "Using {app}"}, v),
                                v, "", default)

    def _note_default(self, v, default):
        """Remember what this app shows when you haven't picked anything (for the settings window)."""
        item = self.seen.get((v.get("class") or v.get("process") or "").lower())
        if item is not None and item.get("default") != default:
            item["default"] = default
            self.seen_dirty = int(time.time())

    def _apply_rule(self, c, v, recipe_name="", default="full"):
        rule = self._app_rule(v, recipe_name)
        detail = rule.get("detail", default)
        if detail == "hidden":
            return None
        if detail == "vague":
            keep = {k: c.fields[k] for k in ("type", "large_image", "show") if k in c.fields}
            c.fields = {**keep, "details": "Using {app}"}
        if "priority" in rule:
            c.priority = int(rule["priority"])
        return c

    # ---------------------------------------------------------- media

    def _media_candidate(self):
        m = self.media.current(skip=self.media_block)
        if not m or self._blocked(m):
            return None
        v = dict(m)
        v["app"] = m["identity"]
        self.library.check(v, self.local_names)
        self._remember(m["identity"].lower(), m["identity"], "media", {"desktop": m.get("desktop", ""), "process": m["player"]})
        prio = int(self.config.get("media_priority", 40))
        fields = None
        client_id = ""
        for r in self.recipes:
            if not r.is_media:
                continue
            found = r.try_match(v)
            if found is not None:
                v.update(found)
                fields, prio, client_id = dict(r.activity, buttons=r.buttons), r.priority, r.client_id
                break
        if fields is None:
            browser = bool(re.search(BROWSERS, m["player"] + " " + m["identity"], re.I))
            video = re.search(r"mpv|vlc|celluloid|haruna|totem|smplayer|kodi|jellyfin", m["player"] + m["identity"], re.I) \
                or re.search(r"youtube\.com|youtu\.be|twitch\.tv", m["url"]) or (browser and not m["album"])
            music = re.search(r"spotify|rhythmbox|elisa|amarok|strawberry|clementine|lollypop|cider|tidal|deezer|audacious|cmus|mpd",
                              m["player"] + " " + m["identity"], re.I)
            fields = {
                "type": "listening" if music else "watching" if video or not m["artist"] else "listening",
                "details": "{title}",
                "state": "by {artist}" if m["artist"] else "on {identity}",
                "large_image": "{art_url}",
                "large_text": "{album}" if m["album"] else "{identity}",
            }
            if browser and not m["album"] and self.config.get("youtube_lookup", True):
                yt = youtube.lookup(m["title"], m["artist"])
                if yt:
                    v["art_url"] = yt["thumb"]
                    v["yt_url"] = yt["url"]
                    fields["large_text"] = "YouTube"
                    fields["buttons"] = [{"label": "Watch on YouTube", "url": "{yt_url}"}]
                elif yt is None:
                    v["art_url"] = ""  # still looking it up: don't upload the local cover meanwhile
        start = end = None
        if m["position_us"] >= 0:
            start = time.time() - m["position_us"] / 1e6
            if m["length_us"] > 0:
                end = start + m["length_us"] / 1e6
        c = Candidate(f"media:{m['player']}:{m['title']}", prio, f"media {m['identity']}", fields, v, client_id, start, end)
        rule = self._app_rule({"app": m["identity"], "process": m["player"]})
        default = "full" if self.config.get("media", False) else "hidden"
        item = self.seen.get(m["identity"].lower())
        if item is not None and item.get("default") != default:
            item["default"] = default
            self.seen_dirty = int(time.time())
        detail = rule.get("detail", default)
        if detail == "hidden":
            return None
        if detail == "vague":
            kind = fields.get("type", "listening")
            verb = "Watching" if kind == "watching" else "Listening"
            c.fields = {"type": kind, "details": verb + " on {identity}"}
        return c

    # ---------------------------------------------------------- result

    def tick(self):
        """Returns (activity dict or None, client_id, info for the preview)."""
        now = time.time()
        self.library.maybe_refresh()
        if self.library.changed:  # a recipe just downloaded: use it straight away
            self.library.changed = False
            self.recipes, self.errors = load_recipes(self.library.enabled)
        cands = [c for c in (self._window_candidate(), self._media_candidate()) if c]
        default_id = str(self.config.get("client_id", ""))
        if self.paused:
            return None, default_id, {"source": "paused"}
        if not cands:
            return None, default_id, {"source": "nothing"}
        c = max(cands, key=lambda c: c.priority)

        start = c.start
        if start is None:
            first, _ = self.starts.get(c.key, (now, now))
            self.starts[c.key] = (first, now)
            start = first
        # forget sessions you haven't come back to in 10 minutes
        self.starts = {k: s for k, s in self.starts.items() if now - s[1] < 600}

        f = c.fields
        v = c.variables
        act = {"type": TYPES.get(str(f.get("type", "playing")).lower(), 0)}
        details, state = _text(fill(f.get("details"), v)), _text(fill(f.get("state"), v))
        if details:
            act["details"] = details
        if state:
            act["state"] = state
        act["status_display_type"] = SHOW.get(str(f.get("show", self.config.get("show", "details"))).lower(), 2)
        if act["status_display_type"] == 2 and not details:
            act["status_display_type"] = 0
        ts = {"start": int(start) * 1000}
        if c.end:
            ts["end"] = int(c.end) * 1000
        act["timestamps"] = ts
        assets = {}
        for k in ("large_image", "small_image"):
            img = _image(fill(f.get(k), v))
            if img and self.icons and not img.startswith("http"):
                img = self.icons.resolve(img)
            if img:
                assets[k] = img
        for k in ("large_text", "small_text"):
            t = _text(fill(f.get(k), v))
            if t:
                assets[k] = t
        art = v.get("art_url", "")
        if "large_image" not in assets and self.icons and art.startswith("file://") and self.config.get("cover_art", True):
            from urllib.parse import unquote
            url = self.icons.url_for_file(unquote(art[7:]))
            if url:
                assets["large_image"] = url
        if "large_image" not in assets and self.icons and self.config.get("window_icons", True):
            url = self.icons.url_for(v)
            if url:
                assets["large_image"] = url
                assets.setdefault("large_text", _text(v.get("app")) or "")
        if "large_image" not in assets and self.config.get("default_image"):
            assets["large_image"] = self.config["default_image"]
        if "large_image" not in assets and self.icons and self.icons.default():
            assets["large_image"] = self.icons.default()  # the blue dot
        if assets:
            act["assets"] = assets
        buttons = []
        for b in f.get("buttons") or []:
            label, url = _text(fill(b.get("label"), v)), fill(b.get("url"), v)
            if label and url.startswith("http"):
                buttons.append({"label": label[:32], "url": url[:512]})
        if buttons:
            act["buttons"] = buttons[:2]
        return act, c.client_id or default_id, {"source": c.source, "variables": v}
