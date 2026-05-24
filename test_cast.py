import pychromecast
import time
import signal
import sys
from math import inf

MEDIA_BASE_PATH = "/tmp/dir"
f="qrtc_image.png"
mime="image/png"
SERVER="192.168.1.39:8080"

chromecasts, browser = pychromecast.get_chromecasts()

print (chromecasts)

cast = chromecasts[0]
cast.wait()

# Kill current app/session
cast.quit_app()

# Give Chromecast time to reset
time.sleep(3)

# IMPORTANT:
# Re-fetch media controller after quit_app
mc = cast.media_controller

# Start media
mc.play_media(
    f"http://{SERVER}/stream/{f}",
    mime
)

# Wait for receiver activation
mc.block_until_active()

# Ensure playback starts
mc.play()

print("Playing")

try:
    while True:
        time.sleep (2)

except KeyboardInterrupt:
    print("Caught Ctrl+C")
    mc.stop()
    time.sleep(2)
    cast.quit_app()
