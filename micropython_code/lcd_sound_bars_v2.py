from machine import Pin, I2C
from i2c_lcd import I2cLcd
import sys

I2C_ADDR_LCD = 0x27
ROWS = 4
COLS = 20

i2c = I2C(0, scl=Pin(19), sda=Pin(18), freq=400000)
lcd = I2cLcd(i2c, I2C_ADDR_LCD, ROWS, COLS)

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


buffer = ""
receiving = False

while True:
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