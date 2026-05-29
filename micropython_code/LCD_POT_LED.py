from machine import Pin, I2C, ADC
from i2c_lcd import I2cLcd
import sys
import uselect
from time import ticks_ms, ticks_diff

# ------------ LEDS ---------------
# Channel 1 = leds[0], Channel 2 = leds[1], etc.
leds = [
    Pin(12, Pin.OUT),
    Pin(13, Pin.OUT),
    Pin(27, Pin.OUT),
    Pin(14, Pin.OUT),
    Pin(26, Pin.OUT)
]

# ---------- LCD CONFIG ----------
I2C_ADDR_LCD = 0x27
ROWS = 4
COLS = 20

i2c = I2C(0, scl=Pin(19), sda=Pin(18), freq=400000)
lcd = I2cLcd(i2c, I2C_ADDR_LCD, ROWS, COLS)

# ---------- ADC / POT CONFIG ----------
pot = ADC(Pin(34))
pot.atten(ADC.ATTN_11DB)

pot2 = ADC(Pin(33))
pot2.atten(ADC.ATTN_11DB)

last_channel = 1
last_pot_check = ticks_ms()
POT_INTERVAL_MS = 100

# ---------- SERIAL NON-BLOCKING SETUP ----------
poll = uselect.poll()
poll.register(sys.stdin, uselect.POLLIN)

buffer = ""
receiving = False

# ---------- CUSTOM BAR CHARS ----------
cell1 = bytearray([0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x1F])
cell2 = bytearray([0x00,0x00,0x00,0x00,0x00,0x00,0x1F,0x1F])
cell3 = bytearray([0x00,0x00,0x00,0x00,0x00,0x1F,0x1F,0x1F])
cell4 = bytearray([0x00,0x00,0x00,0x00,0x1F,0x1F,0x1F,0x1F])
cell5 = bytearray([0x00,0x00,0x00,0x1F,0x1F,0x1F,0x1F,0x1F])
cell6 = bytearray([0x00,0x00,0x1F,0x1F,0x1F,0x1F,0x1F,0x1F])
cell7 = bytearray([0x00,0x1F,0x1F,0x1F,0x1F,0x1F,0x1F,0x1F])
cell8 = bytearray([0x1F,0x1F,0x1F,0x1F,0x1F,0x1F,0x1F,0x1F])

lcd.custom_char(0, cell1)
lcd.custom_char(1, cell2)
lcd.custom_char(2, cell3)
lcd.custom_char(3, cell4)
lcd.custom_char(4, cell5)
lcd.custom_char(5, cell6)
lcd.custom_char(6, cell7)
lcd.custom_char(7, cell8)

lcd.clear()

# ---------- LED HELPERS ----------
def all_leds_off():
    for led in leds:
        led.off()


def set_channel_led(channel):
    """
    channel is 1–5.
    Turns on only the LED for the selected channel.
    """
    for i, led in enumerate(leds):
        if i == channel - 1:
            led.on()
        else:
            led.off()


all_leds_off()
set_channel_led(last_channel)


def partial_char(pixels):
    if pixels <= 0:
        return " "
    if pixels >= 8:
        return chr(7)
    return chr(pixels - 1)


def draw_bottom_bars(values):
    """
    values: 20 numbers from 0–32.
    Bars grow from the bottom of the LCD upwards.
    """

    screen = [[" " for _ in range(COLS)] for _ in range(ROWS)]

    for col in range(COLS):
        level = max(0, min(32, values[col]))

        for row_from_bottom in range(ROWS):
            pixels = level - (row_from_bottom * 8)

            if pixels <= 0:
                char = " "
            elif pixels >= 8:
                char = chr(7)
            else:
                char = partial_char(pixels)

            lcd_row = ROWS - 1 - row_from_bottom
            screen[lcd_row][col] = char

    for row in range(ROWS):
        lcd.move_to(0, row)
        lcd.putstr("".join(screen[row]))


def parse_frame(frame):
    """
    Expected serial format:
    <0,4,12,20,32,28,18,9,3,0,6,14,22,31,25,16,8,4,1,0>
    """

    try:
        parts = frame.split(",")

        if len(parts) != COLS:
            return None

        values = []

        for part in parts:
            value = int(part.strip())
            value = max(0, min(32, value))
            values.append(value)

        return values

    except:
        return None


def get_channel():
    pot_value = pot.read()
    pot2_value = pot2.read()

    if pot_value < 70 and pot2_value < 70:
        return 1

    elif 80 < pot_value < 200 and 80 < pot2_value < 200:
        return 2

    elif 400 < pot_value < 1000 and 400 < pot2_value < 1000:
        return 3

    elif 1500 < pot_value < 3000 and 1500 < pot2_value < 3000:
        return 4

    elif pot_value > 3500 and pot2_value > 3500:
        return 5

    return None


while True:
    # ---------- NON-BLOCKING SERIAL READ FOR LCD AUDIO BARS ----------
    if poll.poll(0):
        char = sys.stdin.read(1)

        if char == "<":
            buffer = ""
            receiving = True

        elif char == ">":
            receiving = False
            values = parse_frame(buffer)

            if values is not None:
                draw_bottom_bars(values)

        elif receiving:
            buffer += char

    # ---------- NON-BLOCKING POT CHECK ----------
    now = ticks_ms()

    if ticks_diff(now, last_pot_check) >= POT_INTERVAL_MS:
        last_pot_check = now

        current_channel = get_channel()

        if current_channel is not None and current_channel != last_channel:
            set_channel_led(current_channel)
            print("C:{}".format(current_channel))
            last_channel = current_channel