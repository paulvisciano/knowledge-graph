import { writable } from 'svelte/store';

export type TabId = 'graph' | 'ingestion' | 'activity';

export const activeTab = writable<TabId>('graph');

export const lightragStatus = writable<'connected' | 'disconnected' | 'busy' | 'error'>('disconnected');
export const llamaStatus = writable<'connected' | 'disconnected' | 'busy' | 'error'>('disconnected');
export const mcpStatus = writable<'connected' | 'disconnected' | 'busy' | 'error'>('disconnected');

export const rightPanelOpen = writable(true);
export const selectedNodeId = writable<string | null>(null);
export const settingsDrawerOpen = writable(false);
export const navDrawerOpen = writable(false);
export const historyPanelOpen = writable(false);