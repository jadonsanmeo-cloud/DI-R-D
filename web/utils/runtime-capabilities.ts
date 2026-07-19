import type { RuntimeCapabilities } from '@/types/responses';

export async function fetchRuntimeCapabilities(signal?: AbortSignal): Promise<RuntimeCapabilities> {
  const response = await fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/runtime-capabilities`, { signal });
  if (!response.ok) {
    throw new Error(`Runtime capability request failed with status ${response.status}`);
  }
  return response.json() as Promise<RuntimeCapabilities>;
}

export function initialMethodHubEnabled(capabilities: RuntimeCapabilities): boolean {
  return capabilities.method_hub.default_enabled;
}
