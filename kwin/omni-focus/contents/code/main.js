// Sends the focused window's title, class and process id to Omni over DBus.
var current = null;

function send(w) {
    if (w) {
        callDBus("io.github.tigershark900.Omni", "/Omni", "io.github.tigershark900.Omni", "Focus",
                 String(w.caption || ""), String(w.resourceClass || ""),
                 String(w.desktopFileName || ""), w.pid || 0);
    } else {
        callDBus("io.github.tigershark900.Omni", "/Omni", "io.github.tigershark900.Omni", "Focus", "", "", "", 0);
    }
}

function onCaption() { send(current); }

workspace.windowActivated.connect(function (w) {
    if (current) {
        try { current.captionChanged.disconnect(onCaption); } catch (e) {}
    }
    current = w;
    if (w) w.captionChanged.connect(onCaption);
    send(w);
});

current = workspace.activeWindow;
if (current) current.captionChanged.connect(onCaption);
send(current);
