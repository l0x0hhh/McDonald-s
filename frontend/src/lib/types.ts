/** API contract mirrors backend/schemas.py. All monetary fields are integer fen. */
export type DataSource = 'live_mcp' | 'demo_snapshot';
export type PlanType = 'recommended' | 'alternative' | 'cheapest' | 'balanced' | 'taste';
export type SpicyLevel = 'spicy' | 'mild' | 'unknown';

export interface DietaryConstraint {
  kind: 'non_spicy';
  people: number;
}

export interface Requirements {
  people: number;
  budget_type: 'total' | 'per_person';
  budget_fen: number;
  dietary_constraints: DietaryConstraint[];
  appetite: 'light' | 'normal' | 'heavy';
  preferred_tags: string[];
  /** V1.1 fields are optional for old fixtures; non-default requests must not be dropped. */
  exclude_beverage?: boolean;
  membership_status?: 'unknown' | 'none' | 'active';
  meal_frequency_per_month?: number | null;
  habit_horizon_months?: number | null;
  habitual_tags?: string[];
}

/** A backend draft may retain missing/contradictory information across chat turns. */
export type RequirementsDraft = {
  [K in keyof Requirements]?: Requirements[K] | null;
} & { pending_fields?: string[]; notes?: string[] };

export interface Store {
  store_code: string;
  name: string;
  address: string;
  /** Snapshot distance is historical, not live user geolocation. */
  distance?: number | null;
  business_status?: boolean | null;
  business_hours?: string | null;
  supports_reservation: boolean;
}

export interface SourceMetadata {
  data_source: DataSource;
  fetched_at: string;
  degraded_reason?: string | null;
}

export interface StoreListResponse extends SourceMetadata {
  stores: Store[];
}

export interface Nutrition {
  energy_kcal?: number | null;
  protein_g?: number | null;
  fat_g?: number | null;
  carbohydrate_g?: number | null;
  sodium_mg?: number | null;
  matched: boolean;
  source: string;
}

export interface PlanItem {
  code: string;
  name: string;
  quantity: number;
  unit_price_fen: number;
  original_unit_price_fen: number;
  line_total_fen: number;
  servings: number;
  spicy_level: SpicyLevel;
  image?: string | null;
  spec?: string | null;
  nutrition?: Nutrition | null;
  line_price_basis?: 'menu_reference' | 'live_quote';
}

export interface ConstraintCheck {
  name: string;
  satisfied: boolean;
  detail: string;
}

export interface Plan {
  plan_id: string;
  quote_id?: string | null;
  quoted_at?: string | null;
  price_basis?: 'live_quote' | 'menu_reference';
  take_way_options?: { code: string; title: string; subtitle?: string }[];
  quote_breakdown?: Record<string, number | null>;
  type: PlanType;
  title: string;
  summary: string;
  items: PlanItem[];
  subtotal_fen: number;
  /** Older recorded fixtures omit this field. Do not reconstruct business pricing. */
  discount_fen?: number;
  total_fen: number;
  budget_fen: number;
  remaining_fen: number;
  servings_total: number;
  non_spicy_servings: number;
  reasons: string[];
  checks: ConstraintCheck[];
}

export interface PlanResponse extends SourceMetadata {
  plans: Plan[];
  requirements: Requirements;
  total_budget_fen: number;
  unsatisfiable_reason?: string | null;
  suggestions: string[];
  algorithm_source?: 'llm_skill' | 'skill' | 'demo_sample' | 'unavailable';
  capabilities?: Record<string, boolean>;
  skill_version?: string;
  skill_hash?: string;
}

export interface ChatResponse {
  /** Complete requirements populate the form; API follow-ups prefer the draft below. */
  requirements: Requirements | null;
  /** Prefer this in the next API chat turn, including when requirements is null. */
  draft?: RequirementsDraft | null;
  missing_fields: string[];
  follow_up?: string | null;
  ready: boolean;
  parse_source: 'llm' | 'rule' | 'demo_template';
  notes: string[];
}

export interface OrderRequest {
  plan_id: string;
  quote_id: string;
  take_way_code: string;
  idempotency_key: string;
}

export interface OrderResponse {
  order_id: string;
  plan_id: string;
  store_code: string;
  items: PlanItem[];
  total_fen: number;
  status: 'pending_payment' | 'paid' | 'preparing' | 'delivering' | 'completed' | 'cancelled' | 'unknown';
  payment_url?: string | null;
  expires_at?: string | null;
  created_at: string;
}

export interface Config {
  data_mode: 'live' | 'auto' | 'demo' | string;
  mcp_configured: boolean;
  llm_configured?: boolean;
  planner_ready?: boolean;
  ordering_available?: boolean;
  default_store_code: string;
  default_city: string;
  default_store_keyword?: string;
  order_type: number;
  be_type: number;
}

/** The server exposes status only; existing credentials never return to the browser. */
export interface AccessSettings {
  provider: 'deepseek';
  model: 'deepseek-flash';
  deepseek_configured: boolean;
  mcd_mcp_configured: boolean;
}

export interface UpdateAccessSettings {
  /** An omitted or empty field preserves the existing server-side value. */
  deepseek_api_key?: string;
  mcd_mcp_key?: string;
}

export interface ApiEnvelope<T> {
  code: number | string;
  data: T | null;
  msg: string;
}
