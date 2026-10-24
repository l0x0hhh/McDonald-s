/** API contract mirrors backend/schemas.py. All monetary fields are integer fen. */
export type Mode = 'demo' | 'api';
export type DataSource = 'live_mcp' | 'demo_snapshot';
export type PlanType = 'cheapest' | 'balanced' | 'taste';
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
}

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
}

export interface ConstraintCheck {
  name: string;
  satisfied: boolean;
  detail: string;
}

export interface Plan {
  plan_id: string;
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
}

export interface ChatResponse {
  /** Only complete Requirements may be sent as the next API current value. */
  requirements: Requirements | null;
  missing_fields: string[];
  follow_up?: string | null;
  ready: boolean;
  parse_source: 'llm' | 'rule' | 'demo_template';
  notes: string[];
}

export interface OrderResponse {
  order_id: string;
  plan_id: string;
  store_code: string;
  items: PlanItem[];
  total_fen: number;
  status: 'simulated';
  is_demo: true;
  notice: string;
  created_at: string;
}

export interface Config {
  data_mode: 'live' | 'auto' | 'demo' | string;
  mcp_configured: boolean;
  default_store_code: string;
  default_city: string;
  default_store_keyword?: string;
  order_type: number;
  be_type: number;
}

export interface ApiEnvelope<T> {
  code: number | string;
  data: T | null;
  msg: string;
}
