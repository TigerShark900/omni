# Omni

A Discord status for everything you do on KDE Plasma (Wayland).

> Built with AI as a quick, useful tool rather than a passion project. It does what it says;
> use it if it helps you.

How it decides what to show (the highest priority wins):

| Layer | Priority | Where it comes from |
|---|---|---|
| Apps with their own Discord status | skipped | they already show it, so Omni leaves them alone |
| Recipes | 50-60 (games, tools) | the online library, `omni/recipes/*.toml`, and your own in `~/.config/omni/recipes/` |
| Media | 40 | anything playing through MPRIS (Spotify, browsers, mpv with mpv-mpris) |
| Steam games | 30 | any Steam game, using its Steam id and store art |
| Fallback | 0 | "Using <app name>" (the name only, never the window title) |

Focusing Discord, the desktop or KRunner doesn't change your status.

## Install

Omni needs KDE Plasma 6 on Wayland, Python 3.11+, PyQt6, dbus-python and PyGObject
(Fedora: `sudo dnf install python3-pyqt6-base python3-dbus python3-gobject`).

    git clone https://github.com/TigerShark900/omni
    cd omni
    ./install.sh
    omni

Omni starts by itself when you log in. Keep the `omni` folder: Omni runs from it.

That's it. Omni uses its own shared Discord app, so your status card says "Omni".
Want your own app (own name on the card, own pictures)? Make one at
https://discord.com/developers/applications and put its Application ID in
`~/.config/omni/config.toml` as `client_id`.

## Updating

New recipes and icons arrive by themselves (Omni checks the library once a day).

For Omni itself, Omni checks GitHub once a day and shows **Update Omni** in the tray menu when
there's a new version. Click it to download it and restart. Omni never updates without you
clicking. From a terminal: `omni update` (or `omni update --check` to only look).
That's the same as `git pull` in the `omni` folder followed by `omni quit; omni`.

If you downloaded a ZIP instead of using git, download the new version and run `./install.sh` again.

## Settings window

Click the tray dot (or run `omni settings`). **Apps** lists every app Omni has seen; set each one to
*Show everything*, *Only the app name* or *Never show*. **General** has the on/off switches.
The window saves to `~/.config/omni/rules.json`, which wins over `config.toml`.

## Pictures

Discord only shows pictures that live on the web or in your Discord app's **Art Assets**.

- **Steam games** use their store art, and **YouTube videos** use YouTube's own thumbnail. Nothing to set up.
- **App icons** come from the [`icons/`](icons) folder in this repo. Discord loads them from GitHub,
  so they work for everyone with nothing to set up. To add one, see [icons/README.md](icons/README.md).
- **Pictures in the shared app** work for everyone too. Recipes can use them by name
  (`large_image = "minecraft"`), and app icons with a matching name are picked up automatically.
- **Recipes** can also use any web image link (`large_image = "https://..."`), on any app.
- **App icons on your own app**: Omni saves each app's icon (512px, small PNGs) in `~/Pictures/Omni icons`.
  Tray menu → *Add icons to Discord…* opens that folder and your app's Art Assets page; drag them in and Save.
  Omni notices within a few minutes and moves uploaded ones out of the folder, so it only ever holds new ones.
- **The blue dot** (`omni.png`, in the same folder) is used whenever there's no other picture.
- **Your own server** instead: set `[icons]` `base_url` + `upload` in `config.toml` (see the example).
  Then icons upload automatically, and song covers work too (one shared file, replaced each time).

## The recipe library

Recipes for games and apps live in [`library/`](library) in this repo, not inside Omni itself.
Omni downloads `library/index.json` about once a day. It's small: it holds only each recipe's
`[match]` rules. Omni checks it **on your PC**, and only when something you run matches does it
download that one recipe, into `~/.local/share/omni/library/`. It keeps the recipe and works
offline from then on. So nobody gets a list of what you play; the most GitHub sees is that
someone downloaded the Minecraft recipe once.

Your own recipes and the bundled ones win over library ones with the same file name.
Switch it off with `library = false` (or in Settings → General).

Library recipes are text only, and they also can't use `[[log]]` (which reads files on your PC)
or match something as broad as "any window". Omni checks this again after downloading.

**Adding a game:** see [CONTRIBUTING.md](CONTRIBUTING.md). In short: a recipe in `library/`, a
picture (an icon in `icons/` or an official link), the rebuilt index, and a screenshot.

## Commands

    omni            start (tray icon)
    omni status     what Discord sees + what Omni detects (shows the variables recipes can use)
    omni settings   open the settings window
    omni toggle     hide/show your status
    omni reload     re-read config and recipes
    omni update     update Omni from GitHub and restart (--check: only look)

## Writing a recipe

```toml
name = "Kdenlive"
priority = 50

[match]                 # every line must match (regular expressions)
class = 'kdenlive'      # also: process, exe, title, steam_appid / player, identity, artist, url for media
title = '^(?P<project>[^/]+?)\.kdenlive'   # named groups become {variables}

[activity]
type = "playing"        # playing / listening / watching / competing
details = "Editing {project}"
state = "in Kdenlive"
large_image = "kdenlive"      # a name from icons/ (or your Discord app's Art Assets), or an https:// link

[[buttons]]             # up to 2
label = "Website"
url = "https://example.com"

[[log]]                 # optional: pull variables from the newest matching log file
file = "~/.local/share/someapp/logs/*.log"
pattern = 'Loaded level (?P<level>.+)$'
```

Run `omni status` with the app focused to see its class, title and process.

## License

GPL-3.0, see [LICENSE](LICENSE).
