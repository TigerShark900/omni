"""The Omni settings window: choose what each app is allowed to show on Discord."""
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .engine import load_rules, save_rules
from .sources import desktop_entry

LEVELS = [("full", "Show everything"), ("vague", "Only the app name"), ("hidden", "Never show")]
KINDS = {"app": "App", "game": "Game", "media": "Media"}

GENERAL = [
    ("fallback", "Show apps that have no recipe (\"Using Dolphin\")", False),
    ("media", "Show music and videos that are playing", False),
    ("window_icons", "Use the app's own icon as the picture", True),
    ("youtube_lookup", "Recognise YouTube videos in the browser (thumbnail + Watch button)", True),
    ("cover_art", "Upload cover art for songs and videos (only the current one is kept)", True),
    ("respect_native", "Leave apps alone that set their own Discord status", True),
    ("library", "Get recipes for new games from Omni's online library", True),
]
SHOW = [("details", "What you're doing (\"Coding with Claude\")"), ("state", "The second line"),
        ("name", "The Discord app's name (\"Omni\")")]


def _ago(t):
    d = int(time.time() - t)
    if d < 120:
        return "just now"
    if d < 3600:
        return f"{d // 60} min ago"
    if d < 86400:
        return f"{d // 3600} h ago"
    days = d // 86400
    return "1 day ago" if days == 1 else f"{days} days ago"


def _icon_for(item):
    if item.get("steam_appid"):
        icon = QIcon.fromTheme(f"steam_icon_{item['steam_appid']}")
        if not icon.isNull():
            return icon
    entry = desktop_entry(item.get("desktop", ""), item.get("class", ""), item.get("process", ""))
    for name in ((entry or ("", ""))[1], item.get("desktop", ""), item.get("class", "")):
        if name:
            icon = QIcon(name) if name.startswith("/") else QIcon.fromTheme(name)
            if not icon.isNull():
                return icon
    return QIcon.fromTheme("application-x-executable")


class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("Omni settings")
        self.setWindowIcon(QIcon.fromTheme("discord"))
        self.resize(640, 560)

        self.now = QLabel()
        self.now.setWordWrap(True)
        self.now.setStyleSheet("padding: 8px; border-radius: 6px; background: palette(alternate-base);")

        tabs = QTabWidget()
        tabs.addTab(self._apps_tab(), "Apps")
        tabs.addTab(self._general_tab(), "General")

        lay = QVBoxLayout(self)
        lay.addWidget(self.now)
        lay.addWidget(tabs)
        self.refresh_now()

    # ---------------------------------------------------------- apps

    def _apps_tab(self):
        w = QWidget()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["App", "Type", "Last seen", "On Discord"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hint = QLabel("Apps appear here once you've used them while Omni is running. Out of the box only games "
                      "and fun stuff are shown; switch anything else on here. "
                      "<b>Only the app name</b> hides titles, files and songs, and shows just \"Using &lt;app&gt;\".")
        hint.setWordWrap(True)
        refresh = QPushButton(QIcon.fromTheme("view-refresh"), "Refresh")
        refresh.clicked.connect(self.fill_table)
        top = QHBoxLayout()
        top.addWidget(self.search)
        top.addWidget(refresh)
        lay = QVBoxLayout(w)
        lay.addLayout(top)
        lay.addWidget(self.table)
        lay.addWidget(hint)
        self.fill_table()
        return w

    def fill_table(self):
        seen = self.app.engine.seen
        apps = self.app.engine.apps
        rows = sorted(seen.items(), key=lambda kv: -kv[1].get("last", 0))
        self.table.setRowCount(len(rows))
        for r, (key, item) in enumerate(rows):
            name = QTableWidgetItem(_icon_for(item), item.get("name") or key)
            name.setToolTip(f"Window class: {item.get('class') or '-'}\nProcess: {item.get('process') or '-'}")
            name.setData(Qt.ItemDataRole.UserRole, key)
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(KINDS.get(item.get("kind"), "App")))
            self.table.setItem(r, 2, QTableWidgetItem(_ago(item.get("last", 0))))
            combo = QComboBox()
            for value, label in LEVELS:
                combo.addItem(label, value)
            current = self._level_of(key, item, apps)
            combo.setCurrentIndex([v for v, _ in LEVELS].index(current))
            combo.currentIndexChanged.connect(lambda _i, k=key, c=combo: self.set_level(k, c.currentData()))
            self.table.setCellWidget(r, 3, combo)
        self._filter(self.search.text())

    def _level_of(self, key, item, apps):
        for k in (key, item.get("class", "").lower(), item.get("process", "").lower(), (item.get("name") or "").lower()):
            if k and k in apps:
                return apps[k].get("detail", "full")
        if self.app.engine._blocked({"class": item.get("class", ""), "process": item.get("process", ""),
                                     "app": item.get("name", "")}):
            return "hidden"
        return item.get("default", "full")

    def _filter(self, text):
        text = text.lower().strip()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            self.table.setRowHidden(r, bool(text) and text not in item.text().lower()
                                    and text not in str(item.data(Qt.ItemDataRole.UserRole)))

    def set_level(self, key, level):
        rules = load_rules()
        rules.setdefault("apps", {})[key] = level
        self._save(rules)

    # ---------------------------------------------------------- general

    def _general_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.app.engine.config
        self.paused = QCheckBox("Hide my status completely")
        self.paused.setChecked(self.app.engine.paused)
        self.paused.toggled.connect(self.app.set_paused)
        form.addRow(self.paused)
        form.addRow(QLabel(""))
        for key, label, default in GENERAL:
            box = QCheckBox(label)
            box.setChecked(bool(cfg.get(key, default)))
            box.toggled.connect(lambda on, k=key: self.set_setting(k, on))
            form.addRow(box)
        show = QComboBox()
        for value, label in SHOW:
            show.addItem(label, value)
        values = [v for v, _ in SHOW]
        show.setCurrentIndex(values.index(cfg.get("show", "details")) if cfg.get("show") in values else 0)
        show.currentIndexChanged.connect(lambda _i: self.set_setting("show", show.currentData()))
        form.addRow(QLabel(""))
        form.addRow("Member list shows:", show)
        return w

    def set_setting(self, key, value):
        rules = load_rules()
        rules.setdefault("settings", {})[key] = value
        self._save(rules)

    def _save(self, rules):
        save_rules(rules)
        self.app.engine.reload()
        self.app.tick()
        self.refresh_now()

    # ---------------------------------------------------------- top line

    def refresh_now(self):
        a = self.app.sent or {}
        if self.app.engine.paused:
            text = "Your status is <b>hidden</b>."
        elif self.app.error:
            text = f"Problem: {self.app.error}"
        elif a:
            kind = {0: "Playing", 2: "Listening to", 3: "Watching", 5: "Competing in"}.get(a.get("type", 0), "Playing")
            lines = " · ".join(x for x in (a.get("details"), a.get("state")) if x)
            text = f"Discord sees: <b>{kind}</b> {lines}"
        else:
            text = "Discord sees: nothing right now."
        self.now.setText(text)
        if self.paused.isChecked() != self.app.engine.paused:
            self.paused.blockSignals(True)
            self.paused.setChecked(self.app.engine.paused)
            self.paused.blockSignals(False)

    def closeEvent(self, e):
        self.app.engine.save_seen()
        e.accept()
