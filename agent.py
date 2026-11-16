"""Simple AI agent for the TFT display.

Type instructions in plain English, e.g.
    show the time
    show nvidia stock
    what's apple trading at? put it on the screen
    show reliance industries
    tell me a joke
    show a motivational quote
    turn the display off

Claude (via the Anthropic API) picks a display tool; a background thread keeps
the screen updated (clock every second, stocks every 60 s) while you type.

Needs:  pip install anthropic pillow
        export ANTHROPIC_API_KEY=...   (or `ant auth login`)
"""
import json, time, signal, sys, threading, urllib.request, urllib.parse
from PIL import Image, ImageDraw
import anthropic
import tft

MODEL = "claude-opus-5-5"
STOCK_REFRESH = 60  # seconds

W, H = tft.WIDTH, tft.HEIGHT
f_title, f_huge = tft.font(22, bold=True), tft.font(64, bold=True)
f_big, f_mid, f_small = tft.font(52, bold=True), tft.font(22), tft.font(14)
CURRENCY = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥"}

# treat "systemctl stop" (SIGTERM) like Ctrl+C
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


# ---------------------------------------------------------------- stock data
def fetch_stock(symbol):
    """Return dict with price info for a Yahoo Finance symbol; raises on failure."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?interval=5m&range=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        res = json.load(r)["chart"]["result"][0]
    meta = res["meta"]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    return {
        "symbol": meta["symbol"],
        "name": meta.get("shortName") or meta.get("longName") or meta["symbol"],
        "currency": meta.get("currency", "USD"),
        "price": meta["regularMarketPrice"],
        "prev": meta.get("chartPreviousClose") or meta.get("previousClose"),
        "closes": closes,
    }


# ---------------------------------------------------------------- drawing
def draw_clock():
    img = Image.new("RGB", (W, H), "black")
    d = ImageDraw.Draw(img)
    d.text((W // 2, 100), time.strftime("%H:%M:%S"), font=f_huge, fill="cyan", anchor="mm")
    d.text((W // 2, 170), time.strftime("%A, %d %b %Y"), font=f_mid, fill="white", anchor="mm")
    tft.show(img)


_fonts = {}
def _font(size):
    if size not in _fonts:
        _fonts[size] = tft.font(size)
    return _fonts[size]


def wrap(text, font, width):
    """Split text into lines that fit `width` pixels (keeps the user's line breaks)."""
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            while font.getlength(word) > width:  # very long word: hard-split it
                cut = len(word)
                while cut > 1 and font.getlength(word[:cut]) > width:
                    cut -= 1
                if line:
                    lines.append(line); line = ""
                lines.append(word[:cut]); word = word[cut:]
            trial = f"{line} {word}".strip()
            if font.getlength(trial) <= width:
                line = trial
            else:
                lines.append(line); line = word
        lines.append(line)
    return lines


def draw_text(title, text):
    """Title (optional) plus body text, using the biggest font size that fits."""
    img = Image.new("RGB", (W, H), "black")
    d = ImageDraw.Draw(img)
    top, margin = 10, 12
    if title:
        d.text((W // 2, top), title[:40], font=f_title, fill="yellow", anchor="mt")
        top += 36
    box_w, box_h = W - 2 * margin, H - top - 8

    for size in (32, 28, 24, 22, 20, 18, 16, 14):
        font, step = _font(size), int(size * 1.25)
        lines = wrap(text, font, box_w)
        if len(lines) * step <= box_h:
            break
    else:  # still too long at the smallest size: cut it off
        lines = lines[: box_h // step]
        lines[-1] = lines[-1].rstrip(" .") + "…"

    y = top + (box_h - len(lines) * step) // 2  # centre vertically
    for line in lines:
        d.text((W // 2, y), line, font=font, fill="white", anchor="mt")
        y += step
    tft.show(img)


def draw_stock(symbol, data, status):
    img = Image.new("RGB", (W, H), "black")
    d = ImageDraw.Draw(img)
    title = f"{data['name'][:18]} ({data['symbol']})" if data else symbol
    d.text((15, 10), title, font=f_title, fill="white")

    if data is None:
        d.text((15, 100), "Loading...", font=f_mid, fill="gray")
    else:
        price, prev, closes = data["price"], data["prev"], data["closes"]
        cur = CURRENCY.get(data["currency"], data["currency"] + " ")
        change = price - prev
        pct = change / prev * 100
        up = change >= 0
        color = (0, 200, 80) if up else (230, 50, 50)
        arrow = "▲" if up else "▼"
        d.text((15, 42), f"{cur}{price:,.2f}", font=f_big, fill="white")
        d.text((15, 110), f"{arrow} {change:+.2f}  ({pct:+.2f}%)", font=f_mid, fill=color)

        if len(closes) > 1:  # intraday sparkline
            lo, hi = min(closes + [prev]), max(closes + [prev])
            span = (hi - lo) or 1
            x0, x1, y0, y1 = 15, W - 15, 150, 212
            py = lambda v: y1 - (v - lo) / span * (y1 - y0)
            d.line([(x0, py(prev)), (x1, py(prev))], fill=(70, 70, 70))
            pts = [(x0 + i * (x1 - x0) / (len(closes) - 1), py(c))
                   for i, c in enumerate(closes)]
            d.line(pts, fill=color, width=2)

    d.text((15, 220), status, font=f_small, fill="gray")
    tft.show(img)


# ---------------------------------------------------------------- display thread
class Display:
    """Runs in the background and keeps whatever mode is selected on screen."""

    def __init__(self):
        self.mode, self.symbol, self.data, self.fetched_at = "off", None, None, 0
        self.panel_on = False  # True once tft.init() has run since the last tft.off()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, mode, symbol=None, data=None):
        with self.lock:
            self.mode, self.symbol, self.data = mode, symbol, data
            self.fetched_at = time.time() if data else 0
        self.wake.set()

    def _run(self):
        while True:
            self.wake.clear()
            with self.lock:
                mode, symbol, data, fetched_at = self.mode, self.symbol, self.data, self.fetched_at
            try:
                if mode != "off" and not self.panel_on:
                    # tft.off() puts the panel to sleep; backlight alone won't wake it
                    tft.init()
                    tft.backlight(True)
                    self.panel_on = True
                if mode == "clock":
                    draw_clock()
                    self.wake.wait(1)
                elif mode == "text":
                    draw_text(*data)
                    self.wake.wait()  # static: redraw only when something changes
                elif mode == "stock":
                    status = None
                    if time.time() - fetched_at >= STOCK_REFRESH:
                        try:
                            data, fetched_at = fetch_stock(symbol), time.time()
                            with self.lock:
                                if self.symbol == symbol:
                                    self.data, self.fetched_at = data, fetched_at
                        except Exception as e:
                            status = f"Offline - retrying ({type(e).__name__})"
                            fetched_at = time.time()  # wait a full interval before retrying
                    status = status or "Updated " + time.strftime("%H:%M:%S", time.localtime(fetched_at))
                    draw_stock(symbol, data, status)
                    self.wake.wait(max(1, STOCK_REFRESH - (time.time() - fetched_at)))
                else:  # off
                    if self.panel_on:
                        tft.off()
                        self.panel_on = False
                    self.wake.wait()
            except Exception as e:  # never let the display thread die
                print(f"\n[display error: {e}]")
                self.wake.wait(5)


display = None  # created in main()


# ---------------------------------------------------------------- tools for Claude
TOOLS = [
    {
        "name": "show_time",
        "description": "Show a live clock (time and date) on the TFT display.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [],
                         "additionalProperties": False},
    },
    {
        "name": "show_stock",
        "description": (
            "Show a live stock price with today's change and an intraday chart on the "
            "TFT display, refreshed every minute. Takes a Yahoo Finance ticker symbol: "
            "US stocks use the plain ticker (NVDA, AAPL, MSFT); Indian NSE stocks end "
            "in .NS (RELIANCE.NS, TCS.NS); BSE in .BO; London .L; indices like ^GSPC. "
            "Returns the current price, or an error if the symbol is not found."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string",
                                      "description": "Yahoo Finance ticker, e.g. NVDA"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "show_text",
        "description": (
            "Show text on the TFT display, e.g. a joke, quote, fact, short answer or "
            "message. The screen is small (320x240): the font shrinks to fit, so keep "
            "it short - ideally under 150 characters, at most about 350. Use \\n for "
            "line breaks (e.g. between a joke's setup and punchline). The text stays "
            "on screen until something else is shown."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string",
                          "description": "Short heading (a few words), or empty string for none"},
                "text": {"type": "string", "description": "The body text to show"},
            },
            "required": ["title", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "turn_off_display",
        "description": "Turn the TFT display and its backlight off.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [],
                         "additionalProperties": False},
    },
]

SYSTEM = (
    "You control a small 320x240 screen attached to a Raspberry Pi. The user types "
    "short instructions. Use the tools to show the time, show a stock price, show "
    "text, or turn the screen off. Map company names to their ticker yourself (e.g. "
    "Nvidia -> NVDA). If a symbol fails, try the most likely alternative once. "
    "When the user asks for something to read - a joke, quote, fact, riddle, message "
    "or a short answer to a question - write it yourself and put it on the screen "
    "with show_text, kept short enough to read at a glance. "
    "If a request is not something the screen can do, say so briefly. "
    "Reply in one short sentence, or repeat the shown text."
)


def run_tool(name, args):
    if name == "show_time":
        display.set("clock")
        return "Clock is now on the display."
    if name == "show_stock":
        symbol = args["symbol"].strip().upper()
        data = fetch_stock(symbol)  # raises if unknown -> reported as tool error
        display.set("stock", symbol, data)
        return (f"Showing {data['name']} ({data['symbol']}): {data['price']:.2f} "
                f"{data['currency']}, previous close {data['prev']:.2f}.")
    if name == "show_text":
        text = args["text"].replace("\\n", "\n").strip()
        if not text:
            raise ValueError("text is empty")
        display.set("text", data=(args["title"].strip(), text))
        return "Text is now on the display."
    if name == "turn_off_display":
        display.set("off")
        return "Display is off."
    raise ValueError(f"Unknown tool {name}")


def ask(client, messages, text):
    """Send one user instruction, run any tool calls, return Claude's reply text."""
    messages.append({"role": "user", "content": text})
    for _ in range(5):  # a few tool round-trips at most
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM,
            tools=TOOLS,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "refusal":
            return "(request declined)"
        if resp.stop_reason != "tool_use":
            return " ".join(b.text for b in resp.content if b.type == "text").strip()

        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                out, err = run_tool(block.name, block.input), False
            except Exception as e:
                out, err = f"Error: {type(e).__name__}: {e}", True
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": out, "is_error": err})
        messages.append({"role": "user", "content": results})
    return "(gave up after too many steps)"


def main():
    global display
    client = anthropic.Anthropic()
    display = Display()  # initialises the panel on the first show_* request
    messages = []
    print("Display agent ready. Try: 'show the time', 'show nvidia', 'show apple', 'off'.")
    print("Ctrl+C or 'quit' to exit.\n")
    try:
        while True:
            try:
                text = input("> ").strip()
            except EOFError:
                break
            if not text:
                continue
            if text.lower() in ("quit", "exit"):
                break
            try:
                print(ask(client, messages, text))
            except anthropic.APIConnectionError:
                messages.clear()  # history may end mid-turn; start fresh
                print("Can't reach the Claude API - check the network.")
            except anthropic.APIStatusError as e:
                messages.clear()
                print(f"API error {e.status_code}: {e.message}")
            if len(messages) > 40:  # keep history short; start fresh
                messages.clear()
    except KeyboardInterrupt:
        pass
    finally:
        tft.off()
        print("\nDisplay off")


if __name__ == "__main__":
    main()
