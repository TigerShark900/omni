"""The online recipe library.

Omni downloads a small index (just each recipe's [match] rules) about once a day and checks it
on your PC. Only when something you're running matches does it download that one recipe, and
then keeps it, so the library never hears what you're doing - at most "someone fetched X once".

Recipes are plain TOML (no code), and library recipes get extra checks on top: they can't read
log files, and they must match something specific (see check_recipe).
"""
import hashlib
import json
import os
import re
import threading
import time
import tomllib
import urllib.parse
import urllib.request

# Bump INDEX_VERSION when index.json changes shape (old Omnis then keep their old index), and
# FEATURES when recipes get a new ability (recipes using it set `requires = <n>`, and older
# Omnis skip just those recipes).
INDEX_VERSION = 1
FEATURES = 1
DEFAULT_URL = "https://raw.githubusercontent.com/TigerShark900/omni/main/library/"
CACHE_DIR = os.path.expanduser("~/.local/share/omni/library")   # downloaded recipes (kept)
INDEX_FILE = os.path.join(CACHE_DIR, "index.json")
INDEX_MAX_AGE = 24 * 3600
RETRY_AFTER = 3600            # a failed download isn't retried for an hour
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ICON_OK = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")   # same as Discord picture names
PROBES = ("", "omni-probe-zzz", "firefox", "konsole", "code", "Untitled - Document")
MAX_SIZE = 64 * 1024
UA = {"User-Agent": "Omni (Discord status for KDE)"}


def check_recipe(data, icons=None):
    """Extra rules for library recipes. Returns a list of problems (empty = fine).
    ICONS (the names in the repo's icons/ folder) turns on the picture check, for pull requests."""
    problems = []
    if icons is not None:
        img = str((data.get("activity") or {}).get("large_image", "") or "")
        if not img:
            problems.append('needs a picture: set large_image to an icons/ name or an https:// link')
        elif img.startswith("http://"):
            problems.append("large_image must be an https:// link")
        elif not img.startswith("https://") and "{" not in img and img not in icons:
            problems.append(f'large_image "{img}" isn\'t in icons/ (add icons/{img}.png)')
    match = data.get("match")
    if not isinstance(match, dict) or not match:
        return ["no [match] section"]
    if data.get("log"):
        problems.append("[[log]] isn't allowed in library recipes (it reads files on your PC)")
    if not isinstance(data.get("requires", 1), int):
        problems.append("requires must be a whole number")
    elif data.get("requires", 1) > FEATURES:
        problems.append("needs a newer Omni")
    if data.get("client_id"):
        problems.append("client_id isn't allowed in library recipes")
    rxs = []
    for key, pattern in match.items():
        if not isinstance(pattern, str):
            problems.append(f"match.{key} must be text")
            continue
        try:
            rxs.append(re.compile(pattern, re.I))
        except re.error as e:
            problems.append(f"match.{key} is not a valid pattern: {e}")
    # every line must match for a recipe to apply, so the recipe is "too broad" only if
    # all of its lines would match everyday stuff
    if not problems and all(any(rx.search(p) for p in PROBES) for rx in rxs):
        problems.append("[match] is too broad (it would match ordinary apps)")
    return problems


def _fetch(url):
    if url.startswith("file://"):
        with open(url[7:], "rb") as f:
            return f.read(MAX_SIZE * 64)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read(MAX_SIZE * 64)


class Stub:
    """One index entry: enough to know whether a recipe is for you, without downloading it."""

    def __init__(self, name, entry):
        self.name = name
        self.sha256 = entry["sha256"]
        self.requires = int(entry.get("requires", 1))
        self.match = {k: re.compile(v, re.I) for k, v in entry["match"].items()}

    def matches(self, variables):
        return all(rx.search(variables.get(k, "") or "") for k, rx in self.match.items())


class Library:
    def __init__(self, log=print):
        self.log = log
        self.enabled = True
        self.url = DEFAULT_URL
        self.stubs = []
        self.icons = {}             # picture name -> fingerprint, for the icons/ folder in the repo
        self.icon_aliases = {}      # other name -> picture name ("vlc" -> "org_videolan_vlc")
        self.lock = threading.Lock()
        self.busy = set()           # names being downloaded right now
        self.failed = {}            # name -> time of the last failed try
        self.changed = False        # a new recipe arrived: the engine should reload
        self.outdated = False       # the library has recipes this Omni is too old for
        self.index_tried = 0
        self.checked = set()        # windows/songs already checked (so it isn't redone every tick)
        self._load_index()

    def configure(self, enabled, url):
        url = (url or DEFAULT_URL).strip()
        if not url.endswith("/"):
            url += "/"
        self.enabled, self.url = bool(enabled), url

    # ---------------------------------------------------------- index

    def _load_index(self):
        try:
            with open(INDEX_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if int(data.get("version", 1)) > INDEX_VERSION:
            return None
        stubs = []
        for name, entry in (data.get("recipes") or {}).items():
            if not NAME_OK.match(name):
                continue
            try:
                stub = Stub(name, entry)
                if stub.requires > FEATURES:
                    self.outdated = True
                    continue
                stubs.append(stub)
            except (KeyError, TypeError, AttributeError, re.error):
                continue
        self.stubs = stubs
        icons = data.get("icons") or {}
        self.icons = {k: str(v) for k, v in icons.items() if ICON_OK.match(k)} if isinstance(icons, dict) else {}
        aliases = data.get("icon_aliases") or {}
        self.icon_aliases = {k: v for k, v in aliases.items() if v in self.icons} if isinstance(aliases, dict) else {}
        return data

    def _index_age(self):
        try:
            return time.time() - os.path.getmtime(INDEX_FILE)
        except OSError:
            return float("inf")

    def maybe_refresh(self):
        """Fetch the index in the background if it's over a day old."""
        if not self.enabled or self._index_age() < INDEX_MAX_AGE:
            return
        if time.time() - self.index_tried < RETRY_AFTER:
            return
        self.index_tried = time.time()
        threading.Thread(target=self._refresh_index, daemon=True).start()

    def _refresh_index(self):
        try:
            raw = _fetch(self.url + "index.json")
            data = json.loads(raw)
            if not isinstance(data.get("recipes"), dict):
                raise ValueError("index has no recipes")
            if int(data.get("version", 1)) > INDEX_VERSION:
                self.outdated = True
                raise ValueError("the library needs a newer Omni (keeping the old index); update Omni")
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(INDEX_FILE + ".tmp", "wb") as f:
                f.write(raw)
            os.replace(INDEX_FILE + ".tmp", INDEX_FILE)
            with self.lock:
                self._load_index()
                self.checked.clear()
            self.log(f"library: index updated ({len(self.stubs)} recipes)")
        except Exception as e:
            self.log("library: couldn't update the index:", e)

    # ---------------------------------------------------------- pictures

    def icon_url(self, key):
        """A web link to the repo's picture called KEY, or None if the repo doesn't have it.
        Discord's servers download it, not your PC."""
        key = self.icon_aliases.get(key, key)
        if not self.enabled or key not in self.icons:
            return None
        url = urllib.parse.urljoin(self.url, f"../icons/{key}.png") + "?v=" + self.icons[key]
        return url if url.startswith("https://") else None  # Discord can't see files on your PC

    # ---------------------------------------------------------- recipes

    def path_for(self, name):
        return os.path.join(CACHE_DIR, name + ".toml")

    def _have(self, stub):
        """True if the newest version of this recipe is already downloaded."""
        try:
            with open(self.path_for(stub.name), "rb") as f:
                return hashlib.sha256(f.read()).hexdigest() == stub.sha256
        except OSError:
            return False

    def check(self, variables, local_names=()):
        """Called with what you're doing. Starts a download if a library recipe fits and
        you don't have it (or yours is outdated). Never blocks."""
        if not self.enabled or not self.stubs:
            return
        key = tuple(variables.get(k, "") for k in ("class", "process", "exe", "title", "steam_appid",
                                                   "player", "identity", "url"))
        if key in self.checked:
            return
        if len(self.checked) > 500:
            self.checked.clear()
        self.checked.add(key)
        now = time.time()
        with self.lock:
            stubs = list(self.stubs)
        for stub in stubs:
            if stub.name in local_names or stub.name in self.busy:
                continue
            if not stub.matches(variables) or self._have(stub):
                continue
            if now - self.failed.get(stub.name, 0) < RETRY_AFTER:
                self.checked.discard(key)  # check again later, once it's time to retry
                continue
            self.busy.add(stub.name)
            threading.Thread(target=self._download, args=(stub,), daemon=True).start()

    def _download(self, stub):
        try:
            raw = _fetch(self.url + stub.name + ".toml")
            if len(raw) > MAX_SIZE:
                raise ValueError("recipe is too big")
            if hashlib.sha256(raw).hexdigest() != stub.sha256:
                raise ValueError("recipe doesn't match the index (try again after the next index update)")
            problems = check_recipe(tomllib.loads(raw.decode()))
            if problems:
                raise ValueError("; ".join(problems))
            os.makedirs(CACHE_DIR, exist_ok=True)
            path = self.path_for(stub.name)
            with open(path + ".tmp", "wb") as f:
                f.write(raw)
            os.replace(path + ".tmp", path)
            self.changed = True
            self.log("library: got recipe", stub.name)
        except Exception as e:
            self.failed[stub.name] = time.time()
            self.log(f"library: couldn't get {stub.name}:", e)
        finally:
            self.busy.discard(stub.name)
