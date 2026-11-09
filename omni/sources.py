"""Where Omni learns what you're doing: the focused window, media players, Steam."""
import configparser
import glob
import os
import re
import time

import dbus

# ---------------------------------------------------------------- windows

def _read(path, binary=False):
    try:
        with open(path, "rb" if binary else "r") as f:
            return f.read()
    except OSError:
        return b"" if binary else ""


def process_info(pid):
    """Process name, exe name and Steam app id for a window's process."""
    info = {"process": "", "exe": "", "steam_appid": ""}
    if not pid or pid <= 0:
        return info
    info["process"] = _read(f"/proc/{pid}/comm").strip()
    cmd = _read(f"/proc/{pid}/cmdline", binary=True).split(b"\0")
    if cmd and cmd[0]:
        first = cmd[0].decode(errors="replace")
        info["exe"] = re.sub(r"\s+--?\w.*$", "", re.split(r"[\\/]", first)[-1])
    env = _read(f"/proc/{pid}/environ", binary=True).split(b"\0")
    for kv in env:
        if kv.startswith((b"SteamAppId=", b"SteamGameId=")):
            val = kv.split(b"=", 1)[1].decode(errors="replace")
            if val.isdigit() and val != "0" and int(val) < 2**31:
                info["steam_appid"] = val
                break
    return info


_steam_names = {}


def steam_name(appid):
    """Look up a Steam game's name from its install manifest."""
    if not appid:
        return ""
    if appid in _steam_names:
        return _steam_names[appid]
    name = ""
    roots = [os.path.expanduser("~/.local/share/Steam"), os.path.expanduser("~/.steam/steam"),
             os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/Steam")]
    for root in roots:
        lib_file = os.path.join(root, "steamapps", "libraryfolders.vdf")
        libs = [root] + re.findall(r'"path"\s+"([^"]+)"', _read(lib_file))
        for lib in libs:
            m = re.search(r'"name"\s+"([^"]+)"', _read(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")))
            if m:
                name = m.group(1)
                break
        if name:
            break
    _steam_names[appid] = name
    return name


_desktop_index = None


def _build_desktop_index():
    index = {}
    dirs = [os.path.expanduser("~/.local/share/applications"), "/usr/share/applications",
            "/var/lib/flatpak/exports/share/applications",
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications")]
    for d in reversed(dirs):  # user files win, so read them last
        for path in glob.glob(os.path.join(d, "*.desktop")):
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                cp.read(path, encoding="utf-8")
                entry = cp["Desktop Entry"]
            except Exception:
                continue
            name = entry.get("Name")
            if not name:
                continue
            item = (name, entry.get("Icon", ""))
            stem = os.path.basename(path)[:-8].lower()
            index[stem] = item
            index[stem.split(".")[-1]] = index.get(stem.split(".")[-1], item)
            wm = entry.get("StartupWMClass")
            if wm:
                index[wm.lower()] = item
            index.setdefault(name.lower(), item)
    return index


def desktop_entry(*keys):
    """(name, icon) from the app's .desktop file, or None."""
    global _desktop_index
    if _desktop_index is None:
        _desktop_index = _build_desktop_index()
    for key in keys:
        if key and key.lower() in _desktop_index:
            return _desktop_index[key.lower()]
    return None


def pretty_app_name(resource_class, process, desktop_file=""):
    entry = desktop_entry(desktop_file, resource_class, process)
    if entry:
        return entry[0]
    raw = resource_class or process or "something"
    raw = raw.split(".")[-1].replace("-", " ").replace("_", " ")
    return raw[:1].upper() + raw[1:]


class Focus:
    """The window you're looking at right now (fed by the KWin script)."""

    def __init__(self):
        self.caption = ""
        self.resource_class = ""
        self.pid = 0
        self.desktop_file = ""
        self.proc = process_info(0)
        self.changed_at = time.time()

    def update(self, caption, resource_class, pid, desktop_file=""):
        self.desktop_file = desktop_file
        if pid != self.pid:
            self.proc = process_info(pid)
        if (caption, resource_class, pid) != (self.caption, self.resource_class, self.pid):
            self.changed_at = time.time()
        self.caption, self.resource_class, self.pid = caption, resource_class, pid

    def variables(self):
        v = dict(self.proc)
        v["title"] = self.caption
        v["class"] = self.resource_class
        v["pid"] = str(self.pid)
        v["desktop"] = self.desktop_file
        v["app"] = pretty_app_name(self.resource_class, self.proc["process"], self.desktop_file)
        v["steam_name"] = steam_name(self.proc["steam_appid"])
        if v["steam_name"]:
            v["app"] = v["steam_name"]
        return v

# ---------------------------------------------------------------- media (MPRIS)

MPRIS = "org.mpris.MediaPlayer2"


class Media:
    """Reads what music/video players report through MPRIS."""

    def __init__(self, bus):
        self.bus = bus
        self.last_playing = None

    def _player(self, name):
        obj = self.bus.get_object(name, "/org/mpris/MediaPlayer2")
        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        status = str(props.Get(MPRIS + ".Player", "PlaybackStatus"))
        if status != "Playing":
            return None
        meta = props.Get(MPRIS + ".Player", "Metadata")
        try:
            identity = str(props.Get(MPRIS, "Identity"))
        except dbus.DBusException:
            identity = name.split(".")[3] if name.count(".") >= 3 else name
        try:
            desktop = str(props.Get(MPRIS, "DesktopEntry"))
        except dbus.DBusException:
            desktop = ""
        try:
            position = int(props.Get(MPRIS + ".Player", "Position"))
        except dbus.DBusException:
            position = -1
        artist = meta.get("xesam:artist", [])
        if isinstance(artist, (list, dbus.Array)):
            artist = ", ".join(str(a) for a in artist)
        return {
            "player": name[len(MPRIS) + 1:],
            "identity": identity,
            "desktop": desktop,
            "title": str(meta.get("xesam:title", "")),
            "artist": str(artist or ""),
            "album": str(meta.get("xesam:album", "")),
            "art_url": str(meta.get("mpris:artUrl", "")),
            "url": str(meta.get("xesam:url", "")),
            "length_us": int(meta.get("mpris:length", 0) or 0),
            "position_us": position,
        }

    def current(self, skip=()):
        playing = []
        try:
            names = [str(n) for n in self.bus.list_names() if str(n).startswith(MPRIS + ".")]
        except dbus.DBusException:
            return None
        for name in names:
            if any(re.search(p, name[len(MPRIS) + 1:], re.I) for p in skip):
                continue
            try:
                p = self._player(name)
            except dbus.DBusException:
                continue
            if p and p["title"]:
                playing.append(p)
        if not playing:
            self.last_playing = None
            return None
        # Stick with the player that was already showing, if it's still playing
        for p in playing:
            if p["player"] == self.last_playing:
                return p
        self.last_playing = playing[0]["player"]
        return playing[0]

# ---------------------------------------------------------------- apps with their own Discord status

import subprocess

_presence_cache = (0.0, set())


def pids_talking_to_discord():
    """Processes (other than Omni) connected to Discord's status socket - they set their own status."""
    global _presence_cache
    now = time.time()
    if now - _presence_cache[0] < 5:
        return _presence_cache[1]
    try:
        out = subprocess.run(["ss", "-xpn"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    by_inode, client_inodes = {}, set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[1] != "ESTAB":
            continue
        local, local_ino, peer_ino = parts[4], parts[5], parts[7]
        pids = {int(p) for p in re.findall(r"pid=(\d+)", line)}
        by_inode[local_ino] = pids
        if "discord-ipc" in local:
            client_inodes.add(peer_ino)
    pids = set()
    for ino in client_inodes:
        pids |= by_inode.get(ino, set())
    pids.discard(os.getpid())
    _presence_cache = (now, pids)
    return pids


def _parent(pid):
    m = re.search(r"^PPid:\s+(\d+)", _read(f"/proc/{pid}/status"), re.M)
    return int(m.group(1)) if m else 0


def has_own_presence(pid):
    """True if this app (or a process it started) sets its own Discord status."""
    talkers = pids_talking_to_discord()
    if not pid or not talkers:
        return False
    for t in talkers:
        q = t
        for _ in range(6):  # is the talker the focused app, or a process it started?
            if q == pid:
                return True
            q = _parent(q)
            if q <= 1:
                break
    return False
