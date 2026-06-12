const TARGET_RATE = 24000;
const FRAME_MS = 40;
const FRAME_SAMPLES = (TARGET_RATE * FRAME_MS) / 1000;

class VoiceCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputRate = sampleRate;
    this.ratio = this.inputRate / TARGET_RATE;
    this.buffer = [];
    this.enabled = true;
    this.port.onmessage = (event) => {
      if (event.data && event.data.type === "enable") {
        this.enabled = Boolean(event.data.value);
      }
    };
  }

  process(inputs) {
    if (!this.enabled) return true;
    const input = inputs[0];
    if (!input || !input[0] || !input[0].length) return true;
    const channel = input[0];
    for (let i = 0; i < channel.length; i += this.ratio) {
      this.buffer.push(channel[Math.floor(i)] || 0);
      if (this.buffer.length >= FRAME_SAMPLES) this.flushFrame();
    }
    return true;
  }

  flushFrame() {
    const samples = this.buffer.slice(0, FRAME_SAMPLES);
    this.buffer = this.buffer.slice(FRAME_SAMPLES);
    const pcm = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
  }
}

registerProcessor("voice-capture", VoiceCaptureProcessor);
