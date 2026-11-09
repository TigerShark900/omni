# Icons

Pictures Omni shows on your Discord status. Discord's servers load them straight from this
folder, so they work for everyone without uploading anything to a Discord app.

- PNG, 512×512, at most 512 KB.
- Name it after the app: its window class, process or desktop file name, lowercase, with
  anything that isn't a letter or digit turned into `_` (`org.kde.konsole` → `org_kde_konsole.png`).
  `omni status` shows these names. Recipes can also use one directly: `large_image = "spotify"`.
- If the app goes by other names too (the Fedora/Debian package, its window class or process,
  e.g. VLC is `org.videolan.VLC` on Flathub but `vlc` from your distro), list them in
  [`aliases.toml`](aliases.toml) instead of copying the picture.
- Run `python3 tools/build_index.py` afterwards so the index lists it.
- Use the app's own unchanged icon or one from a free icon theme. For games, link to official
  art from the recipe instead (like the Steam store images) rather than copying it here.

Logos and icons belong to their owners. They're here only to show which app is running, and
don't mean the owners have anything to do with Omni. If you own one and want it removed, open
an issue and it will be taken down.
