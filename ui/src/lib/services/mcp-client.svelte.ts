import { MCP_API, type MCPToolCall, type OpenAIToolDefinition, type MCPToolInfo } from '$lib/constants';

interface MCPTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

class McpClient {
  private connected = $state(false);
  private connecting = $state(false);
  private connectionError = $state<string | null>(null);
  private serverInfo = $state<{ name: string; version: string } | null>(null);
  private availableTools = $state<MCPToolInfo[]>([]);
  private requestId = 0;

  get isConnected(): boolean { return this.connected; }
  get isConnecting(): boolean { return this.connecting; }
  get error(): string | null { return this.connectionError; }
  get tools(): MCPToolInfo[] { return this.availableTools; }
  get enabledTools(): MCPToolInfo[] { return this.availableTools.filter(t => t.enabled); }
  get enabledOpenAITools(): OpenAIToolDefinition[] {
    return this.availableTools.filter(t => t.enabled).map(t => t.definition);
  }

  async connect(url?: string): Promise<void> {
    if (this.connected || this.connecting) return;

    this.connecting = true;
    this.connectionError = null;

    try {
      const serverUrl = url ?? MCP_API;
      const mcpEndpoint = `${serverUrl}/mcp`;

      const initResponse = await fetch(mcpEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: ++this.requestId,
          method: 'initialize',
          params: {
            protocolVersion: '2024-11-05',
            capabilities: {},
            clientInfo: { name: 'nexus-ui', version: '0.1.0' },
          },
        }),
      });

      if (!initResponse.ok) {
        throw new Error(`Initialize failed: ${initResponse.status}`);
      }

      const initData = await initResponse.json();
      if (initData.error) {
        throw new Error(initData.error.message || 'Initialize failed');
      }

      const result = initData.result;
      if (result?.serverInfo) {
        this.serverInfo = {
          name: result.serverInfo.name || 'Unknown',
          version: result.serverInfo.version || '0.0.0',
        };
      }

      await fetch(mcpEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({
          jsonrpc: '2.0',
          method: 'notifications/initialized',
        }),
      });

      await this.fetchTools();

      this.connected = true;
      this.connecting = false;
    } catch (err) {
      this.connectionError = err instanceof Error ? err.message : 'Connection failed';
      this.connecting = false;
      throw err;
    }
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    this.serverInfo = null;
    this.availableTools = [];
    this.connectionError = null;
  }

  private async fetchTools(): Promise<void> {
    const mcpEndpoint = `${MCP_API}/mcp`;

    const response = await fetch(mcpEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: ++this.requestId,
        method: 'tools/list',
      }),
    });

    if (!response.ok) throw new Error(`Tools list failed: ${response.status}`);

    const data = await response.json();
    if (data.error) throw new Error(data.error.message || 'Failed to list tools');

    const tools: MCPTool[] = data.result?.tools ?? [];

    this.availableTools = tools.map((tool) => {
      const definition: OpenAIToolDefinition = {
        type: 'function',
        function: {
          name: tool.name,
          description: tool.description,
          parameters: tool.inputSchema as Record<string, unknown> | undefined,
        },
      };

      return {
        name: tool.name,
        description: tool.description || '',
        enabled: true,
        definition,
      };
    });
  }

  toggleTool(name: string, enabled: boolean): void {
    this.availableTools = this.availableTools.map((t) =>
      t.name === name ? { ...t, enabled } : t
    );
  }

  enableAllTools(): void {
    this.availableTools = this.availableTools.map((t) => ({ ...t, enabled: true }));
  }

  disableAllTools(): void {
    this.availableTools = this.availableTools.map((t) => ({ ...t, enabled: false }));
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    const mcpEndpoint = `${MCP_API}/mcp`;

    const response = await fetch(mcpEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: ++this.requestId,
        method: 'tools/call',
        params: {
          name,
          arguments: args,
        },
      }),
    });

    if (!response.ok) {
      throw new Error(`MCP tool call failed: ${response.status}`);
    }

    const data = await response.json();
    if (data.error) {
      throw new Error(data.error.message || 'Tool call failed');
    }

    const result = data.result;
    if (result?.content && Array.isArray(result.content)) {
      return result.content
        .map((c: { type: string; text?: string }) => c.text ?? JSON.stringify(c))
        .join('\n');
    }

    return JSON.stringify(result);
  }
}

export const mcpClient = new McpClient();