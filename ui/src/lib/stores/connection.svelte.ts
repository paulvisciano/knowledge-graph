import { lightragClient } from '$lib/services/lightrag-client';
import { llamaClient } from '$lib/services/llama-client';
import { kgApiClient } from '$lib/services/kg-api-client';
import { mcpClient } from '$lib/services/mcp-client.svelte';
import { eventBus } from './event-bus.svelte';

class ConnectionStore {
  lightragConnected = $state(false);
  llamaConnected = $state(false);
  kgApiConnected = $state(false);
  pipelineBusy = $state(false);

  private intervalId: ReturnType<typeof setInterval> | null = null;

  async checkLightragHealth() {
    try {
      const status = await lightragClient.health();
      this.lightragConnected = status.status === 'healthy';
      this.pipelineBusy = status.pipeline_busy ?? false;
    } catch {
      this.lightragConnected = false;
    }
  }

  async checkLlamaHealth() {
    try {
      await llamaClient.getHealth();
      this.llamaConnected = true;
    } catch {
      this.llamaConnected = false;
    }
  }

  async checkKgApiHealth() {
    try {
      const result = await kgApiClient.health();
      this.kgApiConnected = result.status === 'ok';
    } catch {
      this.kgApiConnected = false;
    }
  }

  async connectMcp(url?: string) {
    await mcpClient.connect(url);
  }

  async disconnectMcp() {
    await mcpClient.disconnect();
  }

  async pollAll() {
    await Promise.allSettled([
      this.checkLightragHealth(),
      this.checkLlamaHealth(),
      this.checkKgApiHealth(),
    ]);
  }

  startPolling(intervalMs: number = 10000) {
    this.pollAll();
    this.intervalId = setInterval(() => this.pollAll(), intervalMs);
  }

  stopPolling() {
    if (this.intervalId !== null) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
  }
}

export const connectionStore = new ConnectionStore();