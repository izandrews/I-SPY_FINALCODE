import socket
import sounddevice as sd
import numpy as np
import threading
import time
from collections import deque

MIC_RELAY = {
    "MIC1": {"listen_port": 5005, "forward_port": 6005},
    "MIC2": {"listen_port": 5006, "forward_port": 6006},
    "MIC3": {"listen_port": 5007, "forward_port": 6007},
    "MIC4": {"listen_port": 5008, "forward_port": 6008},
    "MIC5": {"listen_port": 5009, "forward_port": 6009},
}

LISTEN_IP = "0.0.0.0"
FORWARD_IP = "127.0.0.1"

CONTROL_IP = "127.0.0.1"
CONTROL_PORT = 7000

SAMPLE_RATE = 8000
CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 512 #256

AUDIO_OUTPUT_DEVICE = None
MAX_PLAYBACK_BUFFER_SAMPLES = SAMPLE_RATE // 2

# Easy volume fixes
PLAYBACK_GAIN = 20
AUTO_NORMALIZE = True
TARGET_PEAK = 22000
MIN_NORMALIZE_PEAK = 100

WHISPER_GAIN = 8

selected_mic = "MIC1"
selected_mic_lock = threading.Lock()

playback_buffers = {
    mic_id: deque()
    for mic_id in MIC_RELAY.keys()
}

buffer_lock = threading.Lock()
running = True


def get_selected_mic():
    with selected_mic_lock:
        return selected_mic


def set_selected_mic(mic_id):
    global selected_mic

    if mic_id not in MIC_RELAY:
        print("[CONTROL] Unknown mic:", mic_id)
        return

    with selected_mic_lock:
        if selected_mic == mic_id:
            return
        selected_mic = mic_id

    with buffer_lock:
        for buf in playback_buffers.values():
            buf.clear()

    print(f"[CONTROL] Selected playback mic: {selected_mic}")


def control_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((CONTROL_IP, CONTROL_PORT))

    print(f"[CONTROL] Listening on {CONTROL_IP}:{CONTROL_PORT}")

    while True:
        try:
            packet, addr = sock.recvfrom(1024)
            msg = packet.decode("utf-8", errors="ignore").strip().upper()

            if msg in MIC_RELAY:
                set_selected_mic(msg)
            else:
                print("[CONTROL] Ignored message:", msg)

        except Exception as e:
            print("[CONTROL ERROR]", e)
            time.sleep(0.1)


def audio_callback(outdata, frames, time_info, status):
    if status:
        print("[AUDIO STATUS]", status)

    selected = get_selected_mic()
    out = np.zeros(frames, dtype=np.float32)

    with buffer_lock:
        buf = playback_buffers[selected]

        for i in range(frames):
            if buf:
                out[i] = float(buf.popleft())

    # Fixed gain first
    out *= PLAYBACK_GAIN

    # Automatic block normalization for quiet mic signal
    if AUTO_NORMALIZE:
        peak = np.max(np.abs(out))

        if peak > MIN_NORMALIZE_PEAK:
            normalize_gain = TARGET_PEAK / peak
            out *= normalize_gain

    out = np.clip(out, -32768, 32767)
    outdata[:, 0] = out.astype(DTYPE)


def add_samples_to_playback(mic_id, samples):
    selected = get_selected_mic()

    if mic_id != selected:
        return

    with buffer_lock:
        buf = playback_buffers[mic_id]

        for s in samples:
            buf.append(s)

        while len(buf) > MAX_PLAYBACK_BUFFER_SAMPLES:
            buf.popleft()


def relay_mic(mic_id, listen_port, forward_port):
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind((LISTEN_IP, listen_port))

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(
        f"[{mic_id}] Listening {LISTEN_IP}:{listen_port} "
        f"→ forwarding {FORWARD_IP}:{forward_port}"
    )

    packet_count = 0
    last_debug = time.time()

    while running:
        try:
            packet, addr = recv_sock.recvfrom(4096)
            packet_count += 1

            samples = np.frombuffer(packet, dtype=DTYPE)

            if samples.size > 0:
                add_samples_to_playback(mic_id, samples)

                whisper_samples = samples.astype(np.int32)
                whisper_samples *= WHISPER_GAIN
                whisper_samples = np.clip(whisper_samples, -32768, 32767).astype(np.int16)

                whisper_packet = whisper_samples.tobytes()
                send_sock.sendto(whisper_packet, (FORWARD_IP, forward_port))
            else:
                send_sock.sendto(packet, (FORWARD_IP, forward_port))

            now = time.time()

            if now - last_debug > 1:
                rms = (
                    np.sqrt(np.mean(samples.astype(np.float32) ** 2))
                    if samples.size
                    else 0
                )

                peak = np.max(np.abs(samples)) if samples.size else 0

                print(
                    f"[{mic_id}] packets={packet_count} "
                    f"bytes={len(packet)} samples={samples.size} "
                    f"rms={rms:.2f} peak={peak} "
                    f"selected={get_selected_mic()}"
                )

                last_debug = now

        except Exception as e:
            print(f"[{mic_id}] Relay error:", e)
            time.sleep(0.1)


def main():
    print("\n" + "=" * 60)
    print("MULTI-MIC RELAY + SELECTED-MIC SPEAKER PLAYBACK")
    print("=" * 60)

    for mic_id, cfg in MIC_RELAY.items():
        print(f"{mic_id}: {cfg['listen_port']} → {cfg['forward_port']}")

    print("Control port:", CONTROL_PORT)
    print("Initial selected mic:", selected_mic)
    print("Output device:", AUDIO_OUTPUT_DEVICE if AUDIO_OUTPUT_DEVICE is not None else "system default")
    print("Playback gain:", PLAYBACK_GAIN)
    print("Auto normalize:", AUTO_NORMALIZE)
    print("Target peak:", TARGET_PEAK)
    print("=" * 60)

    stream = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=audio_callback,
        blocksize=BLOCKSIZE,
        device=AUDIO_OUTPUT_DEVICE,
        latency="low",
    )

    stream.start()

    threading.Thread(target=control_listener, daemon=True).start()

    for mic_id, cfg in MIC_RELAY.items():
        threading.Thread(
            target=relay_mic,
            args=(mic_id, cfg["listen_port"], cfg["forward_port"]),
            daemon=True,
        ).start()

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()