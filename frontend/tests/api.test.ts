import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  clearDemoPlans,
  createOrder,
  DEMO_REQUIREMENTS,
  formatMoney,
  getConfig,
  getPlans,
  getStores,
  parseChat,
} from '../src/lib/api';
import type { Requirements } from '../src/lib/types';

const canonical = (): Requirements => structuredClone(DEMO_REQUIREMENTS);

beforeEach(() => clearDemoPlans());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('offline snapshots', () => {
  it('labels stores as offline and filters city/keyword without a network request', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const stores = await getStores('demo', '广州', '今日');
    expect(stores.data_source).toBe('demo_snapshot');
    expect(stores.degraded_reason).toContain('离线');
    expect(stores.stores.map((store) => store.store_code)).toEqual(['1400011']);
    expect((await getStores('demo', '上海', '')).stores).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('returns fixture amounts and offline provenance for the canonical request', async () => {
    const result = await getPlans('demo', '1400011', canonical());
    expect(result.data_source).toBe('demo_snapshot');
    expect(result.plans).toHaveLength(3);
    expect(result.plans[0].total_fen).toBe(10140);
    expect(result.plans[0].plan_id).toMatch(/^demo-/);
    expect(result.plans[1].title).toBe('搭配多样方案');
    expect(result.plans[0].items.some((item) => item.spicy_level === 'unknown')).toBe(true);
    expect(result.suggestions.join('')).toContain('离线');
  });

  it('returns the genuine 30 yuan unsatisfiable fixture without manufacturing plans', async () => {
    const result = await getPlans('demo', '1400011', { ...canonical(), budget_fen: 3000 });
    expect(result.plans).toEqual([]);
    expect(result.unsatisfiable_reason).toContain('超出预算');
    expect(result.total_budget_fen).toBe(3000);
    expect(result.data_source).toBe('demo_snapshot');
  });

  it.each([
    ['other store', '1400401', {}],
    ['different people', '1400011', { people: 8 }],
    ['different budget', '1400011', { budget_fen: 20000 }],
    ['per-person budget', '1400011', { budget_type: 'per_person', budget_fen: 2500 }],
    ['heavy appetite', '1400011', { appetite: 'heavy' }],
    ['preferred tags', '1400011', { preferred_tags: ['鸡肉'] }],
    ['different dietary need', '1400011', { dietary_constraints: [{ kind: 'non_spicy', people: 6 }] }],
  ])('does not fake success for %s', async (_name, store, patch) => {
    const requirements = { ...canonical(), ...patch } as Requirements;
    const result = await getPlans('demo', store, requirements);
    expect(result.plans).toEqual([]);
    expect(result.unsatisfiable_reason).toContain('无可用离线样例');
    expect(result.requirements).toEqual(requirements);
  });

  it('requires a locally issued current plan and recreates checkout from its trusted snapshot', async () => {
    await expect(createOrder('demo', '184a7cd003da')).rejects.toMatchObject({ code: 'E_PLAN_NOT_FOUND' });
    const plans = await getPlans('demo', '1400011', canonical());
    const selected = plans.plans[0];
    selected.total_fen = 1;
    selected.items[0].name = 'modified by caller';
    const order = await createOrder('demo', selected.plan_id);
    expect(order.status).toBe('simulated');
    expect(order.is_demo).toBe(true);
    expect(order.store_code).toBe('1400011');
    expect(order.total_fen).toBe(10140);
    expect(order.items[0].name).toBe('大堡口福三件套');
    expect(order.notice).toContain('模拟');
    expect(order.order_id).toMatch(/^demo-order-/);
    expect(Number.isNaN(Date.parse(order.created_at))).toBe(false);
    clearDemoPlans();
    await expect(createOrder('demo', selected.plan_id)).rejects.toMatchObject({ code: 'E_PLAN_NOT_FOUND' });
  });

  it('invalidates previous demo plan ids on every new generation, including an unsupported request', async () => {
    const first = await getPlans('demo', '1400011', canonical());
    const next = await getPlans('demo', '1400011', canonical());
    expect(first.plans[0].plan_id).not.toBe(next.plans[0].plan_id);
    await expect(createOrder('demo', first.plans[0].plan_id)).rejects.toMatchObject({ code: 'E_PLAN_NOT_FOUND' });
    await getPlans('demo', '1400011', { ...canonical(), people: 8 });
    await expect(createOrder('demo', next.plans[0].plan_id)).rejects.toMatchObject({ code: 'E_PLAN_NOT_FOUND' });
  });
});

describe('clearly marked local chat templates', () => {
  it('recognizes the sample sentence and does not pretend to use an LLM', async () => {
    const result = await parseChat('demo', '6个人，预算150元，2人不吃辣', null);
    expect(result.requirements).toEqual(canonical());
    expect(result.ready).toBe(true);
    expect(result.parse_source).toBe('demo_template');
    expect(result.notes.join('')).toContain('固定句式');
  });

  it('keeps incomplete information out of the complete current contract and asks follow-up questions', async () => {
    const result = await parseChat('demo', '6个人', null);
    expect(result.requirements).toBeNull();
    expect(result.ready).toBe(false);
    expect(result.missing_fields).toContain('budget');
    expect(result.follow_up).toBeTruthy();
  });

  it('asks for a non-spicy count instead of silently treating everybody as non-spicy', async () => {
    const result = await parseChat('demo', '6个人，预算150元，不吃辣', null);
    expect(result.ready).toBe(false);
    expect(result.missing_fields).toContain('non_spicy_people');
    expect(result.follow_up).toContain('几位');
  });

  it('does not emit invalid complete Requirements when dietary counts exceed the party size', async () => {
    const result = await parseChat('demo', '6个人，预算150元，20人不吃辣', null);
    expect(result.ready).toBe(false);
    expect(result.requirements).toBeNull();
    expect(result.missing_fields).toContain('non_spicy_people');
  });

  it('preserves all current fields when only a budget is updated', async () => {
    const current = { ...canonical(), appetite: 'heavy', preferred_tags: ['鸡肉'] } satisfies Requirements;
    const result = await parseChat('demo', '预算改为180元', current);
    expect(result.requirements).toEqual({ ...current, budget_fen: 18000 });
    expect(current.budget_fen).toBe(15000);
    expect(result.parse_source).toBe('demo_template');
  });

  it('retains per-person budget semantics and rejects constraints absent from this contract', async () => {
    const perPerson = await parseChat('demo', '6人，人均25元，2人不吃辣', null);
    expect(perPerson.requirements?.budget_type).toBe('per_person');
    expect(perPerson.requirements?.budget_fen).toBe(2500);
    const unsupported = await parseChat('demo', '不要饮料', canonical());
    expect(unsupported.ready).toBe(false);
    expect(unsupported.follow_up).toContain('表单');
    expect(unsupported.requirements).toEqual(canonical());
  });
});

describe('typed HTTP JSON boundary', () => {
  it('sends query params and omits browser credentials', async () => {
    const payload = { stores: [], data_source: 'live_mcp', fetched_at: 'now' };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: payload, msg: 'ok' })));
    vi.stubGlobal('fetch', fetch);
    expect(await getStores('api', '广州', '体育西路')).toEqual(payload);
    const [url, init] = fetch.mock.calls[0];
    expect(String(url)).toContain('/api/v1/stores?');
    expect(new URL(String(url)).searchParams.get('city')).toBe('广州');
    expect(new URL(String(url)).searchParams.get('keyword')).toBe('体育西路');
    expect(init.credentials).toBe('omit');
  });

  it('sends exact complete contracts to chat, plans, and orders; reads config', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ code: 0, data: {}, msg: 'ok' })));
    vi.stubGlobal('fetch', fetch);
    await parseChat('api', '预算150元', canonical());
    await getPlans('api', '1400011', canonical());
    await createOrder('api', 'server-plan');
    await getConfig();
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ message: '预算150元', current: canonical() });
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ store_code: '1400011', requirements: canonical() });
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ plan_id: 'server-plan' });
    expect(String(fetch.mock.calls[3][0])).toMatch(/\/api\/v1\/config$/);
  });

  it('preserves stable business codes, messages, and HTTP status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'E_PLAN_EXPIRED', data: null, msg: '方案已过期' }), { status: 409 })));
    await expect(createOrder('api', 'old')).rejects.toMatchObject({ code: 'E_PLAN_EXPIRED', status: 409, message: '方案已过期' });
  });

  it('rejects a nonzero envelope even when HTTP is 200', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'E_PARAM', data: null, msg: '需求错误' }))));
    await expect(getConfig()).rejects.toMatchObject({ code: 'E_PARAM', status: 200 });
  });

  it('turns non-JSON HTTP failures into readable typed errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>upstream unavailable</html>', { status: 502 })));
    await expect(getConfig()).rejects.toMatchObject({ code: 'E_HTTP_502', status: 502 });
  });

  it('reports a malformed success payload instead of exposing undefined data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ msg: 'ok' }))));
    await expect(getConfig()).rejects.toBeInstanceOf(ApiError);
  });

  it('gives network failures a distinguishable error code', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(getConfig()).rejects.toMatchObject({ code: 'E_NETWORK', status: 0 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('aborts a stalled checkout at the request timeout without retrying it', async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    const fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      signal = init.signal ?? undefined;
      return new Promise((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    });
    vi.stubGlobal('fetch', fetch);
    const outcome = createOrder('api', 'server-plan').catch((error: unknown) => error);
    expect(signal).toBeInstanceOf(AbortSignal);
    await vi.advanceTimersByTimeAsync(19_999);
    expect(signal!.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(signal!.aborted).toBe(true);
    expect(await outcome).toMatchObject({ code: 'E_REQUEST_TIMEOUT', status: 0 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps the request timeout active until the JSON body finishes', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (_url: string, init: RequestInit) => ({
      ok: true,
      status: 200,
      json: () => new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      }),
    })));
    const outcome = getConfig().catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(20_000);
    // The response body belongs to the same request deadline, not a separate parse error.
    expect(await Promise.race([outcome, Promise.resolve('still pending')])).toMatchObject({ code: 'E_REQUEST_TIMEOUT' });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cleans the request timeout after a successful JSON response', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: {}, msg: 'ok' }))));
    await getConfig();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('formats server cents without modifying the underlying amount', () => {
    expect(formatMoney(10140)).toBe('101.40');
    expect(formatMoney(0)).toBe('0.00');
  });
});
