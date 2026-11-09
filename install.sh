#!/bin/bash
# Installs Omni for the current user: command, KWin focus script, autostart, starter config.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p ~/.local/bin ~/.local/share/kwin/scripts ~/.config/omni/recipes ~/.config/autostart ~/.local/share/applications

cat > ~/.local/bin/omni <<EOS
#!/bin/bash
exec env PYTHONPATH="$HERE" python3 -m omni "\$@"
EOS
chmod +x ~/.local/bin/omni

rm -rf ~/.local/share/kwin/scripts/omni-focus
cp -r "$HERE/kwin/omni-focus" ~/.local/share/kwin/scripts/
kwriteconfig6 --file kwinrc --group Plugins --key omni-focusEnabled true

[ -f ~/.config/omni/config.toml ] || cp "$HERE/config.example.toml" ~/.config/omni/config.toml

for f in ~/.config/autostart/omni.desktop ~/.local/share/applications/omni.desktop; do
cat > "$f" <<EOS
[Desktop Entry]
Type=Application
Name=Omni
Comment=Discord status for everything you do
Exec=$HOME/.local/bin/omni
Icon=discord
Terminal=false
Categories=Utility;
EOS
done

echo "Installed. Run: omni"
