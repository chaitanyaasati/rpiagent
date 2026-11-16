import time, signal, sys
from PIL import Image, ImageDraw
import tft

# treat "systemctl stop" (SIGTERM) like Ctrl+C
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

tft.init()
tft.backlight(True)
big, small = tft.font(64, bold=True), tft.font(22)

try:
    while True:
        img = Image.new("RGB", (tft.WIDTH, tft.HEIGHT), "black")
        d = ImageDraw.Draw(img)
        d.text((160, 100), time.strftime("%H:%M:%S"), font=big, fill="cyan", anchor="mm")
        d.text((160, 170), time.strftime("%A, %d %b %Y"), font=small, fill="white", anchor="mm")
        tft.show(img)
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    tft.off()
    print("Display off")