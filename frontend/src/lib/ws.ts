// WebSocket manager with auto-reconnect and event emitter pattern

type WsEvent = 'backlog' | 'signal_detected' | 'signal_scored' | 'connect' | 'disconnect';
type Handler = (data: unknown) => void;

const MAX_RECONNECT_ATTEMPTS = 10;
const RECONNECT_DELAY_MS = 3000;

class WsManager {
  private socket: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private manualClose = false;

  connect(): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) return;

    this.manualClose = false;

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/ws`;

    this.socket = new WebSocket(url);

    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      this._emit('connect', null);
    };

    this.socket.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data as string) as { event: string; data: unknown };
        this._emit(msg.event, msg.data);
      } catch {
        // malformed frame — ignore
      }
    };

    this.socket.onclose = () => {
      this._emit('disconnect', null);
      if (!this.manualClose) {
        this._scheduleReconnect();
      }
    };

    this.socket.onerror = () => {
      this.socket?.close();
    };
  }

  disconnect(): void {
    this.manualClose = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.socket?.close();
    this.socket = null;
  }

  subscribe(event: WsEvent | string, handler: Handler): () => void {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event)!.add(handler);
    // return unsubscribe fn
    return () => this.handlers.get(event)?.delete(handler);
  }

  /** Alias for subscribe */
  on(event: WsEvent | string, handler: Handler): () => void {
    return this.subscribe(event, handler);
  }

  private _emit(event: string, data: unknown): void {
    this.handlers.get(event)?.forEach((h) => h(data));
  }

  private _scheduleReconnect(): void {
    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) return;
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, RECONNECT_DELAY_MS);
  }

  get connected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }
}

// Singleton — share one connection across all components
export const ws = new WsManager();
