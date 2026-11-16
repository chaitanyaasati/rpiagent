import time, spidev
import RPi.GPIO as GPIO
from PIL import ImageFont

DC, RST = 24, 25
WIDTH, HEIGHT = 320, 240

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(DC, GPIO.OUT)
GPIO.setup(RST, GPIO.OUT)

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 8_000_000
spi.mode = 0

def _cmd(c, data=None):
    GPIO.output(DC, 0); spi.writebytes([c])
    if data:
        GPIO.output(DC, 1); spi.writebytes2(bytes(data))

def init():
    GPIO.output(RST, 1); time.sleep(0.05)
    GPIO.output(RST, 0); time.sleep(0.05)
    GPIO.output(RST, 1); time.sleep(0.15)
    _cmd(0x01); time.sleep(0.15)
    _cmd(0x11); time.sleep(0.15)
    _cmd(0x3A, [0x55])
    _cmd(0x36, [0x28])          # landscape
    _cmd(0x29); time.sleep(0.05)

def _rgb565(img):
    try:
        import numpy as np
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        v = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
        return v.astype(">u2").tobytes()
    except ImportError:
        out = bytearray()
        for r, g, b in img.convert("RGB").getdata():
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out += bytes([v >> 8, v & 0xFF])
        return bytes(out)

def show(img):
    _cmd(0x2A, [0, 0, (WIDTH - 1) >> 8, (WIDTH - 1) & 0xFF])
    _cmd(0x2B, [0, 0, (HEIGHT - 1) >> 8, (HEIGHT - 1) & 0xFF])
    _cmd(0x2C)
    GPIO.output(DC, 1)
    spi.writebytes2(_rgb565(img))

def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default()

# ---- backlight + power control ----
LED = 23                      # pin 16
GPIO.setup(LED, GPIO.OUT)

def backlight(on=True):
    GPIO.output(LED, 1 if on else 0)

def off():
    _cmd(0x28)                # display off
    _cmd(0x10)                # sleep mode
    backlight(False)
