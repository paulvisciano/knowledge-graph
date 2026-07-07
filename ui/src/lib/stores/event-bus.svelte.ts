import type { ActivityEvent } from '$lib/constants';

const MAX_EVENTS = 100;

type EventCallback = (event: ActivityEvent) => void;

class EventBus {
  events = $state<ActivityEvent[]>([]);
  private subscribers = new Set<EventCallback>();

  pushEvent(event: ActivityEvent) {
    this.events = [...this.events, event].slice(-MAX_EVENTS);
    for (const cb of this.subscribers) cb(event);
  }

  subscribe(callback: EventCallback) {
    this.subscribers.add(callback);
    return () => {
      this.subscribers.delete(callback);
    };
  }

  clear() {
    this.events = [];
  }

  getByType(type: ActivityEvent['type']): ActivityEvent[] {
    return this.events.filter((e) => e.type === type);
  }
}

export const eventBus = new EventBus();