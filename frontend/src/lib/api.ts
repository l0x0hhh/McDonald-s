import storeFixture from '../../../docs/api-examples/stores.json';
import successFixture from '../../../docs/api-examples/plans_6人150元2人不吃辣.json';
import noSolutionFixture from '../../../docs/api-examples/plans_无解.json';
import { parseDemoChat } from './demo-chat';
import type {
  ApiEnvelope,
  ChatResponse,
  Config,
  Mode,
  OrderResponse,
  Plan,
  PlanResponse,
  Requirements,
  StoreListResponse,
} from './types';

export type { Mode } from './types';

export const DEMO_NOTICE = '离线样例：固定历史快照，不代表实时门店、附近定位、最终收银报价或正式推荐算法。';
export const DEMO_REQUIREMENTS: Requirements = structuredClone(successFixture.data.requirements) as Requirements;
const BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:18000').replace(/\/+$/, '');
const DEMO_STORE = '1400011';
const PLAN_TTL_MS = 30 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 20_000;
const issuedDemoPlans = new Map<string, { plan: Plan; createdAt: number }>();
let generation = 0;

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

/** Shared HTTP boundary. Never retries a checkout and never sends browser credentials. */
async function request<T>(path: string, body?: unknown): Promise<T> {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const timeoutError = () => new ApiError('后端请求超过 20 秒，请稍后重试或切换离线样例。', 'E_REQUEST_TIMEOUT');
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
        : new ApiError('后端连接失败，请检查服务地址与后端运行状态，或切换离线样例。', 'E_NETWORK');
    }

    let envelope: Partial<ApiEnvelope<T>>;
    try {
      envelope = await response.json() as Partial<ApiEnvelope<T>>;
    } catch {
      if (controller.signal.aborted) throw timeoutError();
      throw new ApiError(
        response.ok ? '接口返回了非 JSON 响应。' : `接口请求失败（HTTP ${response.status}）。`,
        response.ok ? 'E_BAD_RESPONSE' : `E_HTTP_${response.status}`,
        response.status,
      );
    }
    if (!envelope || typeof envelope !== 'object') {
      throw new ApiError('接口响应格式异常。', response.ok ? 'E_BAD_RESPONSE' : `E_HTTP_${response.status}`, response.status);
    }
    if (!response.ok || envelope.code !== 0) {
      const code = typeof envelope.code === 'string' ? envelope.code
        : !response.ok ? `E_HTTP_${response.status}` : 'E_BAD_RESPONSE';
      throw new ApiError(envelope.msg || `接口请求失败（HTTP ${response.status}）。`, code, response.status);
    }
    if (envelope.data === null || envelope.data === undefined) {
      throw new ApiError('接口成功响应缺少 data。', 'E_BAD_RESPONSE', response.status);
    }
    return envelope.data;
  } finally {
    clearTimeout(deadline);
  }
}

/** Call when changing mode, store, or requirements to invalidate local checkout candidates. */
export function clearDemoPlans(): void {
  issuedDemoPlans.clear();
}

export function formatMoney(fen: number): string {
  return (fen / 100).toFixed(2);
}

export async function getStores(mode: Mode, city = '广州', keyword = ''): Promise<StoreListResponse> {
  if (mode === 'api') {
    const query = new URLSearchParams({ city, keyword });
    return request<StoreListResponse>(`/stores?${query.toString()}`);
  }
  const result = structuredClone(storeFixture.data) as StoreListResponse;
  result.data_source = 'demo_snapshot';
  result.degraded_reason = DEMO_NOTICE;
  const cityQuery = city.trim().replace(/市$/, '');
  const keywords = keyword.trim().split(/\s+/).filter(Boolean);
  result.stores = result.stores.filter((store) => {
    const haystack = `${store.name} ${store.address}`;
    return (!cityQuery || haystack.includes(cityQuery)) && keywords.every((word) => haystack.includes(word));
  });
  return result;
}

export async function parseChat(mode: Mode, message: string, current: Requirements | null): Promise<ChatResponse> {
  return mode === 'api'
    ? request<ChatResponse>('/chat', { message, current })
    : parseDemoChat(message, current);
}

function matchesCanonical(requirements: Requirements, budget: number): boolean {
  return requirements.people === 6
    && requirements.budget_type === 'total'
    && requirements.budget_fen === budget
    && requirements.appetite === 'normal'
    && requirements.preferred_tags.length === 0
    && requirements.dietary_constraints.length === 1
    && requirements.dietary_constraints[0].kind === 'non_spicy'
    && requirements.dietary_constraints[0].people === 2;
}

export async function getPlans(mode: Mode, storeCode: string, requirements: Requirements): Promise<PlanResponse> {
  clearDemoPlans();
  if (mode === 'api') return request<PlanResponse>('/plans', { store_code: storeCode, requirements });

  const sampleBudget = matchesCanonical(requirements, 15000) ? 15000
    : matchesCanonical(requirements, 3000) ? 3000 : null;
  if (storeCode !== DEMO_STORE || sampleBudget === null) {
    return {
      plans: [],
      requirements: structuredClone(requirements),
      total_budget_fen: requirements.budget_type === 'per_person'
        ? requirements.budget_fen * requirements.people : requirements.budget_fen,
      data_source: 'demo_snapshot',
      fetched_at: successFixture.data.fetched_at,
      degraded_reason: DEMO_NOTICE,
      unsatisfiable_reason: '当前门店或需求无可用离线样例。这不表示真实点餐无解；离线模式不重新算价，也不会忽略你的约束。',
      suggestions: ['体验固定样例：广州今日广场餐厅 · 6 人 · 总预算 150 元 · 2 人不辣 · 正常饭量 · 无额外偏好。', '测试预算无解：保持以上条件，仅把总预算改为 30 元。', '其他需求请切换真实 API 模式。'],
    };
  }

  const result = structuredClone(sampleBudget === 15000 ? successFixture.data : noSolutionFixture.data) as PlanResponse;
  result.data_source = 'demo_snapshot';
  result.degraded_reason = DEMO_NOTICE;
  result.suggestions = [DEMO_NOTICE, ...result.suggestions];
  const batch = ++generation;
  const createdAt = Date.now();
  result.plans.forEach((plan, index) => {
    plan.plan_id = `demo-${batch}-${index}-${createdAt.toString(36)}`;
    if (plan.type === 'balanced') plan.title = '搭配多样方案';
    issuedDemoPlans.set(plan.plan_id, { plan: structuredClone(plan), createdAt });
  });
  return result;
}

export async function createOrder(mode: Mode, planId: string): Promise<OrderResponse> {
  if (mode === 'api') return request<OrderResponse>('/orders', { plan_id: planId });
  const issued = issuedDemoPlans.get(planId);
  if (!issued) throw new ApiError('离线方案已失效，请重新生成后再模拟结算。', 'E_PLAN_NOT_FOUND', 404);
  if (Date.now() - issued.createdAt > PLAN_TTL_MS) {
    issuedDemoPlans.delete(planId);
    throw new ApiError('离线方案已超过 30 分钟，请重新生成。', 'E_PLAN_EXPIRED', 409);
  }
  return {
    order_id: `demo-order-${crypto.randomUUID()}`,
    plan_id: planId,
    store_code: DEMO_STORE,
    items: structuredClone(issued.plan.items),
    total_fen: issued.plan.total_fen,
    status: 'simulated',
    is_demo: true,
    notice: '离线模拟结算：不产生真实扣款，不向门店发送订单，不构成履约承诺；金额来自所选历史样例。',
    created_at: new Date().toISOString(),
  };
}

export async function getConfig(): Promise<Config> {
  return request<Config>('/config');
}
