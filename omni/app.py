"""The background app: listens to KWin, polls media, sends the status, shows a tray icon."""
import json
import os
import shutil
import subprocess
import sys
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

from .engine import Engine, CONFIG_DIR, load_rules, save_rules
from .ipc import DiscordIPC, DiscordError
from .icons import Icons
from .sources import Focus, Media
from .updater import Updater

BUS_NAME = "io.github.tigershark900.Omni"
KWIN_SCRIPT = "omni-focus"
MIN_GAP = 4.5   # Discord allows ~5 status updates per 20 seconds


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _same(a, b):
    """Same status, ignoring timer wobble of a couple of seconds (media players drift)."""
    if a is None or b is None:
        return a is b
    a, b = dict(a), dict(b)
    ta, tb = a.pop("timestamps", {}), b.pop("timestamps", {})
    if a != b or set(ta) != set(tb):
        return False
    return all(abs(ta[k] - tb[k]) < 3000 for k in ta)


class Service(dbus.service.Object):
    def __init__(self, app, bus):
        self.app = app
        dbus.service.Object.__init__(self, dbus.service.BusName(BUS_NAME, bus), "/Omni")

    @dbus.service.method(BUS_NAME, in_signature="sssi")
    def Focus(self, caption, resource_class, desktop_file, pid):
        self.app.focus.update(str(caption), str(resource_class), int(pid), str(desktop_file))
        self.app.kwin_alive = time.time()

    @dbus.service.method(BUS_NAME, out_signature="s")
    def Status(self):
        return json.dumps(self.app.preview(), ensure_ascii=False)

    @dbus.service.method(BUS_NAME, in_signature="b")
    def SetPaused(self, paused):
        self.app.set_paused(bool(paused))

    @dbus.service.method(BUS_NAME, out_signature="b")
    def Toggle(self):
        self.app.set_paused(not self.app.engine.paused)
        return self.app.engine.paused

    @dbus.service.method(BUS_NAME, out_signature="s")
    def Reload(self):
        self.app.engine.reload()
        return "\n".join(self.app.engine.errors) or "ok"

    @dbus.service.method(BUS_NAME, in_signature="b", out_signature="s")
    def Update(self, check_only):
        u = self.app.updater
        if check_only:
            if not u.is_git:
                return "This copy of Omni wasn't installed with git, so it can't check for updates."
            u._check()
            return f"{u.behind} new change(s) on GitHub." if u.behind else "Omni is up to date."
        ok, msg = self.app.update()
        return msg + (" Restarting Omni." if ok else "")

    @dbus.service.method(BUS_NAME)
    def ShowSettings(self):
        self.app.show_settings()

    @dbus.service.method(BUS_NAME)
    def Quit(self):
        self.app.quit()


class App:
    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        if self.bus.name_has_owner(BUS_NAME):
            sys.exit("Omni is already running.")
        self.focus = Focus()
        self.engine = Engine(self.focus, Media(self.bus), log)
        self.engine.paused = bool(self.engine.config.get("paused", False))  # stays hidden across restarts
        self.engine.icons = Icons(log)
        self.engine.icons.web = self.engine.library.icon_url
        self.engine.icons.configure(self.engine.config.get("icons"), self.engine.config["client_id"],
                                    self.engine.config["shared_app"])
        for e in self.engine.errors:
            log("recipe error:", e)
        self.service = Service(self, self.bus)
        self.discord = None
        self.sent = None             # what Discord currently shows
        self.wanted = None           # what we want it to show
        self.wanted_info = {}
        self.last_send = 0
        self.retry_at = 0
        self.error = ""
        self.kwin_alive = 0
        self.on_change = lambda: None
        self.show_settings = lambda: log("the settings window needs the tray version (run: omni)")
        self.last_seen_save = time.time()
        self.updater = Updater(log)
        self.refresh_kwin()

    # ---------------------------------------------------------- KWin

    def refresh_kwin(self):
        """Reload the KWin script so it tells us which window is focused right now."""
        try:
            kwin = self.bus.get_object("org.kde.KWin", "/Scripting")
            iface = dbus.Interface(kwin, "org.kde.kwin.Scripting")
            bundled = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kwin", KWIN_SCRIPT)
            installed = os.path.expanduser(f"~/.local/share/kwin/scripts/{KWIN_SCRIPT}")
            path = os.path.join(bundled, "contents", "code", "main.js")
            if os.path.isdir(installed):  # keep the installed copy (used at login) in step with this version
                for rel in ("metadata.json", "contents/code/main.js"):
                    src, dst = os.path.join(bundled, rel), os.path.join(installed, rel)
                    if _read_bytes(src) != _read_bytes(dst):
                        shutil.copyfile(src, dst)
            if iface.isScriptLoaded(KWIN_SCRIPT):
                iface.unloadScript(KWIN_SCRIPT)
            iface.get_dbus_method("loadScript")(path, KWIN_SCRIPT, signature="ss")
            iface.start()
        except dbus.DBusException as e:
            log("couldn't talk to KWin:", e.get_dbus_message())

    # ---------------------------------------------------------- loop

    def set_paused(self, paused):
        self.engine.paused = paused
        rules = load_rules()
        rules.setdefault("settings", {})["paused"] = paused
        save_rules(rules)
        log("paused" if paused else "resumed")
        self.tick()

    def update(self):
        """Pull the new version from GitHub, then restart into it."""
        ok, msg = self.updater.update()
        log("update:", msg)
        if ok:
            from gi.repository import GLib
            GLib.timeout_add(500, self.restart)  # let the DBus reply go out first
        return ok, msg

    def restart(self):
        self.shutdown()
        os.execv(sys.executable, [sys.executable, "-m", "omni", *sys.argv[1:]])

    def tick(self):
        self.updater.maybe_check()
        try:
            act, client_id, info = self.engine.tick()
        except Exception as e:  # a bad recipe shouldn't kill the app
            log("engine error:", repr(e))
            return True
        self.wanted, self.wanted_info = act, info
        now = time.time()
        if now - self.last_seen_save > 60:
            self.engine.save_seen()
            self.last_seen_save = now
        if not client_id:
            self.error = "No client_id set in ~/.config/omni/config.toml"
            self.on_change()
            return True
        if _same(act, self.sent) and self.discord and self.discord.client_id == client_id:
            return True
        if now - self.last_send < MIN_GAP or now < self.retry_at:
            return True
        try:
            if not self.discord or self.discord.client_id != client_id:
                if self.discord:
                    self.discord.close()
                self.discord = DiscordIPC(client_id)
                self.discord.connect()
                log("connected to Discord as", (self.discord.user or {}).get("username", "?"))
            self.discord.set_activity(act)
            self.sent = act
            self.error = ""
            log("status:", info.get("source"), "|", (act or {}).get("details", "-"), "|", (act or {}).get("state", ""))
        except (OSError, DiscordError) as e:
            self.error = str(e)
            self.discord = None
            self.sent = None
            self.retry_at = now + 15
            log("discord:", e)
        self.last_send = now
        self.on_change()
        return True

    def preview(self):
        return {
            "showing": self.sent,
            "wanted": self.wanted,
            "source": self.wanted_info.get("source"),
            "variables": self.wanted_info.get("variables", {}),
            "focus": self.focus.variables(),
            "paused": self.engine.paused,
            "error": self.error,
            "recipe_errors": self.engine.errors,
        }

    def shutdown(self):
        self.engine.save_seen()
        if self.discord:
            try:
                self.discord.set_activity(None)
            except (OSError, DiscordError):
                pass
            self.discord.close()
            self.discord = None

    def quit(self):
        self.shutdown()
        self.loop_quit()


def run_headless():
    from gi.repository import GLib
    app = App()
    loop = GLib.MainLoop()
    app.loop_quit = loop.quit
    GLib.timeout_add(1000, app.tick)
    try:
        loop.run()
    except KeyboardInterrupt:
        app.shutdown()


def run_tray():
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QMessageBox

    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("Omni")
    qapp.setDesktopFileName("omni")
    app = App()
    app.loop_quit = qapp.quit

    def dot(color):
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(6, 6, 52, 52)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(22, 22, 20, 20)
        p.end()
        return QIcon(pm)

    icons = {"live": dot("#5865F2"), "paused": dot("#80848E"), "error": dot("#BF464B")}
    tray = QSystemTrayIcon(icons["live"])
    menu = QMenu()
    now_item = QAction("…")
    now_item.setEnabled(False)
    pause = QAction("Hide my status")
    pause.setCheckable(True)
    pause.toggled.connect(lambda on: app.set_paused(on))
    settings = QAction(QIcon.fromTheme("configure"), "Settings…")
    add_icons = QAction(QIcon.fromTheme("folder-pictures"), "Add icons to Discord…")
    preview = QAction("What does Discord see?")
    folder = QAction("Open recipes folder")
    reload_ = QAction("Reload recipes and config")
    update_ = QAction(QIcon.fromTheme("update-none"), "Update Omni")
    quit_ = QAction("Quit")
    for a in (now_item, None, update_, pause, settings, add_icons, preview, folder, reload_, None, quit_):
        menu.addSeparator() if a is None else menu.addAction(a)
    tray.setContextMenu(menu)

    def show_preview():
        p = app.preview()
        a = p["showing"] or {}
        lines = [f"Source: {p['source']}", ""]
        if p["paused"]:
            lines.append("Your status is hidden.")
        elif not a:
            lines.append("Nothing is being shown.")
        else:
            kind = {0: "Playing", 2: "Listening", 3: "Watching", 5: "Competing"}.get(a.get("type", 0))
            lines += [f"{kind}", f"Details: {a.get('details', '-')}", f"State: {a.get('state', '-')}"]
            if a.get("assets", {}).get("large_image"):
                lines.append(f"Image: {a['assets']['large_image']}")
        if p["error"]:
            lines += ["", f"Problem: {p['error']}"]
        QMessageBox.information(None, "Omni - what Discord sees", "\n".join(lines))

    def open_folder():
        d = os.path.join(CONFIG_DIR, "recipes")
        os.makedirs(d, exist_ok=True)
        subprocess.Popen(["xdg-open", d])

    def do_reload():
        app.engine.reload()
        msg = "\n".join(app.engine.errors) or "Recipes and config reloaded."
        tray.showMessage("Omni", msg)

    window = {"w": None}

    def open_settings():
        from .settings import SettingsWindow
        if window["w"] is None:
            window["w"] = SettingsWindow(app)
        else:
            window["w"].fill_table()
            window["w"].refresh_now()
        w = window["w"]
        w.show()
        w.raise_()
        w.activateWindow()

    app.show_settings = open_settings
    settings.triggered.connect(open_settings)
    def update_icon_item():
        n = len(app.engine.icons.pending())
        add_icons.setVisible(n > 0)
        add_icons.setText(f"Add icons to Discord… ({n} new)")

    def open_icons():
        from .icons import ASSET_DIR
        os.makedirs(ASSET_DIR, exist_ok=True)
        subprocess.Popen(["xdg-open", ASSET_DIR])
        cid = app.engine.config.get("client_id", "")
        if cid:
            subprocess.Popen(["xdg-open", f"https://discord.com/developers/applications/{cid}/rich-presence/assets"])
        tray.showMessage("Omni", "Drag the pictures from the folder into Art Assets and click Save. "
                                 "Omni picks them up within a few minutes.")
        app.engine.icons.refresh()

    def update_update_item():
        n = app.updater.behind
        update_.setVisible(n > 0)
        update_.setText(f"Update Omni ({n} new change{'s' if n != 1 else ''})")

    def do_update():
        ok, msg = app.update()
        if not ok:
            QMessageBox.warning(None, "Omni", msg)

    # the check runs in the background; hop back to the Qt thread for the message
    notice = {"n": 0}
    app.updater.notify = lambda n: notice.update(n=n)

    def show_notice():
        if notice["n"]:
            tray.showMessage("Omni", "A new version of Omni is out. Right-click the tray dot → Update Omni.")
            notice["n"] = 0

    notice_timer = QTimer()
    notice_timer.timeout.connect(show_notice)
    notice_timer.start(5000)
    update_.triggered.connect(do_update)
    menu.aboutToShow.connect(update_update_item)
    update_update_item()
    menu.aboutToShow.connect(update_icon_item)
    add_icons.triggered.connect(open_icons)
    update_icon_item()
    preview.triggered.connect(show_preview)
    tray.activated.connect(lambda r: open_settings() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
    folder.triggered.connect(open_folder)
    reload_.triggered.connect(do_reload)
    quit_.triggered.connect(app.quit)

    def changed():
        a = app.sent or {}
        if app.engine.paused:
            text, icon = "Status hidden", "paused"
        elif app.error:
            text, icon = f"Problem: {app.error}", "error"
        elif a:
            text, icon = " · ".join(x for x in (a.get("details"), a.get("state")) if x), "live"
        else:
            text, icon = "Nothing to show", "live"
        now_item.setText(text[:80])
        tray.setToolTip(f"Omni\n{text}")
        tray.setIcon(icons[icon])
        if window["w"] is not None and window["w"].isVisible():
            window["w"].refresh_now()
        if pause.isChecked() != app.engine.paused:
            pause.blockSignals(True)
            pause.setChecked(app.engine.paused)
            pause.blockSignals(False)

    app.on_change = changed
    timer = QTimer()
    timer.timeout.connect(app.tick)
    timer.start(1000)
    changed()
    tray.show()
    qapp.aboutToQuit.connect(app.shutdown)
    sys.exit(qapp.exec())
