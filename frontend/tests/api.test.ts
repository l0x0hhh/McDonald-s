import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError,createOrder,formatMoney,getConfig,getOrder,getPlans,getStores,officialPaymentUrl,parseChat } from '../src/lib/api';
import { canonical as requirements } from './helpers/online-api';
const canonical=()=>structuredClone(requirements);
const payload={plan_id:'server-plan',quote_id:'server-quote',take_way_code:'take-in-store',idempotency_key:'stable-key'};
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers()});
describe('typed HTTP JSON boundary', () => {
  it('sends query params and omits browser credentials', async () => {
    const payload = { stores: [], data_source: 'live_mcp', fetched_at: 'now' };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: payload, msg: 'ok' })));
    vi.stubGlobal('fetch', fetch);
    expect(await getStores('广州', '体育西路')).toEqual(payload);
    const [url, init] = fetch.mock.calls[0];
    expect(String(url)).toContain('/api/v1/stores?');
    expect(new URL(String(url), window.location.origin).searchParams.get('city')).toBe('广州');
    expect(new URL(String(url), window.location.origin).searchParams.get('keyword')).toBe('体育西路');
    expect(init.credentials).toBe('omit');
  });

  it('sends exact complete contracts to chat, plans, and orders; reads config', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ code: 0, data: {}, msg: 'ok' })));
    vi.stubGlobal('fetch', fetch);
    await parseChat('预算150元', canonical());
    await getPlans('1400011', canonical());
    await createOrder(payload);
    await getConfig();
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ message: '预算150元', current: canonical() });
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ store_code: '1400011', requirements: canonical() });
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual(payload);
    expect(String(fetch.mock.calls[3][0])).toMatch(/\/api\/v1\/config$/);
  });

  it('preserves stable business codes, messages, and HTTP status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'E_PLAN_EXPIRED', data: null, msg: '方案已过期' }), { status: 409 })));
    await expect(createOrder({...payload,plan_id:'old'})).rejects.toMatchObject({ code: 'E_PLAN_EXPIRED', status: 409, message: '方案已过期' });
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
    const outcome = createOrder(payload).catch((error: unknown) => error);
    expect(signal).toBeInstanceOf(AbortSignal);
    await vi.advanceTimersByTimeAsync(89_999);
    expect(signal!.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(signal!.aborted).toBe(true);
    expect(await outcome).toMatchObject({ code: 'E_ORDER_SUBMISSION_UNKNOWN', status: 0 });
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
    await vi.advanceTimersByTimeAsync(30_000);
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

describe('payment contract boundary',()=>{
  it('preserves price-change data instead of discarding the updated quote',async()=>{
    const data={updated_plan:{plan_id:'server-plan',quote_id:'new'}}
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({code:'E_PRICE_CHANGED',data,msg:'价格变化'}),{status:409})))
    await expect(createOrder(payload)).rejects.toMatchObject({code:'E_PRICE_CHANGED',data})
  })
  it.each(['https://m.mcd.cn/pay','https://mcp.mcd.cn/pay','https://mcd.cn/pay'])('accepts exact official HTTPS %s',url=>expect(officialPaymentUrl(url)).toBe(url))
  it.each(['http://m.mcd.cn/pay','https://m.mcd.cn.evil.test/pay','https://a.mcd.cn/pay','https://u:p@m.mcd.cn/pay','https://m.mcd.cn:444/pay'])('rejects untrusted URL %s',url=>expect(officialPaymentUrl(url)).toBeNull())
  it('queries the order instead of creating one',async()=>{
    const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({code:0,data:{status:'pending_payment'},msg:'ok'})))
    vi.stubGlobal('fetch',fetch);await getOrder('order/one')
    expect(fetch.mock.calls[0][0]).toContain('/orders/order%2Fone');expect(fetch.mock.calls[0][1].method).toBe('GET')
  })
  it.each(['network','decode'])('treats ambiguous %s order submission as unknown without retry',async kind=>{
    const fetch=vi.fn().mockImplementation(async()=>{if(kind==='network')throw new TypeError('offline');return new Response('not JSON')})
    vi.stubGlobal('fetch',fetch);await expect(createOrder(payload)).rejects.toMatchObject({code:'E_ORDER_SUBMISSION_UNKNOWN'});expect(fetch).toHaveBeenCalledTimes(1)
  })
})
