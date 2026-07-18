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
  private stream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private recordedChunks: Float32Array[] = [];
  private recordingState = false;
  private _sampleRate = 0;
  private workletReady: Promise<void> | null = null;

  async startRecording(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      this.audioContext = new AudioContext();
      this._sampleRate = this.audioContext.sampleRate;

      this.workletReady = this.audioContext.audioWorklet.addModule(getWorkletBlobUrl());
      await this.workletReady;

      this.sourceNode = this.audioContext.createMediaStreamSource(this.stream);

      const workletNode = new AudioWorkletNode(this.audioContext, 'pcm-processor');
      this.recordedChunks = [];

      workletNode.port.onmessage = (event) => {
        if (event.data.type === 'audio' && this.recordingState) {
          this.recordedChunks.push(new Float32Array(event.data.samples));
        }
      };

      this.sourceNode.connect(workletNode);
      workletNode.connect(this.audioContext.destination);

      workletNode.port.postMessage({ type: 'start' });
      this.recordingState = true;
    } catch (error) {
      console.error('Failed to start recording:', error);
      this.cleanupStream();
      throw new Error('Failed to access microphone. Please check permissions.');
    }
  }

  async stopRecording(): Promise<Blob> {
    if (!this.recordingState) {
      throw new Error('No active recording to stop');
    }

    this.recordingState = false;

    const chunks = this.recordedChunks;
    const sampleRate = this._sampleRate;

    this.recordedChunks = [];
    this.cleanupStream();

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
    this.cleanupStream();
  }

  private cleanupStream(): void {
    if (this.sourceNode) {
      this.sourceNode.disconnect();
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