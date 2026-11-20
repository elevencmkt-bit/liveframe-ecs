# dmgbuild settings for LiveFrame.dmg ("drag to Applications" window).
#     dmgbuild -s packaging/dmg_settings.py -D app=dist/LiveFrame.app -D art=build/art "LiveFrame" dist/LiveFrame.dmg
import os

app = defines["app"]          # noqa: F821 (dmgbuild injects `defines`)
art = defines["art"]          # noqa: F821

format = "UDZO"
filesystem = "HFS+"
files = [app]
symlinks = {"Applications": "/Applications"}
badge_icon = None
icon = os.path.join(art, "LiveFrame.icns")      # volume icon
icon_locations = {os.path.basename(app): (170, 200), "Applications": (490, 200)}
background = os.path.join(art, "dmg_background.png")   # dmgbuild picks up the @2x file next to it
window_rect = ((200, 160), (660, 400))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 128
text_size = 13
