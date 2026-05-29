from machine import I2S, Pin
import network
import socket
import time
import gc

WIFI_SSID = "UAL-IoT"
WIFI_PASSWORD = "WRpr4559!@UPNS"

PC_IP = "10.65.100.145"
PC_PORT = 5006 #mics (1-5) port 5005-5009

# ---------- WiFi ----------
gc.collect()
print("free before wifi:", gc.mem_free())

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(WIFI_SSID, WIFI_PASSWORD)

while not wlan.isconnected():
    time.sleep_ms(200)

print("WiFi:", wlan.ifconfig())
gc.collect()
print("free after wifi:", gc.mem_free())

# ---------- I2S microphone ----------
audio_in = I2S(
    1,
    sck=Pin(14),
    ws=Pin(15),
    sd=Pin(32),
    mode=I2S.RX,
    bits=16,
    format=I2S.MONO,
    rate=8000,
    ibuf=1024
)

gc.collect()
print("free after i2s:", gc.mem_free())

# ---------- UDP socket ----------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# connect UDP socket to avoid repeatedly allocating destination tuple
sock.connect((PC_IP, PC_PORT))

buf = bytearray(128)
view = memoryview(buf)

print("starting stream")

try:
    while True:
        print("before read")
        n = audio_in.readinto(buf)
        print("after read")

        if n:
            try:
                sock.send(view[:n])
                print(n)
            except OSError as e:
                print("send error:", e)
                time.sleep_ms(50)

        # prevents ESP32 network stack from being overwhelmed
        time.sleep_ms(5)

except Exception as e:
    print("error:", e)

finally:
    audio_in.deinit()
    sock.close()
    gc.collect()
    print("free after cleanup:", gc.mem_free())
