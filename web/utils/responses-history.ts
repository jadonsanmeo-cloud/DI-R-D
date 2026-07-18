import type { ResponseHistoryDetail, ResponseHistorySummary } from '@/types/responses';

const HISTORY_SESSION_STORAGE_KEY = 'data-intelligence.history-session-id';

export const RESPONSES_HISTORY_CHANGED_EVENT = 'data-intelligence:responses-history-changed';

function createSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, character => {
    const random = (Math.random() * 16) | 0;
    const value = character === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

export function getResponseHistorySessionId(): string | null {
  if (typeof window === 'undefined') return null;
  const existing = window.localStorage.getItem(HISTORY_SESSION_STORAGE_KEY);
  if (existing) return existing;
  const sessionId = createSessionId();
  window.localStorage.setItem(HISTORY_SESSION_STORAGE_KEY, sessionId);
  return sessionId;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail =
      typeof payload?.detail === 'string' ? payload.detail : `Request failed with status ${response.status}`;
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function listResponseHistory(sessionId: string, limit = 50): Promise<ResponseHistorySummary[]> {
  const query = new URLSearchParams({ session_id: sessionId, limit: String(limit) });
  const payload = await requestJson<{ items: ResponseHistorySummary[] }>(
    `${process.env.API_BASE_URL ?? ''}/api/v1/responses?${query.toString()}`,
  );
  return payload.items;
}

export function getResponseHistory(responseId: string, sessionId: string): Promise<ResponseHistoryDetail> {
  const query = new URLSearchParams({ session_id: sessionId });
  return requestJson<ResponseHistoryDetail>(
    `${process.env.API_BASE_URL ?? ''}/api/v1/responses/${encodeURIComponent(responseId)}/history?${query.toString()}`,
  );
}

export async function deleteResponseHistory(responseId: string, sessionId: string): Promise<void> {
  const query = new URLSearchParams({ session_id: sessionId });
  const response = await fetch(
    `${process.env.API_BASE_URL ?? ''}/api/v1/responses/${encodeURIComponent(responseId)}?${query.toString()}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail =
      typeof payload?.detail === 'string' ? payload.detail : `Request failed with status ${response.status}`;
    throw new Error(detail);
  }
}

export function notifyResponseHistoryChanged(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(RESPONSES_HISTORY_CHANGED_EVENT));
  }
}
