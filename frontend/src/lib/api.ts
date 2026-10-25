import type { AccessSettings, ApiEnvelope, ChatResponse, Config, OrderRequest, OrderResponse, PlanResponse, Requirements, RequirementsDraft, StoreListResponse, UpdateAccessSettings } from './types';
// Same-origin by default: a LAN browser's loopback is not the server computer.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '');
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly data: Record<string, unknown> | null;

  constructor(message: string, code: string, status = 0, data: Record<string, unknown> | null = null) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.data = data;
  }
}

/** Shared HTTP boundary. Never retries a checkout and never sends browser credentials. */
async function request<T>(path: string, body?: unknown, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), timeoutMs);
  const isOrder = path === '/orders';
  const uncertain = () => new ApiError('订单提交结果待核实，请勿重复下单。', 'E_ORDER_SUBMISSION_UNKNOWN');
  const timeoutError = () => isOrder ? uncertain() : new ApiError('请求超时，请重试。', 'E_REQUEST_TIMEOUT');
  try {
    let response: Response;
    try {
      response = await fetch(`${BASE_URL}/api/v1${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        credentials: 'omit',
        signal: controller.signal,
        headers: body === undefined
          ? { Accept: 'application/json' }
          : { Accept: 'application/json', 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      throw controller.signal.aborted ? timeoutError()
        : isOrder ? uncertain() : new ApiError('连接失败，请稍后重试。', 'E_NETWORK');
    }

    let envelope: Partial<ApiEnvelope<T>>;
    try {
      envelope = await response.json() as Partial<ApiEnvelope<T>>;
    } catch {
      if (controller.signal.aborted) throw timeoutError();
      if (isOrder) throw uncertain();
      throw new ApiError(
        response.ok ? '接口返回了非 JSON 响应。' : `接口请求失败（HTTP ${response.status}）。`,
        response.ok ? 'E_BAD_RESPONSE' : `E_HTTP_${response.status}`,
        response.status,
      );
    }
    if (!envelope || typeof envelope !== 'object') {
      if (isOrder) throw uncertain();
      throw new ApiError('接口响应格式异常。', response.ok ? 'E_BAD_RESPONSE' : `E_HTTP_${response.status}`, response.status);
    }
    if (!response.ok || envelope.code !== 0) {
      const code = typeof envelope.code === 'string' ? envelope.code
        : !response.ok ? `E_HTTP_${response.status}` : 'E_BAD_RESPONSE';
      if (isOrder && (code.startsWith('E_HTTP_') || code === 'E_BAD_RESPONSE')) throw uncertain();
      throw new ApiError(envelope.msg || `接口请求失败（HTTP ${response.status}）。`, code, response.status, envelope.data as Record<string, unknown> | null);
    }
    if (envelope.data === null || envelope.data === undefined) {
      if (isOrder) throw uncertain();
      throw new ApiError('接口成功响应缺少 data。', 'E_BAD_RESPONSE', response.status);
    }
    return envelope.data;
  } finally {
    clearTimeout(deadline);
  }
}


export function formatMoney(fen: number): string { return (fen / 100).toFixed(2); }
export function getStores(city = '广州', keyword = ''): Promise<StoreListResponse> {
  return request(`/stores?${new URLSearchParams({ city, keyword })}`);
}
export function parseChat(message: string, current: Requirements | RequirementsDraft | null): Promise<ChatResponse> {
  return request('/chat', { message, current });
}
export function getPlans(storeCode: string, requirements: Requirements): Promise<PlanResponse> {
  return request('/plans', { store_code: storeCode, requirements }, 90_000);
}
export function createOrder(payload: OrderRequest): Promise<OrderResponse> { return request('/orders', payload, 90_000); }
export function getOrder(orderId: string): Promise<OrderResponse> { return request(`/orders/${encodeURIComponent(orderId)}`); }
export function getConfig(): Promise<Config> { return request('/config'); }
export function getSettings(): Promise<AccessSettings> { return request('/settings'); }
export function saveSettings(payload: UpdateAccessSettings): Promise<AccessSettings> { return request('/settings', payload); }
/** Same exact hosts as the server; no suffix wildcard, credentials, HTTP or custom port. */
export function officialPaymentUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || !['m.mcd.cn', 'mcp.mcd.cn', 'mcd.cn'].includes(url.hostname)) return null;
    return url.href;
  } catch { return null; }
}
