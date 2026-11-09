# Adding a game or app to Omni

Thanks for helping! Every recipe in [`library/`](library) is downloaded by Omni users who play
that game, so please follow these rules. GitHub checks most of them automatically when you open
the pull request.

## What a pull request needs

1. **One recipe** in `library/<name>.toml`, one game or app per pull request.
   The file name is lowercase letters, digits and dashes: `hollow-knight.toml`.
2. **A picture**: `large_image` in `[activity]` must be set, either to
   - the name of an icon in [`icons/`](icons) (add it in the same pull request if it's new), or
   - an `https://` link to **official** art, like the game's Steam store image.
3. **The updated index**: run `python3 tools/build_index.py` and commit `library/index.json` too.
4. **A screenshot** of the status in Discord, so we can see it works.

## Recipe rules

- **Match something specific.** `[match]` must only fit that game or app, not "any browser" or
  "any window". For Steam games, match the Steam id exactly: `steam_appid = '^367520$'`.
  The checker rejects patterns that would fit ordinary apps.
- **Respect privacy.** If the status would show file names, project names, documents, chats or
  anything else personal, put `default = "hidden"` at the top. People then switch it on
  themselves in Settings. Games can be shown by default.
- **No `[[log]]` and no `client_id`.** Library recipes can't read files on people's PCs, and
  they all use Omni's shared Discord app.
- **Buttons** may only link to the official game or app page (store page, official website).
  No referral links, ads, or personal pages.
- **Keep the text plain**: say what the person is doing, no ads, nothing offensive.
- `priority`: 60 for games, 50 for apps. Lower only if you know why.

## Icon rules

See [icons/README.md](icons/README.md). In short:

- PNG, 512×512, at most 512 KB, named after the app (`org.kde.konsole` → `org_kde_konsole.png`).
- Use the app's own unchanged icon, or one from a free icon theme.
- For games, link to official art from the recipe (like the Steam store image) instead of copying it here.
- Never edit a company's logo or make it look like they made Omni.

## Testing your recipe

1. Copy it to `~/.config/omni/recipes/` and run `omni reload`.
2. Open the game or app and run `omni status`. It shows what Discord sees and every variable
   (`title`, `class`, `steam_appid`, ...) you can use in `[match]` and `{...}` text.
3. Check your status in Discord, take the screenshot, then delete your local copy so the
   library version is used.

See [Writing a recipe](README.md#writing-a-recipe) for the full recipe format.
