// WEBSOCKET THINGS
const ws_whisper = new WebSocket("ws://localhost:8000/ws/whisper");
const ws_wave = new WebSocket("ws://localhost:8000/ws/wave");

const mic_outputs = {
    MIC1: document.querySelector(".mic1-ts"),
    MIC2: document.querySelector(".mic2-ts"),
    MIC3: document.querySelector(".mic3-ts"),
    MIC4: document.querySelector(".mic4-ts"),
    MIC5: document.querySelector(".mic5-ts"),
};

const MAX_LINES = 12;
const NO_TEXT_TIMEOUT = 10000;

let mic_lines = {
    MIC1: [],
    MIC2: [],
    MIC3: [],
    MIC4: [],
    MIC5: [],
};

let timeoutIds = {
    MIC1: null,
    MIC2: null,
    MIC3: null,
    MIC4: null,
    MIC5: null,
};

let waveforms = {
    MIC1: [],
    MIC2: [],
    MIC3: [],
    MIC4: [],
    MIC5: [],
};

let selectedMic = "MIC1";
let displayedWaveform = [];
let incomingQueue = [];

ws_whisper.onopen = () => console.log("✅ Whisper WebSocket connected");
ws_whisper.onerror = (error) => console.error("❌ Whisper WebSocket error:", error);
ws_whisper.onclose = () => console.log("🔌 Whisper WebSocket disconnected");

ws_wave.onopen = () => console.log("✅ Wave WebSocket connected");
ws_wave.onerror = (error) => console.error("❌ Wave WebSocket error:", error);
ws_wave.onclose = () => console.log("🔌 Wave WebSocket disconnected");

function resetNoTextTimer(micId) {
    if (timeoutIds[micId]) clearTimeout(timeoutIds[micId]);

    timeoutIds[micId] = setTimeout(() => {
        mic_lines[micId] = [];

        if (mic_outputs[micId]) {
            mic_outputs[micId].textContent = "no conversation detected ...";
        }
    }, NO_TEXT_TIMEOUT);
}

function splitTextIntoLines(text, maxLength = 45) {
    const words = text.split(" ");
    const lines = [];
    let current = "";

    for (const word of words) {
        if ((current + " " + word).trim().length > maxLength) {
            if (current) lines.push(current);
            current = word;
        } else {
            current = (current + " " + word).trim();
        }
    }

    if (current) lines.push(current);
    return lines;
}

// ============================================================
// SELECTED MIC UI
// ============================================================

function setSelectedMic(micId) {
    selectedMic = String(micId).toUpperCase();

    console.log("selected mic:", selectedMic);

    for (let i = 1; i <= 5; i++) {
        const micBox = document.getElementById(`mic${i}`);
        const transcript = document.querySelector(`.mic${i}-ts`);
        const label = document.querySelector(`.mic${i}-selected`);

        if (micBox) micBox.classList.remove("selected");
        if (transcript) transcript.classList.remove("active-transcript");
        if (label) label.classList.remove("active-selected");
    }

    const selectedBox = document.getElementById(selectedMic.toLowerCase());
    const selectedTranscript = document.querySelector(`.${selectedMic.toLowerCase()}-ts`);
    const selectedLabel = document.querySelector(`.${selectedMic.toLowerCase()}-selected`);
    const noMic = document.querySelector(".nomic-selected");
    const noText = document.querySelector(".no-ts");

    if (selectedBox) selectedBox.classList.add("selected");
    if (selectedTranscript) selectedTranscript.classList.add("active-transcript");
    if (selectedLabel) selectedLabel.classList.add("active-selected");
    if (noMic) noMic.classList.add("hidden");
    if (noText) noText.style.display = "none";

    displayedWaveform = new Array(WAVE_BUFFER_SIZE).fill(0);
    incomingQueue = [...(waveforms[selectedMic] || [])];

    if (!running) {
        running = true;
        drawWave();
    }
}

// ============================================================
// WHISPER TEXT SOCKET
// ============================================================

ws_whisper.onmessage = function(event) {
    let data;

    try {
        data = JSON.parse(event.data);
    } catch (err) {
        console.error("Invalid whisper JSON:", event.data);
        return;
    }

    const micId = String(data.mic).toUpperCase();
    const text = data.text;

    if (micId === "SYSTEM") {
        console.log("System message:", data);

        if (data.selectedMic) {
            setSelectedMic(data.selectedMic);
        }

        return;
    }

    if (!(micId in mic_outputs)) {
        console.warn("Unknown mic:", micId);
        return;
    }

    if (!text) return;

    const wrappedLines = splitTextIntoLines(text, 45);

    for (const line of wrappedLines) {
        mic_lines[micId].push("> " + line);
    }

    if (mic_lines[micId].length > MAX_LINES) {
        mic_lines[micId] = mic_lines[micId].slice(-MAX_LINES);
    }

    mic_outputs[micId].innerHTML = mic_lines[micId].join("<br>");
    resetNoTextTimer(micId);
};

// ============================================================
// WAVEFORM THINGS
// ============================================================

const canvas = document.getElementById("wave-drawing");
const ctx = canvas.getContext("2d");

let animationId = null;
let running = false;

const WAVE_BUFFER_SIZE = 400;
const WAVE_SPEED = 3;
const WAVE_AMPLITUDE = 0.45;

const NOISE_GATE = 0.001; //0.025
const QUIET_GAIN = 1.2; //0.6 1.2
const LOUD_GAIN = 3.2;
const SMOOTH_AMOUNT = 0.35;
const CURVE_LINE = true;

// ============================================================
// WAVEFORM SOCKET
// ============================================================

ws_wave.onmessage = (event) => {
    let data;

    try {
        data = JSON.parse(event.data);
    } catch (err) {
        console.error("Invalid wave JSON:", event.data);
        return;
    }

    const micId = String(data.mic).toUpperCase();
    const waveform = data.waveform;

    console.log("wave received:", micId, waveform ? waveform.length : 0);

    if (!(micId in waveforms)) {
        console.warn("Unknown waveform mic:", micId);
        return;
    }

    if (!Array.isArray(waveform)) {
        console.warn("Waveform is not an array:", waveform);
        return;
    }

    waveforms[micId] = waveform;

    if (micId === selectedMic) {
        for (const sample of waveform) {
            incomingQueue.push(sample);
        }

        if (incomingQueue.length > 2000) {
            incomingQueue = incomingQueue.slice(-2000);
        }

        if (!running) {
            running = true;
            drawWave();
        }
    }
};

// ============================================================
// WAVE DRAWING
// ============================================================

function smoothWaveform(arr) {
    const smoothed = [...arr];

    for (let i = 1; i < arr.length - 1; i++) {
        smoothed[i] =
            arr[i - 1] * 0.25 +
            arr[i] * 0.5 +
            arr[i + 1] * 0.25;
    }

    return smoothed;
}

function shapeSample(sample) {
    const abs = Math.abs(sample);

    if (abs < NOISE_GATE) {
        return 0;
    }

    let cleaned = (abs - NOISE_GATE) / (1 - NOISE_GATE);
    cleaned = Math.pow(cleaned, 0.65);

    const gain = QUIET_GAIN + cleaned * (LOUD_GAIN - QUIET_GAIN);
    const shaped = Math.sign(sample) * cleaned * gain;

    return Math.max(-1, Math.min(1, shaped));
}

function drawWave() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (displayedWaveform.length === 0) {
        displayedWaveform = new Array(WAVE_BUFFER_SIZE).fill(0);
    }

    for (let i = 0; i < WAVE_SPEED; i++) {
        displayedWaveform.shift();

        let nextSample = 0;

        if (incomingQueue.length > 0) {
            nextSample = incomingQueue.shift();
        }

        nextSample = shapeSample(nextSample);
        displayedWaveform.push(nextSample);
    }

    let drawData = smoothWaveform(displayedWaveform);

    for (let i = 1; i < drawData.length - 1; i++) {
        drawData[i] =
            drawData[i] * (1 - SMOOTH_AMOUNT) +
            ((drawData[i - 1] + drawData[i + 1]) / 2) * SMOOTH_AMOUNT;
    }

    const midY = canvas.height / 2;
    const amplitude = canvas.height * WAVE_AMPLITUDE;

    ctx.beginPath();

    if (CURVE_LINE) {
        for (let i = 0; i < drawData.length - 1; i++) {
            const x1 = (i / (drawData.length - 1)) * canvas.width;
            const y1 = midY - drawData[i] * amplitude;

            const x2 = ((i + 1) / (drawData.length - 1)) * canvas.width;
            const y2 = midY - drawData[i + 1] * amplitude;

            const midX = (x1 + x2) / 2;
            const midPointY = (y1 + y2) / 2;

            if (i === 0) ctx.moveTo(x1, y1);

            ctx.quadraticCurveTo(x1, y1, midX, midPointY);
        }
    } else {
        for (let i = 0; i < drawData.length; i++) {
            const x = (i / (drawData.length - 1)) * canvas.width;
            const y = midY - drawData[i] * amplitude;

            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
    }

    ctx.strokeStyle = "white";
    ctx.lineWidth = 2;
    ctx.stroke();

    animationId = requestAnimationFrame(drawWave);
}

// ============================================================
// INITIAL STATE
// ============================================================

window.addEventListener("load", () => {
    setSelectedMic("MIC1");

    if (!running) {
        running = true;
        drawWave();
    }
});