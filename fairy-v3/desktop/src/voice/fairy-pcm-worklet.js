class FairyPcmRingProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ring = new Float32Array(24_000 * 120);
    this.readIndex = 0;
    this.writeIndex = 0;
    this.available = 0;
    this.phase = 0;
    this.sourceRate = 24_000;
    this.rate = 1;
    this.volume = 1;
    this.sessionId = null;
    this.writtenTotal = 0;
    this.readTotal = 0;
    this.completions = [];
    this.port.onmessage = (event) => this.handleMessage(event.data);
  }

  handleMessage(message) {
    if (message.type === "configure") {
      this.sessionId = message.sessionId;
      this.sourceRate = message.sampleRate;
      this.rate = message.rate;
      this.volume = message.volume;
      return;
    }
    if (message.type === "append") {
      this.append(message.pcm, message.sessionId);
      return;
    }
    if (message.type === "complete") {
      this.completions.push({
        sessionId: message.sessionId,
        boundary: this.writtenTotal,
      });
      return;
    }
    if (message.type === "clear") {
      this.clear();
      this.sessionId = null;
    }
  }

  clear() {
    this.readIndex = 0;
    this.writeIndex = 0;
    this.available = 0;
    this.phase = 0;
    this.writtenTotal = 0;
    this.readTotal = 0;
    this.completions = [];
  }

  append(buffer, sessionId) {
    const pcm = new Int16Array(buffer);
    if (pcm.length > this.ring.length - this.available) {
      this.port.postMessage({ type: "overflow", sessionId });
      return;
    }
    for (let index = 0; index < pcm.length; index += 1) {
      this.ring[this.writeIndex] = pcm[index] / 32768;
      this.writeIndex = (this.writeIndex + 1) % this.ring.length;
    }
    this.available += pcm.length;
    this.writtenTotal += pcm.length;
  }

  process(_inputs, outputs) {
    const output = outputs[0][0];
    const step = (this.sourceRate / sampleRate) * this.rate;
    for (let index = 0; index < output.length; index += 1) {
      if (this.available === 0) {
        output[index] = 0;
        continue;
      }
      const current = this.ring[this.readIndex];
      const next = this.available > 1
        ? this.ring[(this.readIndex + 1) % this.ring.length]
        : current;
      output[index] = (current + (next - current) * this.phase) * this.volume;
      this.phase += step;
      const consumed = Math.min(Math.floor(this.phase), this.available);
      this.phase -= consumed;
      this.readIndex = (this.readIndex + consumed) % this.ring.length;
      this.available -= consumed;
      this.readTotal += consumed;
    }
    while (
      this.completions.length > 0 &&
      this.readTotal >= this.completions[0].boundary
    ) {
      const completion = this.completions.shift();
      this.port.postMessage({ type: "drained", sessionId: completion.sessionId });
    }
    return true;
  }
}

registerProcessor("fairy-pcm-ring", FairyPcmRingProcessor);
