import json, time, signal, sys, urllib.request
from PIL import Image, ImageDraw
import tft

SYMBOL = "NVDA"
NAME = "NVIDIA"
REFRESH = 60   # seconds
URL = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=5m&range=1d"

W, H = tft.WIDTH, tft.HEIGHT
f_title, f_big = tft.font(22, bold=True), tft.font(52, bold=True)
f_mid, f_small = tft.font(22), tft.font(14)

# treat "systemctl stop" (SIGTERM) like Ctrl+C
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        res = json.load(r)["chart"]["result"][0]
    meta = res["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    return price, prev, closes

def draw(data, status):
    img = Image.new("RGB", (W, H), "black")
    d = ImageDraw.Draw(img)
    d.text((15, 10), f"{NAME} ({SYMBOL})", font=f_title, fill="white")

    if data is None:
        d.text((15, 100), "Loading...", font=f_mid, fill="gray")
    else:
        price, prev, closes = data
        change = price - prev
        pct = change / prev * 100
        up = change >= 0
        color = (0, 200, 80) if up else (230, 50, 50)
        arrow = "▲" if up else "▼"

        d.text((15, 42), f"${price:,.2f}", font=f_big, fill="white")
        d.text((15, 110), f"{arrow} {change:+.2f}  ({pct:+.2f}%)", font=f_mid, fill=color)

        # intraday sparkline
        if len(closes) > 1:
            lo, hi = min(closes + [prev]), max(closes + [prev])
            span = (hi - lo) or 1
            x0, x1, y0, y1 = 15, W - 15, 150, 212
            py = lambda v: y1 - (v - lo) / span * (y1 - y0)
            d.line([(x0, py(prev)), (x1, py(prev))], fill=(70, 70, 70))  # prev close
            pts = [(x0 + i * (x1 - x0) / (len(closes) - 1), py(c))
                   for i, c in enumerate(closes)]
            d.line(pts, fill=color, width=2)

    d.text((15, 220), status, font=f_small, fill="gray")
    tft.show(img)

tft.init()
tft.backlight(True)

try:
    data = None
    draw(None, "Fetching price...")
    while True:
        try:
            data = fetch()
            status = "Updated " + time.strftime("%H:%M:%S")
        except Exception as e:
            status = f"Offline - retrying ({type(e).__name__})"
        draw(data, status)
        time.sleep(REFRESH)
except KeyboardInterrupt:
    pass
finally:
    tft.off()
    print("Display off")
