import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Config } from '../src/lib/types';

const config: Config = {
  data_mode: 'demo',
  mcp_configured: false,
  default_store_code: '1400011',
  default_city: '广州',
  order_type: 1,
  be_type: 1,
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe('API origin configuration', () => {
  it.each([undefined, ''])('uses a same-origin API path when VITE_API_BASE_URL is %s', async (baseUrl) => {
    vi.stubEnv('VITE_API_BASE_URL', baseUrl);
    vi.resetModules();
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: config, msg: 'ok' })));
    vi.stubGlobal('fetch', fetch);
    const { getConfig } = await import('../src/lib/api');

    expect(await getConfig()).toEqual(config);
    const path = String(fetch.mock.calls[0][0]);
    expect(path).toBe('/api/v1/config');
    for (const origin of ['http://localhost:5173', 'http://192.168.126.213:5173']) {
      expect(new URL(path, origin).origin).toBe(origin);
    }
  });

  it('preserves a configured API origin and path while removing trailing slashes', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test:18443/backend///');
    vi.resetModules();
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: config, msg: 'ok' })));
    vi.stubGlobal('fetch', fetch);
    const { getConfig } = await import('../src/lib/api');

    expect(await getConfig()).toEqual(config);
    expect(String(fetch.mock.calls[0][0])).toBe('https://api.example.test:18443/backend/api/v1/config');
  });
});
