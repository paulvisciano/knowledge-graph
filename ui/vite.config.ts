import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

const llamaHost = process.env.LLAMA_PROXY_HOST || 'http://localhost:8080';
const lightragHost = process.env.LIGHTRAG_PROXY_HOST || 'http://localhost:9621';
const kgApiHost = process.env.KG_API_PROXY_HOST || 'http://localhost:8000';
const mcpHost = process.env.MCP_PROXY_HOST || 'http://localhost:9653';

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  optimizeDeps: {
    exclude: ['framework7', 'framework7-svelte']
  },
  server: {
    host: '0.0.0.0',
    port: 5180,
    proxy: {
      '/v1': {
        target: llamaHost,
        secure: false,
        ws: true
      },
      '/health': {
        target: llamaHost,
        secure: false
      },
      '/slots': {
        target: llamaHost,
        secure: false
      },
      '/api/lightrag': {
        target: lightragHost,
        rewrite: (path) => path.replace(/^\/api\/lightrag/, '')
      },
      '/api/kg': {
        target: kgApiHost,
        rewrite: (path) => path.replace(/^\/api\/kg/, '')
      },
      '/api/sync': {
        target: kgApiHost,
        rewrite: (path) => path.replace(/^\/api\/sync/, ''),
        ws: true
      },
      '/mcp': {
        target: mcpHost,
        rewrite: (path) => path.replace(/^\/mcp/, '/mcp')
      }
    }
  }
});