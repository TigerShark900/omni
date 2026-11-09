"""omni            start the app (tray icon)
omni --no-tray  start without a tray icon
omni status     show what Discord sees and what Omni detects (handy for writing recipes)
omni pause | resume | toggle
omni settings   open the settings window (choose what each app shows)
omni reload     re-read config and recipes
omni update     download the newest Omni from GitHub and restart (--check: only look)
omni quit"""
import json
import sys


def _call(method, *args, timeout=25):
    import dbus
    try:
        obj = dbus.SessionBus().get_object("io.github.tigershark900.Omni", "/Omni")
    except dbus.DBusException:
        sys.exit("Omni isn't running.")
    return getattr(dbus.Interface(obj, "io.github.tigershark900.Omni"), method)(*args, timeout=timeout)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd in ("", "--no-tray", "run"):
        from . import app
        if cmd == "--no-tray" or "--no-tray" in args:
            app.run_headless()
        else:
            app.run_tray()
    elif cmd == "status":
        print(json.dumps(json.loads(_call("Status")), indent=2, ensure_ascii=False))
    elif cmd == "pause":
        _call("SetPaused", True)
    elif cmd == "resume":
        _call("SetPaused", False)
    elif cmd == "toggle":
        print("hidden" if _call("Toggle") else "showing")
    elif cmd == "settings":
        _call("ShowSettings")
    elif cmd == "reload":
        print(_call("Reload"))
    elif cmd == "update":
        print(_call("Update", "--check" in args, timeout=180))
    elif cmd == "quit":
        _call("Quit")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
