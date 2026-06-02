from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

import socket
import serial
import whisper
import asyncio
import threading
import queue
import numpy as np
import time
from collections import deque

from fastapi.staticfiles import StaticFiles

# ============================================================
# CONFIG
# ============================================================

RELAY_CONTROL_IP = "127.0.0.1"
RELAY_CONTROL_PORT = 7000
relay_control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

MIC_PORTS = {
    "MIC1": 6005,
    "MIC2": 6006,
    "MIC3": 6007,
    "MIC4": 6008,
    "MIC5": 6009,
}

ESP32_SAMPLE_RATE = 8000
WHISPER_SAMPLE_RATE = 16000
CHUNK_DURATION = 2

DTYPE = np.int16

SERIAL_PORT = "COM5"
BAUD_RATE = 115200

COLUMNS = 20
MAX_LCD_LEVEL = 32

GAIN = 220
ATTACK = 0.90
DECAY = 0.55
LCD_SEND_INTERVAL = 0.04

WHISPER_MODEL_NAME = "medium"
WHISPER_SILENCE_RMS_THRESHOLD = 0.00025

# Match this to WHISPER_GAIN in relay script
VISUAL_GAIN_COMPENSATION = 6

# ============================================================
# STATE
# ============================================================

selected_mic = "MIC1"
selected_mic_lock = threading.Lock()

ser = None
ser_lock = threading.Lock()

whisper_clients = []
wave_clients = []

audio_queue = queue.Queue(maxsize=5)
text_queue = queue.Queue()
wave_queue = queue.Queue(maxsize=10)

visual_buffer = deque(maxlen=512)
smoothed_columns = [0.0] * COLUMNS

current_udp_socket = None
current_udp_port = None

transcribe_lock = threading.Lock()

print("Loading Whisper model...")
whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
print(f"Whisper model loaded: {WHISPER_MODEL_NAME}")

# ============================================================
# SELECTED MIC
# ============================================================

def get_selected_mic():
    with selected_mic_lock:
        return selected_mic


def get_selected_port():
    return MIC_PORTS[get_selected_mic()]


def clear_queue(q):
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def clear_runtime_buffers():
    global smoothed_columns

    clear_queue(audio_queue)
    clear_queue(wave_queue)
    visual_buffer.clear()
    smoothed_columns = [0.0] * COLUMNS


def broadcast_selected_mic(mic_id):
    text_queue.put({
        "mic": "SYSTEM",
        "selectedMic": mic_id,
        "text": f"{mic_id} selected"
    })


def set_selected_mic(mic_id):
    global selected_mic

    if mic_id not in MIC_PORTS:
        print("[SELECT ERROR] Unknown mic:", mic_id)
        return

    with selected_mic_lock:
        if selected_mic == mic_id:
            return
        selected_mic = mic_id

    clear_runtime_buffers()

    print(f"[SELECTED MIC] {mic_id} | forwarded UDP port {MIC_PORTS[mic_id]}")
    broadcast_selected_mic(mic_id)

    try:
        relay_control_sock.sendto(
            mic_id.encode("utf-8"),
            (RELAY_CONTROL_IP, RELAY_CONTROL_PORT)
        )
        print(f"[RELAY CONTROL] sent {mic_id}")
    except Exception as e:
        print("[RELAY CONTROL ERROR]", e)

# ============================================================
# QUEUES
# ============================================================

def put_latest_audio(samples):
    try:
        audio_queue.put_nowait(samples.copy())
    except queue.Full:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            audio_queue.put_nowait(samples.copy())
        except queue.Full:
            pass


def put_latest_wave(payload):
    try:
        wave_queue.put_nowait(payload)
    except queue.Full:
        try:
            wave_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            wave_queue.put_nowait(payload)
        except queue.Full:
            pass

# ============================================================
# LCD
# ============================================================

def audio_to_20_columns(samples):
    if samples.size == 0:
        return [0] * COLUMNS

    audio_float = samples.astype(np.float32) / 32768.0
    audio_float = audio_float - np.mean(audio_float)

    chunks = np.array_split(audio_float, COLUMNS)
    columns = []

    for chunk in chunks:
        if chunk.size == 0:
            columns.append(0)
            continue

        rms = np.sqrt(np.mean(chunk ** 2))
        level = int((rms ** 0.55) * GAIN)
        level = max(0, min(MAX_LCD_LEVEL, level))
        columns.append(level)

    return columns


def smooth_columns(raw_columns):
    global smoothed_columns

    for i in range(COLUMNS):
        if raw_columns[i] > smoothed_columns[i]:
            smoothed_columns[i] = ATTACK * raw_columns[i] + (1 - ATTACK) * smoothed_columns[i]
        else:
            smoothed_columns[i] = DECAY * smoothed_columns[i] + (1 - DECAY) * raw_columns[i]

    return [max(0, min(MAX_LCD_LEVEL, int(v))) for v in smoothed_columns]


def send_to_lcd(columns):
    if ser is None:
        return

    message = "<" + ",".join(str(v) for v in columns) + ">\n"

    try:
        with ser_lock:
            ser.write(message.encode("utf-8"))
    except Exception as e:
        print("[LCD SERIAL ERROR]", e)


def lcd_update_loop():
    print("[LCD] Update loop started")

    while True:
        if len(visual_buffer) > 0:
            visual_samples = np.array(visual_buffer, dtype=DTYPE)
            raw_columns = audio_to_20_columns(visual_samples)
            output_columns = smooth_columns(raw_columns)
            send_to_lcd(output_columns)

        time.sleep(LCD_SEND_INTERVAL)

# ============================================================
# SERIAL POT CHANNEL READER
# ============================================================

def serial_channel_reader():
    print("[SERIAL] Channel reader started")

    serial_buffer = ""

    while True:
        try:
            if ser and ser.in_waiting:
                with ser_lock:
                    raw = ser.read(ser.in_waiting)

                text = raw.decode("utf-8", errors="ignore")
                serial_buffer += text

                print("[SERIAL RAW]", repr(text))

                while "\n" in serial_buffer:
                    line, serial_buffer = serial_buffer.split("\n", 1)
                    line = line.strip()

                    print("[SERIAL LINE]", repr(line))

                    if line.startswith("C:"):
                        try:
                            channel = int(line.split(":")[1])
                            mic_id = f"MIC{channel}"
                            set_selected_mic(mic_id)
                        except Exception as e:
                            print("[SERIAL PARSE ERROR]", line, e)

                for ch in range(1, 6):
                    token = f"C:{ch}"
                    if token in serial_buffer:
                        print("[SERIAL TOKEN]", token)
                        set_selected_mic(f"MIC{ch}")
                        serial_buffer = ""
                        break

                if len(serial_buffer) > 50:
                    serial_buffer = serial_buffer[-10:]

            time.sleep(0.01)

        except Exception as e:
            print("[SERIAL READ ERROR]", e)
            time.sleep(0.1)

# ============================================================
# UDP RECEIVER
# ============================================================

def open_udp_socket(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(0.2)
    return sock


def make_visual_samples(samples):
    visual_samples = samples.astype(np.float32) / VISUAL_GAIN_COMPENSATION
    visual_samples = np.clip(visual_samples, -32768, 32767)
    return visual_samples.astype(np.int16)


def udp_receiver_loop():
    global current_udp_socket, current_udp_port

    print("[UDP] Selected forwarded-port receiver started")

    last_debug = time.time()
    packet_count = 0

    while True:
        selected_port = get_selected_port()
        selected = get_selected_mic()

        if current_udp_socket is None or current_udp_port != selected_port:
            if current_udp_socket is not None:
                try:
                    current_udp_socket.close()
                except Exception:
                    pass

            try:
                current_udp_socket = open_udp_socket(selected_port)
                current_udp_port = selected_port
                packet_count = 0
                print(f"[UDP] Now listening for {selected} on 0.0.0.0:{selected_port}")
            except OSError as e:
                print("[UDP BIND ERROR]", e)
                time.sleep(0.5)
                continue

        try:
            packet, addr = current_udp_socket.recvfrom(4096)

        except socket.timeout:
            continue

        except Exception as e:
            print("[UDP SOCKET ERROR]", e)
            time.sleep(0.2)
            current_udp_socket = None
            current_udp_port = None
            continue

        mic_id = get_selected_mic()

        if current_udp_port != MIC_PORTS[mic_id]:
            continue

        samples = np.frombuffer(packet, dtype=DTYPE)

        if samples.size == 0:
            continue

        packet_count += 1

        visual_samples = make_visual_samples(samples)

        now = time.time()
        if now - last_debug > 1:
            whisper_rms_debug = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
            visual_rms_debug = np.sqrt(np.mean(visual_samples.astype(np.float32) ** 2))

            print(
                f"[UDP RX] {mic_id} packets={packet_count} "
                f"bytes={len(packet)} samples={samples.size} "
                f"whisper_rms={whisper_rms_debug:.2f} "
                f"visual_rms={visual_rms_debug:.2f}"
            )

            last_debug = now

        # Keep amplified audio for Whisper
        put_latest_audio(samples)

        # Use de-amplified audio for LCD
        for s in visual_samples:
            visual_buffer.append(s)

        # Use de-amplified audio for browser waveform
        audio_float = visual_samples.astype(np.float32) / 32768.0
        audio_float = audio_float - np.mean(audio_float)

        target_points = 128
        step = max(1, len(audio_float) // target_points)
        waveform = audio_float[::step][:target_points]

        if len(waveform) > 3:
            kernel = np.ones(2) / 2
            waveform = np.convolve(waveform, kernel, mode="same")

        rms = np.sqrt(np.mean(audio_float ** 2))

        if rms < 0.001:
            waveform = np.zeros_like(waveform)
        else:
            waveform = waveform * 8

        waveform = np.clip(waveform, -1, 1)

        put_latest_wave({
            "mic": mic_id,
            "waveform": waveform.tolist()
        })

# ============================================================
# WHISPER
# ============================================================

def upsample_to_16k(audio):
    if ESP32_SAMPLE_RATE == WHISPER_SAMPLE_RATE:
        return audio

    if ESP32_SAMPLE_RATE == 8000 and WHISPER_SAMPLE_RATE == 16000:
        return np.repeat(audio, 2)

    raise ValueError("Unsupported sample-rate conversion")


def whisper_worker():
    buffer = np.empty(0, dtype=np.int16)
    required_samples = ESP32_SAMPLE_RATE * CHUNK_DURATION

    print("[WHISPER] Worker started")

    while True:
        data = audio_queue.get()

        if data is None or len(data) == 0:
            continue

        while audio_queue.qsize() > 2:
            try:
                data = audio_queue.get_nowait()
            except queue.Empty:
                break

        buffer = np.concatenate((buffer, data))

        if len(buffer) < required_samples:
            continue

        chunk = buffer[:required_samples]
        buffer = buffer[required_samples:]

        mic_id = get_selected_mic()

        audio_float = chunk.astype(np.float32) / 32768.0
        audio_float = audio_float - np.mean(audio_float)

        rms = np.sqrt(np.mean(audio_float ** 2))

        if rms < WHISPER_SILENCE_RMS_THRESHOLD:
            print(f"[WHISPER] skipped quiet audio rms={rms:.4f}")
            continue

        audio_float = upsample_to_16k(audio_float)

        try:
            print(f"[WHISPER] Transcribing {mic_id} rms={rms:.4f}...")

            with transcribe_lock:
                result = whisper_model.transcribe(
                    audio_float,
                    fp16=False,
                    language="en",
                    task="transcribe",
                    temperature=0,
                    no_speech_threshold=1.0,
                    logprob_threshold=-1.0,
                    condition_on_previous_text=False,
                )

            text = result["text"].strip()

            if text:
                text_queue.put({
                    "mic": mic_id,
                    "text": text
                })

        except Exception as e:
            print("[WHISPER ERROR]", e)

# ============================================================
# WEBSOCKET SENDER
# ============================================================

async def send_messages():
    while True:
        try:
            try:
                text_payload = text_queue.get_nowait()

                for ws in whisper_clients[:]:
                    try:
                        await ws.send_json(text_payload)
                    except Exception:
                        if ws in whisper_clients:
                            whisper_clients.remove(ws)

            except queue.Empty:
                pass

            try:
                wave_payload = wave_queue.get_nowait()

                if wave_payload.get("mic") == get_selected_mic():
                    for ws in wave_clients[:]:
                        try:
                            await ws.send_json(wave_payload)
                        except Exception:
                            if ws in wave_clients:
                                wave_clients.remove(ws)

            except queue.Empty:
                pass

            await asyncio.sleep(0.03)

        except Exception as e:
            print("[WEBSOCKET LOOP ERROR]", e)
            await asyncio.sleep(0.05)

# ============================================================
# STARTUP
# ============================================================

def start_background_systems():
    global ser

    print("[STARTUP] Starting main server systems...")

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.05)
        time.sleep(2)
        print(f"[SERIAL] Opened {SERIAL_PORT} at {BAUD_RATE}")
    except Exception as e:
        ser = None
        print("[SERIAL ERROR]", e)

    threading.Thread(target=serial_channel_reader, daemon=True).start()
    threading.Thread(target=udp_receiver_loop, daemon=True).start()
    threading.Thread(target=whisper_worker, daemon=True).start()
    threading.Thread(target=lcd_update_loop, daemon=True).start()

    broadcast_selected_mic(get_selected_mic())

    print("[STARTUP] Current selected mic:", get_selected_mic())
    print("[STARTUP] Current forwarded UDP port:", get_selected_port())


@asynccontextmanager
async def lifespan(app):
    start_background_systems()
    asyncio.create_task(send_messages())
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# WEBSOCKETS
# ============================================================

@app.websocket("/ws/whisper")
async def whisper_ws(websocket: WebSocket):
    await websocket.accept()
    whisper_clients.append(websocket)

    await websocket.send_json({
        "mic": "SYSTEM",
        "selectedMic": get_selected_mic(),
        "text": "Listening to selected ESP32 microphone..."
    })

    try:
        while True:
            data = await websocket.receive_text()

            if data == "ping":
                await websocket.send_json({
                    "mic": "SYSTEM",
                    "selectedMic": get_selected_mic(),
                    "text": "pong"
                })

    except Exception as e:
        print("[WHISPER WS ERROR]", e)

    finally:
        if websocket in whisper_clients:
            whisper_clients.remove(websocket)


@app.websocket("/ws/wave")
async def wave_ws(websocket: WebSocket):
    await websocket.accept()
    wave_clients.append(websocket)

    await websocket.send_json({
        "mic": get_selected_mic(),
        "waveform": []
    })

    try:
        while True:
            data = await websocket.receive_text()

            if data == "ping":
                await websocket.send_json({
                    "mic": get_selected_mic(),
                    "waveform": []
                })

    except Exception as e:
        print("[WAVE WS ERROR]", e)

    finally:
        if websocket in wave_clients:
            wave_clients.remove(websocket)

# ============================================================
# ROUTES
# ============================================================

@app.get("/test")
async def test():
    return {
        "status": "running",
        "selected_mic": get_selected_mic(),
        "selected_forwarded_port": get_selected_port(),
        "mic_ports": MIC_PORTS,
        "esp32_sample_rate": ESP32_SAMPLE_RATE,
        "whisper_sample_rate": WHISPER_SAMPLE_RATE,
        "chunk_duration": CHUNK_DURATION,
        "whisper_model": WHISPER_MODEL_NAME,
        "lcd_serial_port": SERIAL_PORT,
        "lcd_baud_rate": BAUD_RATE,
        "visual_gain_compensation": VISUAL_GAIN_COMPENSATION,
        "audio_playback": "handled by separate relay script",
    }


app.mount("/", StaticFiles(directory=".", html=True), name="static")

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 60)
    print("MAIN SERVER: WHISPER + LCD + WEBSOCKET")
    print("=" * 60)
    print("Receives forwarded audio from relay script.")
    print("MIC1 forwarded:", MIC_PORTS["MIC1"])
    print("MIC2 forwarded:", MIC_PORTS["MIC2"])
    print("MIC3 forwarded:", MIC_PORTS["MIC3"])
    print("MIC4 forwarded:", MIC_PORTS["MIC4"])
    print("MIC5 forwarded:", MIC_PORTS["MIC5"])
    print("Visual gain compensation:", VISUAL_GAIN_COMPENSATION)
    print("=" * 60)

    uvicorn.run(app, host="127.0.0.1", port=8000)