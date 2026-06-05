/**
 * AI English Speaking Practice — Frontend App
 *
 * ASR: True streaming via WebSocket (AudioContext → PCM → base64 → WS)
 * TTS: Text → Speech (via REST)
 */

const API_BASE = "http://localhost:8000";
const WS_BASE  = "ws://localhost:8000";

// ===================================================================
// State
// ===================================================================
const state = {
    stream: null,
    audioCtx: null,
    processor: null,
    analyser: null,
    ws: null,

    sessionActive: false,
    isSpeaking: false,

    sentenceIndex: 0,
    currentPartialId: null,  // index of the "partial" display item
    waveformTimer: null,

    // TTS
    ttsAudio: null,
};

// ===================================================================
// DOM refs
// ===================================================================
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const btnStart      = $("#btn-vad-start");
const btnStop       = $("#btn-vad-stop");
const asrStatus     = $("#asr-status");
const asrTimer      = $("#asr-timer");
const canvas        = $("#waveform");
const transcript    = $("#transcript");

const btnSpeak      = $("#btn-speak");
const btnStopTTS    = $("#btn-stop-tts");
const ttsText       = $("#tts-text");
const ttsVoice      = $("#tts-voice");
const ttsStatus     = $("#tts-status");
const ttsLog        = $("#tts-log");

const quickBtns     = $$(".btn-sm[data-text]");
const btnE2E        = $("#btn-end2end");

// ===================================================================
// Utility
// ===================================================================
function log(msg) {
    const e = document.createElement("div");
    e.className = "log-entry";
    e.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    ttsLog.appendChild(e);
    ttsLog.scrollTop = ttsLog.scrollHeight;
    const ph = ttsLog.querySelector(".placeholder");
    if (ph) ph.remove();
}

function elapsedStr(ms) {
    const s = Math.floor(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function updateStatus(mode) {
    const labels = {
        idle: "空闲", waiting: "等待说话...", speaking: "说话中",
        partial: "实时识别中", processing: "识别中",
    };
    asrStatus.textContent = labels[mode] || mode;
    const cls = { speaking: "recording", partial: "processing", processing: "processing" };
    asrStatus.className = "status-indicator " + (cls[mode] || "");
}

// ===================================================================
// Waveform
// ===================================================================
function startWaveform() {
    if (!canvas || !state.analyser) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    const bufLen = state.analyser.frequencyBinCount;
    const data = new Uint8Array(bufLen);

    function draw() {
        if (!state.sessionActive) { ctx.clearRect(0, 0, w, h); return; }
        state.waveformTimer = requestAnimationFrame(draw);
        state.analyser.getByteTimeDomainData(data);
        ctx.fillStyle = "#0f1119"; ctx.fillRect(0, 0, w, h);
        ctx.lineWidth = 2;
        ctx.strokeStyle = state.isSpeaking ? "#00c9a7" : "#6c5ce7";
        ctx.beginPath();
        const sw = w / bufLen;
        for (let i = 0; i < bufLen; i++) {
            const y = (data[i] / 128.0) * (h / 2);
            ctx.lineTo(i * sw, y);
        }
        ctx.lineTo(w, h / 2);
        ctx.stroke();

        // volume bar
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += (data[i] / 128.0 - 1) ** 2;
        const vol = Math.min(1, Math.sqrt(sum / data.length) * 3);
        ctx.fillStyle = state.isSpeaking ? "#00c9a7" : "#2a2e45";
        ctx.fillRect(w - 6, h - vol * h, 4, vol * h);
    }
    draw();
}

function stopWaveform() {
    if (state.waveformTimer) cancelAnimationFrame(state.waveformTimer);
    const ctx = canvas?.getContext("2d");
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}

// ===================================================================
// WebSocket streaming session
// ===================================================================
async function startStreamSession() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        state.stream = stream;
        state.sessionActive = true;
        state.sentenceIndex = 0;
        state.currentPartialId = null;

        btnStart.disabled = true;
        btnStop.disabled = false;
        btnStart.classList.add("recording");
        transcript.innerHTML = '<span class="placeholder">说话后实时显示识别文字...</span>';
        updateStatus("waiting");

        // Timer
        const startTime = Date.now();
        state.timerInt = setInterval(() => {
            asrTimer.textContent = elapsedStr(Date.now() - startTime);
        }, 500);

        // AudioContext — force 16kHz for Whisper
        state.audioCtx = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: 16000,
        });
        const source = state.audioCtx.createMediaStreamSource(stream);

        // Analyser for waveform
        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 256;
        source.connect(state.analyser);
        startWaveform();

        // ScriptProcessor → raw PCM
        state.processor = state.audioCtx.createScriptProcessor(4096, 1, 1);
        source.connect(state.processor);
        state.processor.connect(state.audioCtx.destination);

        // Open WebSocket
        const sessionId = "session_" + Date.now();
        state.ws = new WebSocket(`${WS_BASE}/ws/stream/${sessionId}`);
        state.ws.binaryType = "arraybuffer";

        state.ws.onopen = () => {
            log("WebSocket 连接已建立，开始流式传输");
            updateStatus("waiting");

            // Start sending PCM chunks
            state.processor.onaudioprocess = (e) => {
                if (!state.sessionActive || state.ws.readyState !== WebSocket.OPEN) return;
                const input = e.inputBuffer.getChannelData(0); // Float32
                // Convert to PCM16
                const pcm16 = new Int16Array(input.length);
                for (let i = 0; i < input.length; i++) {
                    const s = Math.max(-1, Math.min(1, input[i]));
                    pcm16[i] = s < 0 ? s * 32768 : s * 32767;
                }
                // base64 encode
                const bytes = new Uint8Array(pcm16.buffer);
                let binary = "";
                for (let i = 0; i < bytes.length; i++) {
                    binary += String.fromCharCode(bytes[i]);
                }
                const b64 = btoa(binary);
                state.ws.send(JSON.stringify({ type: "audio", data: b64 }));
            };
        };

        state.ws.onmessage = (evt) => {
            const msg = JSON.parse(evt.data);
            switch (msg.type) {
                case "partial":
                    handlePartial(msg);
                    break;
                case "final":
                    handleFinal(msg);
                    break;
                case "reset":
                    state.currentPartialId = null;
                    break;
                case "pong":
                    break;
                case "end":
                    log("会话结束");
                    break;
                case "error":
                    log("服务端错误: " + (msg.message || ""));
                    break;
            }
        };

        state.ws.onerror = () => {
            log("WebSocket 连接错误");
        };

        state.ws.onclose = () => {
            log("WebSocket 连接已关闭");
            if (state.sessionActive) stopStreamSession();
        };

    } catch (err) {
        console.error("Stream start error:", err);
        stopStreamSession();
        asrStatus.textContent = "麦克风访问被拒绝";
        asrStatus.className = "status-indicator";
    }
}

function stopStreamSession() {
    state.sessionActive = false;

    // Send stop signal
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "stop" }));
        // Wait a tiny bit then close
        setTimeout(() => {
            if (state.ws) state.ws.close();
        }, 500);
    }

    // Stop processor
    if (state.processor) {
        state.processor.disconnect();
        state.processor = null;
    }

    // Stop stream
    if (state.stream) {
        state.stream.getTracks().forEach(t => t.stop());
        state.stream = null;
    }

    // Close audio context
    if (state.audioCtx) {
        state.audioCtx.close().catch(() => {});
        state.audioCtx = null;
    }

    stopWaveform();

    if (state.timerInt) {
        clearInterval(state.timerInt);
        state.timerInt = null;
    }

    btnStart.disabled = false;
    btnStop.disabled = true;
    btnStart.classList.remove("recording");
    asrStatus.textContent = "已结束";
    asrStatus.className = "status-indicator done";
    asrTimer.textContent = "00:00";
}

// ===================================================================
// Stream message handlers
// ===================================================================
function handlePartial(msg) {
    state.isSpeaking = true;
    updateStatus("partial");

    // Update or create partial item
    if (state.currentPartialId) {
        updateTranscriptItem(state.currentPartialId, msg.text, "partial");
    } else {
        state.currentPartialId = ++state.sentenceIndex;
        addTranscriptItem(state.currentPartialId, msg.text, "partial");
    }
}

function handleFinal(msg) {
    state.isSpeaking = false;
    const idx = ++state.sentenceIndex;
    const prevPartialId = state.currentPartialId;
    state.currentPartialId = null;

    addTranscriptItem(
        idx, msg.text, "done",
        msg.processing_s, msg.pronunciation, msg.fluency
    );

    // Remove the previous partial item
    if (prevPartialId) {
        const prev = transcript.querySelector(`[data-index="${prevPartialId}"]`);
        if (prev) prev.remove();
    }

    log(`#${idx}: "${msg.text.substring(0, 50)}..." P=${msg.pronunciation} F=${msg.fluency} (${msg.processing_s}s)`);
    updateStatus("waiting");
}

// ===================================================================
// Transcript display
// ===================================================================
function addTranscriptItem(index, text, status, processing_s, pron, flu) {
    const div = document.createElement("div");
    div.className = `transcript-item ${status}`;
    div.dataset.index = index;
    div.innerHTML = `
        <span class="transcript-num">#${index}</span>
        <span class="transcript-text">${text}</span>
        <div class="item-meta"></div>
    `;
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
    const ph = transcript.querySelector(".placeholder");
    if (ph) ph.remove();

    // Add scores if provided
    if (status === "done" && (pron || flu || processing_s)) {
        const meta = div.querySelector(".item-meta");
        const parts = [];
        if (processing_s) parts.push(`<span class="item-time">${processing_s}s</span>`);
        if (pron > 0) {
            const c = pron >= 70 ? "green" : pron >= 50 ? "yellow" : "red";
            parts.push(`<span class="item-score ${c}">发音 ${pron}</span>`);
        }
        if (flu > 0) {
            const c = flu >= 70 ? "green" : flu >= 50 ? "yellow" : "red";
            parts.push(`<span class="item-score ${c}">流利 ${flu}</span>`);
        }
        meta.innerHTML = parts.join(" ");
    }

}

function updateTranscriptItem(index, text, status) {
    let item = transcript.querySelector(`[data-index="${index}"]`);
    if (!item) {
        // Create on the fly
        return addTranscriptItem(index, text, status);
    }
    item.className = `transcript-item ${status}`;
    item.querySelector(".transcript-text").textContent = text;
    transcript.scrollTop = transcript.scrollHeight;
}

// ===================================================================
// TTS — Text to Speech
// ===================================================================
async function speakText(text, voice) {
    if (!text || !text.trim()) { log("请输入文字"); return; }
    btnSpeak.disabled = true;
    btnStopTTS.disabled = false;
    ttsStatus.textContent = "合成中...";
    ttsStatus.className = "status-indicator processing";

    try {
        log(`合成: "${text.substring(0, 40)}..." (${voice})`);
        const resp = await fetch(
            `${API_BASE}/api/tts/speak?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(voice)}`
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        const pt = resp.headers.get("X-Processing-Time") || "?";
        log(`完成 (${pt}s, ${blob.size} bytes)`);

        const audio = new Audio(URL.createObjectURL(blob));
        state.ttsAudio = audio;
        audio.onended = () => {
            ttsStatus.textContent = "播放完成"; ttsStatus.className = "status-indicator done";
            btnSpeak.disabled = false; btnStopTTS.disabled = true;
        };
        audio.onplay = () => {
            ttsStatus.textContent = "播放中"; ttsStatus.className = "status-indicator recording";
        };
        audio.play().catch(err => {
            log("自动播放失败: " + err.message);
            btnSpeak.disabled = false; btnStopTTS.disabled = true;
            ttsStatus.textContent = "点击播放"; ttsStatus.className = "status-indicator";
        });
    } catch (err) {
        log("合成失败: " + err.message);
        btnSpeak.disabled = false; btnStopTTS.disabled = true;
        ttsStatus.textContent = "错误"; ttsStatus.className = "status-indicator";
    }
}

function stopTTS() {
    if (state.ttsAudio) { state.ttsAudio.pause(); state.ttsAudio = null; }
    btnSpeak.disabled = false; btnStopTTS.disabled = true;
    ttsStatus.textContent = "已停止"; ttsStatus.className = "status-indicator";
}

// ===================================================================
// Event Bindings
// ===================================================================
btnStart.addEventListener("click", startStreamSession);
btnStop.addEventListener("click", stopStreamSession);
btnSpeak.addEventListener("click", () => speakText(ttsText.value, ttsVoice.value));
btnStopTTS.addEventListener("click", stopTTS);
quickBtns.forEach(b => b.addEventListener("click", () => {
    ttsText.value = b.dataset.text;
    speakText(b.dataset.text, ttsVoice.value);
}));
ttsText.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); btnSpeak.click(); }
});

// End-to-end test
btnE2E.addEventListener("click", async () => {
    if (state.sessionActive) return;
    log("=== 端到端测试 ===");
    btnE2E.disabled = true; btnE2E.textContent = "测试中...";
    await startStreamSession();
    setTimeout(() => {
        if (state.sessionActive) stopStreamSession();
        const items = transcript.querySelectorAll(".transcript-item.done");
        const last = items[items.length - 1];
        if (last) {
            const text = last.querySelector(".transcript-text").textContent;
            if (text && text.length > 3) speakText(text, ttsVoice.value);
        }
        btnE2E.disabled = false; btnE2E.textContent = "端到端测试";
    }, 10000);
});

// ===================================================================
// Init
// ===================================================================
log("流式传输模式就绪 — 点击「开始对话」，说话时实时显示文字");
log("WebSocket 实时传输，服务器每 0.8s 返回局部结果");
