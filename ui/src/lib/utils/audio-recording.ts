/**
 * AudioRecorder - Browser-based audio recording with MediaRecorder API.
 * Based on the llama.cpp server UI implementation.
 */
export class AudioRecorder {
  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];
  private stream: MediaStream | null = null;
  private recordingState = false;

  async startRecording(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      this.initializeRecorder(this.stream);
      this.audioChunks = [];
      this.mediaRecorder!.start(100);
      this.recordingState = true;
    } catch (error) {
      console.error('Failed to start recording:', error);
      throw new Error('Failed to access microphone. Please check permissions.');
    }
  }

  async stopRecording(): Promise<Blob> {
    return new Promise((resolve, reject) => {
      const recorder = this.mediaRecorder;
      const chunks = this.audioChunks;
      const stream = this.stream;

      if (!recorder || recorder.state === 'inactive') {
        reject(new Error('No active recording to stop'));
        return;
      }

      // Detach instance state so a new recording can start without races
      this.mediaRecorder = null;
      this.audioChunks = [];
      this.stream = null;
      this.recordingState = false;

      recorder.onstop = () => {
        const audioBlob = new Blob(chunks, {
          type: recorder.mimeType || 'audio/wav',
        });

        if (stream) {
          for (const track of stream.getTracks()) {
            track.stop();
          }
        }

        resolve(audioBlob);
      };

      recorder.onerror = () => {
        if (stream) {
          for (const track of stream.getTracks()) {
            track.stop();
          }
        }
        reject(new Error('Recording failed'));
      };

      recorder.stop();
    });
  }

  isRecording(): boolean {
    return this.recordingState;
  }

  cancelRecording(): void {
    const recorder = this.mediaRecorder;
    const stream = this.stream;

    this.mediaRecorder = null;
    this.audioChunks = [];
    this.stream = null;
    this.recordingState = false;

    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null;
      recorder.onerror = null;
      recorder.stop();
    }

    if (stream) {
      for (const track of stream.getTracks()) {
        track.stop();
      }
    }
  }

  private initializeRecorder(stream: MediaStream): void {
    const options: MediaRecorderOptions = {};

    // Prefer WAV, fall back to WebM/Opus, then WebM, then MP4
    if (MediaRecorder.isTypeSupported('audio/wav')) {
      options.mimeType = 'audio/wav';
    } else if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
      options.mimeType = 'audio/webm;codecs=opus';
    } else if (MediaRecorder.isTypeSupported('audio/webm')) {
      options.mimeType = 'audio/webm';
    } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
      options.mimeType = 'audio/mp4';
    }

    this.mediaRecorder = new MediaRecorder(stream, options);

    this.mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        this.audioChunks.push(event.data);
      }
    };

    this.mediaRecorder.onstop = () => {
      this.recordingState = false;
    };

    this.mediaRecorder.onerror = () => {
      this.recordingState = false;
    };
  }
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
  const bytesPerSample = 2; // 16-bit
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
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, numberOfChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
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

export async function transcribeAudio(
  audioBlob: Blob,
  endpoint: string,
): Promise<string> {
  const wavBlob = await convertToWav(audioBlob);
  const file = new File([wavBlob], 'recording.wav', { type: 'audio/wav' });

  const formData = new FormData();
  formData.append('file', file);
  formData.append('model', 'whisper');

  const response = await fetch(endpoint, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Transcription failed: ${response.status} ${response.statusText}`);
  }

  const result = await response.json();
  return result.text ?? '';
}

export function isAudioRecordingSupported(): boolean {
  return !!(
    typeof navigator !== 'undefined' &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    typeof window !== 'undefined' &&
    window.MediaRecorder
  );
}