/**
 * PCM audio capture via AudioWorklet → WAV → base64 for input_audio API.
 */

const WORKLET_PROCESSOR_CODE = `
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.isRecording = false;
    this.port.onmessage = (event) => {
      if (event.data.type === 'start') {
        this.isRecording = true;
      } else if (event.data.type === 'stop') {
        this.isRecording = false;
        this.port.postMessage({ type: 'stopped' });
      }
    };
  }

  process(inputs, outputs, parameters) {
    if (!this.isRecording) return true;
    const input = inputs[0];
    if (input && input[0]) {
      this.port.postMessage({ type: 'audio', samples: Float32Array.from(input[0]) });
    }
    return true;
  }
}

registerProcessor('pcm-processor', PCMProcessor);
`;

let workletBlobUrl: string | null = null;

function getWorkletBlobUrl(): string {
  if (workletBlobUrl) return workletBlobUrl;
  const blob = new Blob([WORKLET_PROCESSOR_CODE], { type: 'application/javascript' });
  workletBlobUrl = URL.createObjectURL(blob);
  return workletBlobUrl;
}

export class AudioRecorder {
  // One long-lived AudioContext + MediaStream for the recorder's entire
  // lifetime. Chrome throws "AudioContext encountered an error from the audio
  // device or the WebAudio renderer" when AudioContexts are created and closed
  // in rapid succession (the audio hardware doesn't release fast enough, and
  // subsequent contexts enter an errored state whose worklet process() never
  // fires -> "No audio data recorded"). Keeping both hot and just toggling the
  // capture flag between recordings is the pattern used by WhatsApp/Discord web
  // voice. The mic stays warm while the page is open and is fully released by
  // destroy() on teardown.
  private stream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private recordedChunks: Float32Array[] = [];
  private recordingState = false;
  private _sampleRate = 0;
  private initialized = false;

  private async init(): Promise<void> {
    if (this.initialized) return;

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.stream = stream;

    const ctx = new AudioContext();
    this.audioContext = ctx;
    this._sampleRate = ctx.sampleRate;

    // Per autoplay policy, a fresh AudioContext can start in "suspended" state.
    // When suspended, the AudioWorkletProcessor.process() is never called, so no
    // audio chunks are ever produced -> "No audio data recorded". Resume explicitly.
    if (ctx.state === 'suspended') {
      await ctx.resume();
    }
    if (ctx.state !== 'running') {
      throw new Error(`AudioContext not running (state: ${ctx.state}). Microphone may be unavailable.`);
    }

    await ctx.audioWorklet.addModule(getWorkletBlobUrl());

    this.sourceNode = ctx.createMediaStreamSource(stream);

    // One persistent worklet node. We toggle its recording flag via postMessage
    // rather than tearing it down and recreating it per recording.
    const workletNode = new AudioWorkletNode(ctx, 'pcm-processor');
    this.workletNode = workletNode;
    workletNode.port.onmessage = (event) => {
      if (event.data.type === 'audio' && this.recordingState) {
        this.recordedChunks.push(new Float32Array(event.data.samples));
      }
    };

    this.sourceNode.connect(workletNode);
    // Do NOT connect workletNode to ctx.destination: that would route the
    // microphone back to the speakers (echo/feedback) and is unnecessary for capture.

    this.initialized = true;
  }

  async startRecording(): Promise<void> {
    try {
      await this.init();
      this.recordedChunks = [];
      this.recordingState = true;
      this.workletNode?.port.postMessage({ type: 'start' });
    } catch (error) {
      console.error('Failed to start recording:', error);
      this.destroy();
      throw new Error('Failed to access microphone. Please check permissions.');
    }
  }

  async stopRecording(): Promise<Blob> {
    if (!this.recordingState) {
      throw new Error('No active recording to stop');
    }

    this.recordingState = false;

    // Ask the worklet to stop and wait briefly for any in-flight audio chunks
    // to arrive. Without this drain, chunks captured in the last few
    // render quanta can still be in flight when we check recordedChunks,
    // and very short recordings fail with "No audio data recorded".
    const node = this.workletNode;
    if (node) {
      try {
        node.port.postMessage({ type: 'stop' });
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, 120);
          const onStopped = (ev: MessageEvent) => {
            if (ev.data?.type === 'stopped') {
              clearTimeout(timer);
              node.port.removeEventListener('message', onStopped);
              resolve();
            }
          };
          node.port.addEventListener('message', onStopped);
        });
      } catch {}
    }

    const chunks = this.recordedChunks;
    const sampleRate = this._sampleRate;
    this.recordedChunks = [];

    if (chunks.length === 0) {
      throw new Error('No audio data recorded');
    }

    const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const samples = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
      samples.set(chunk, offset);
      offset += chunk.length;
    }

    return pcmToWav(samples, sampleRate, 1);
  }

  isRecording(): boolean {
    return this.recordingState;
  }

  cancelRecording(): void {
    this.recordingState = false;
    this.recordedChunks = [];
  }

  /** Fully release the mic stream + AudioContext. Call on teardown. */
  destroy(): void {
    this.recordingState = false;
    this.recordedChunks = [];
    if (this.workletNode) {
      try { this.workletNode.port.onmessage = null; } catch {}
      try { this.workletNode.disconnect(); } catch {}
      this.workletNode = null;
    }
    if (this.sourceNode) {
      try { this.sourceNode.disconnect(); } catch {}
      this.sourceNode = null;
    }
    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) {
        track.stop();
      }
      this.stream = null;
    }
    this.initialized = false;
  }
}

function pcmToWav(samples: Float32Array, sampleRate: number, numberOfChannels: number): Blob {
  const bytesPerSample = 2;
  const blockAlign = numberOfChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = samples.length * blockAlign;
  const bufferSize = 44 + dataSize;

  const arrayBuffer = new ArrayBuffer(bufferSize);
  const view = new DataView(arrayBuffer);

  const writeString = (offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, bufferSize - 8, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numberOfChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true);
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);

  const pcm = new Int16Array(arrayBuffer, 44, samples.length * numberOfChannels);
  let p = 0;
  for (let i = 0; i < samples.length; i++) {
    for (let c = 0; c < numberOfChannels; c++) {
      let s = samples[i];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      pcm[p++] = s * 0x7fff;
    }
  }

  return new Blob([arrayBuffer], { type: 'audio/wav' });
}

export async function convertToWav(audioBlob: Blob): Promise<Blob> {
  try {
    if (audioBlob.type.includes('wav')) {
      return audioBlob;
    }

    const arrayBuffer = await audioBlob.arrayBuffer();
    const audioContext = new AudioContext();

    try {
      const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
      return audioBufferToWav(audioBuffer);
    } finally {
      audioContext.close();
    }
  } catch (error) {
    console.error('Failed to convert audio to WAV:', error);
    return audioBlob;
  }
}

function audioBufferToWav(buffer: AudioBuffer): Blob {
  const length = buffer.length;
  const numberOfChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const bytesPerSample = 2;
  const blockAlign = numberOfChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = length * blockAlign;
  const bufferSize = 44 + dataSize;

  const arrayBuffer = new ArrayBuffer(bufferSize);
  const view = new DataView(arrayBuffer);

  const writeString = (offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, bufferSize - 8, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numberOfChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true);
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);

  const channels: Float32Array[] = new Array(numberOfChannels);
  for (let c = 0; c < numberOfChannels; c++) {
    channels[c] = buffer.getChannelData(c);
  }

  const pcm = new Int16Array(arrayBuffer, 44, length * numberOfChannels);
  let p = 0;
  for (let i = 0; i < length; i++) {
    for (let c = 0; c < numberOfChannels; c++) {
      let s = channels[c][i];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      pcm[p++] = s * 0x7fff;
    }
  }

  return new Blob([arrayBuffer], { type: 'audio/wav' });
}

export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const base64 = result.split(',')[1] || result;
      resolve(base64);
    };
    reader.onerror = () => reject(new Error('Failed to convert blob to base64'));
    reader.readAsDataURL(blob);
  });
}

export async function transcribeAudio(
  audioBlob: Blob,
  endpoint: string,
): Promise<string> {
  const wavBlob = await convertToWav(audioBlob);
  const file = new File([wavBlob], 'recording.wav', { type: 'audio/wav' });

  const formData = new FormData();
  formData.append('file', file);
  formData.append('temperature', '0.0');
  formData.append('response_format', 'json');

  const response = await fetch(endpoint, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Transcription failed: ${response.status} ${response.statusText}`);
  }

  const result = await response.json();
  return (result.text ?? '').trim();
}

export function isAudioRecordingSupported(): boolean {
  return !!(
    typeof navigator !== 'undefined' &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    typeof window !== 'undefined' &&
    window.AudioContext &&
    window.AudioWorkletNode
  );
}