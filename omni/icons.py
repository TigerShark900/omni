"""Turns a window's app icon into a web link Discord can show.

Discord only shows pictures from the web, so each icon is saved as a PNG once,
uploaded with the command from [icons] in config.toml, and then linked from there.
"""
import hashlib
import json
import os
import subprocess
import threading
import time

from .sources import desktop_entry

CACHE = os.path.expanduser("~/.cache/omni/icons")
SIZE = 256


def _image_file_png(path):
    """Shrinks a local picture (like album art) to at most 512px and returns PNG bytes."""
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PyQt6.QtGui import QImage
    except ImportError:
        return None
    img = QImage(path)
    if img.isNull():
        return None
    if max(img.width(), img.height()) > 512:
        img = img.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def _render_png(icon_name):
    """Draws the icon from your icon theme into PNG bytes (needs the Qt app to be running)."""
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt6.QtGui import QGuiApplication, QIcon
    except ImportError:
        return None
    if QGuiApplication.instance() is None:
        return None
    icon = QIcon(icon_name) if os.path.isabs(icon_name) else QIcon.fromTheme(icon_name)
    if icon.isNull():
        return None
    pm = icon.pixmap(SIZE, SIZE)
    if pm.isNull():
        return None
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(data)


class IconHost:
    def __init__(self, log=print):
        self.log = log
        self.config = {}
        self.by_app = {}        # icon name -> file name (or None if there's no icon)
        self.failed = {}        # file name -> when the upload last failed
        self.busy = set()
        self.cover_live = None     # which cover is on the server right now
        self.cover_wanted = None
        self.cover_busy = False
        os.makedirs(CACHE, exist_ok=True)
        self.state_file = os.path.join(CACHE, "uploaded.json")
        try:
            with open(self.state_file) as f:
                self.uploaded = set(json.load(f))
        except (OSError, ValueError):
            self.uploaded = set()

    def configure(self, cfg):
        self.config = cfg or {}

    @property
    def ready(self):
        return bool(self.config.get("enabled", True) and self.config.get("base_url") and self.config.get("upload"))

    def icon_name(self, v):
        entry = desktop_entry(v.get("desktop", ""), v.get("class", ""), v.get("process", ""),
                              v.get("player", "").split(".")[0], v.get("app", ""))
        if entry and entry[1]:
            return entry[1]
        return v.get("desktop") or v.get("class") or ""

    def url_for(self, v):
        """A web link to this app's icon, or None (also None while it's still uploading)."""
        if not self.ready:
            return None
        name = self.icon_name(v)
        if not name:
            return None
        if name not in self.by_app:
            png = _render_png(name)
            if not png:
                self.by_app[name] = None
            else:
                fname = hashlib.sha1(png).hexdigest()[:16] + ".png"
                path = os.path.join(CACHE, fname)
                if not os.path.exists(path):
                    with open(path, "wb") as f:
                        f.write(png)
                self.by_app[name] = fname
        return self._link(self.by_app[name])

    def url_for_file(self, path):
        """A web link for a local picture such as a song's cover art.

        Covers all share ONE file on the server (cover.png, replaced each time), so your
        listening/watch history never piles up there. The ?v= tag makes Discord notice the change.
        """
        if not self.ready or not os.path.isfile(path):
            return None
        key = ("file", path, os.path.getmtime(path))
        if key not in self.by_app:
            png = _image_file_png(path)
            self.by_app[key] = (hashlib.sha1(png).hexdigest()[:16], png) if png else None
        item = self.by_app[key]
        if not item:
            return None
        digest, png = item
        if self.cover_live == digest:
            return self.config["base_url"].rstrip("/") + f"/cover.png?v={digest}"
        if self.cover_wanted != digest and not self.cover_busy:
            self.cover_wanted = digest
            self.cover_busy = True
            threading.Thread(target=self._upload_cover, args=(digest, png), daemon=True).start()
        return None

    def _upload_cover(self, digest, png):
        folder = os.path.join(CACHE, "cover")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "cover.png")
        try:
            with open(path, "wb") as f:
                f.write(png)
            cmd = [part.replace("{file}", path).replace("{name}", "cover.png") for part in self.config["upload"]]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or f"exit {r.returncode}")
            self.cover_live = digest
        except Exception as e:
            self.log("cover upload failed:", e)
        finally:
            self.cover_busy = False
            self.cover_wanted = None

    def default(self):
        """The blue dot, uploaded once like any other icon."""
        if not self.ready:
            return None
        if "omni-dot" not in self.by_app:
            png = _render_dot_png()
            fname = None
            if png:
                fname = "omni-" + hashlib.sha1(png).hexdigest()[:10] + ".png"
                path = os.path.join(CACHE, fname)
                if not os.path.exists(path):
                    with open(path, "wb") as f:
                        f.write(png)
            self.by_app["omni-dot"] = fname
        return self._link(self.by_app["omni-dot"])

    def _link(self, fname):
        if not fname:
            return None
        if fname in self.uploaded:
            return self.config["base_url"].rstrip("/") + "/" + fname
        if fname not in self.busy and time.time() - self.failed.get(fname, 0) > 300:
            self.busy.add(fname)
            threading.Thread(target=self._upload, args=(fname,), daemon=True).start()
        return None

    def _upload(self, fname):
        path = os.path.join(CACHE, fname)
        cmd = [part.replace("{file}", path).replace("{name}", fname) for part in self.config["upload"]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or f"exit {r.returncode}")
            self.uploaded.add(fname)
            with open(self.state_file, "w") as f:
                json.dump(sorted(self.uploaded), f)
            self.log("uploaded icon", fname)
        except Exception as e:
            self.failed[fname] = time.time()
            self.log("icon upload failed:", e)
        finally:
            self.busy.discard(fname)


# ---------------------------------------------------------------- no server: your Discord app's Art Assets

ASSET_DIR = os.path.expanduser("~/Pictures/Omni icons")
ASSET_SIZE = 512          # the smallest picture Discord accepts for Art Assets
DEFAULT_KEY = "omni"      # the blue dot, used when there's no other picture


def icon_key(name):
    """Picture name for the repo's icons/ folder: lowercase letters, digits and _."""
    key = "".join(ch if ch.isascii() and ch.isalnum() else "_" for ch in name.lower()).strip("_")
    return "_".join(p for p in key.split("_") if p)[:64] or "app"


def asset_key(name):
    """Discord picture names: like icon_key, but kept short."""
    key = icon_key(name)
    if len(key) > 28:
        key = key[:21] + "_" + hashlib.sha1(name.encode()).hexdigest()[:6]
    return key


def _render_asset_png(icon_name):
    """The icon centred on a 512px transparent square, saved as a small PNG."""
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PyQt6.QtGui import QGuiApplication, QIcon, QImage, QPainter
    except ImportError:
        return None
    if QGuiApplication.instance() is None:
        return None
    icon = QIcon(icon_name) if os.path.isabs(icon_name) else QIcon.fromTheme(icon_name)
    if icon.isNull():
        return None
    pm = icon.pixmap(ASSET_SIZE, ASSET_SIZE)
    if pm.isNull():
        return None
    img = QImage(ASSET_SIZE, ASSET_SIZE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scaled = pm.scaled(ASSET_SIZE, ASSET_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap((ASSET_SIZE - scaled.width()) // 2, (ASSET_SIZE - scaled.height()) // 2, scaled)
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG", 0)  # 0 = squeeze the file as small as it goes
    return bytes(data)


def _render_dot_png():
    """The Omni blue dot, same as the tray icon."""
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter
    except ImportError:
        return None
    if QGuiApplication.instance() is None:
        return None
    n = ASSET_SIZE
    img = QImage(n, n, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    s = n / 64
    p.setBrush(QColor("#5865F2"))
    p.drawEllipse(round(6 * s), round(6 * s), round(52 * s), round(52 * s))
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(round(22 * s), round(22 * s), round(20 * s), round(20 * s))
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG", 0)
    return bytes(data)


class AssetFolder:
    """Icons go into a folder on your PC; you drag them into your Discord app's Art Assets.

    Omni checks which pictures the Discord app already has and only uses those,
    and keeps only the not-yet-uploaded icons in the folder.
    """

    def __init__(self, log=print):
        self.log = log
        self.client_id = ""
        self.known = set()        # picture names your Discord app has
        self.checked_at = 0
        self.checking = False
        self.by_app = {}          # icon name -> asset key (or None)
        self.done_dir = os.path.join(CACHE, "uploaded")
        self.shared = False       # on Omni's shared app you can't upload, so no folder
        self.web = None           # picture name -> link to the repo's icons/ folder (or None)

    def configure(self, cfg, client_id="", shared=False):
        self.shared = shared
        if client_id != self.client_id:
            self.client_id = client_id
            self.known = set()
            self.checked_at = 0
        if not (self.web and self.web(DEFAULT_KEY)):
            self._ensure(DEFAULT_KEY, _render_dot_png)
        self.refresh()

    # what's in your Discord app
    def refresh(self, force=False):
        if not self.client_id or self.checking:
            return
        if not force and time.time() - self.checked_at < 300:
            return
        self.checking = True
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        import urllib.request
        try:
            req = urllib.request.Request(
                f"https://discord.com/api/v10/oauth2/applications/{self.client_id}/assets?nocache=true",  # skip the 10-min cached copy
                headers={"User-Agent": "Omni (Discord status app)"})
            items = json.load(urllib.request.urlopen(req, timeout=10))
            self.known = {str(a.get("name", "")).lower() for a in items}
            self._tidy()
        except Exception as e:
            self.log("couldn't check your Discord app's pictures:", e)
        finally:
            self.checked_at = time.time()
            self.checking = False

    def _tidy(self):
        """Move icons Discord already has out of the to-upload folder."""
        if not os.path.isdir(ASSET_DIR):
            return
        os.makedirs(self.done_dir, exist_ok=True)
        for f in os.listdir(ASSET_DIR):
            if f.endswith(".png") and f[:-4] in self.known:
                os.replace(os.path.join(ASSET_DIR, f), os.path.join(self.done_dir, f))

    def pending(self):
        if self.shared:
            return []
        try:
            return sorted(f for f in os.listdir(ASSET_DIR) if f.endswith(".png") and f[:-4] not in self.known)
        except OSError:
            return []

    def _ensure(self, key, render):
        """Puts KEY.png in the folder if your Discord app doesn't have it yet."""
        path = os.path.join(ASSET_DIR, key + ".png")
        if self.shared:
            return  # you can't upload to the shared app; whatever it already has is still used
        if key in self.known or os.path.exists(path) or os.path.exists(os.path.join(self.done_dir, key + ".png")):
            return
        png = render()
        if png:
            os.makedirs(ASSET_DIR, exist_ok=True)
            with open(path, "wb") as f:
                f.write(png)

    # used by the engine
    def url_for(self, v):
        self.refresh()
        name = IconHost.icon_name(self, v)
        if not name:
            return None
        # the same app can be called "spotify" (its player) or "com.spotify.client" (its window),
        # so use whichever of its names the Discord app has a picture for
        keys = []
        for n in (v.get("class"), v.get("desktop"), v.get("process"), v.get("player"), v.get("app"), name):
            if n:
                k = asset_key(n)
                if k not in keys:
                    keys.append(k)
        for k in keys:
            if k in self.known:
                return k
        for n in (v.get("class"), v.get("desktop"), v.get("process"), v.get("player"), v.get("app"), name):
            url = self.web(icon_key(n)) if self.web and n else None
            if url:
                return url
        if name not in self.by_app:  # none yet: put one in the folder to upload, once per icon
            self._ensure(keys[0], lambda: _render_asset_png(name))
            self.by_app[name] = keys[0]
        return None

    def url_for_file(self, path):
        return None  # song covers change all the time, so they can't be Art Assets

    def default(self):
        if DEFAULT_KEY in self.known:
            return DEFAULT_KEY
        return self.web(DEFAULT_KEY) if self.web else None


class Icons:
    """Picks how icons reach Discord: "discord" (folder + Art Assets, default) or "upload" (your own server)."""

    def __init__(self, log=print):
        self.log = log
        self.backend = None
        self.mode = None
        self.web = None           # set by the app: the recipe library's icon links

    def configure(self, cfg, client_id="", shared=False):
        cfg = cfg or {}
        mode = cfg.get("mode") or ("upload" if cfg.get("upload") and cfg.get("base_url") else "discord")
        if mode != self.mode:
            self.mode = mode
            self.backend = IconHost(self.log) if mode == "upload" else AssetFolder(self.log)
        if mode == "upload":
            self.backend.configure(cfg)
        else:
            self.backend.web = self.web
            self.backend.configure(cfg, client_id, shared)

    def url_for(self, v):
        return self.backend.url_for(v) if self.backend else None

    def resolve(self, key):
        """A picture name from a recipe: your Discord app's picture if it has one,
        else the repo's icons/ copy, else the name as it is."""
        known = getattr(self.backend, "known", set())
        if key in known:
            return key
        return (self.web(key) if self.web else None) or key

    def url_for_file(self, path):
        return self.backend.url_for_file(path) if self.backend else None

    def default(self):
        return self.backend.default() if self.backend and hasattr(self.backend, "default") else None

    def pending(self):
        return self.backend.pending() if self.backend and hasattr(self.backend, "pending") else []

    def refresh(self):
        if self.backend and hasattr(self.backend, "refresh"):
            self.backend.refresh(force=True)
