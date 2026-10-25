import { useState } from 'react';
import { motion } from 'motion/react';
import {
  ArrowRight,
  CheckCircle2,
  ChefHat,
  PiggyBank,
  Sandwich,
  Utensils,
  XCircle,
} from 'lucide-react';
import { formatMoney } from '../lib/api';
import { buttonMotion, cardMotion } from '../lib/motion-presets';
import { planSharedTransition, planTitles, planTransitionId, sharedPlanElement } from '../lib/plan-transition';
import { useLiveReducedMotion } from '../lib/use-live-reduced-motion';
import type { Plan, PlanItem, PlanType } from '../lib/types';
import './plan-cards.css';
import './plan-transition.css';

const planPresentation = {
  recommended: {title: '推荐搭配', badge: '为你搭配', icon: ChefHat},
  alternative: {title: '换个搭配', badge: '另一种选择', icon: Utensils},
  cheapest: { title: planTitles.cheapest, badge: '价格优先', icon: PiggyBank },
  balanced: { title: planTitles.balanced, badge: '多样搭配', icon: Utensils },
  taste: { title: planTitles.taste, badge: '口味偏好', icon: ChefHat },
} satisfies Record<PlanType, { title: string; badge: string; icon: typeof ChefHat }>;

const spicyLabels = {
  mild: '本地判断不辣',
  unknown: '辣度未知',
  spicy: '辣味',
} as const;

export function FoodImage({ item, transitionId }: { item: PlanItem; transitionId?: string }) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const reducedMotion = useLiveReducedMotion();
  const image = item.image?.trim();

  return (
    <motion.div className="food-image" {...sharedPlanElement(transitionId, reducedMotion)} transition={{ layout: planSharedTransition }}>
      {image && image !== failedImage ? (
        <img
          src={image}
          alt={item.name}
          loading="lazy"
          onError={() => setFailedImage(image)}
        />
      ) : (
        <Sandwich size={30} strokeWidth={1.6} aria-hidden="true" />
      )}
    </motion.div>
  );
}

function nutritionValue(value: number | null | undefined, unit: string): string {
  return value == null ? '暂无数据' : `${value} ${unit}`;
}

function basketQuantities(plan: Plan): Map<string, number> {
  const quantities = new Map<string, number>();
  for (const item of plan.items) {
    quantities.set(item.code, (quantities.get(item.code) ?? 0) + item.quantity);
  }
  return quantities;
}

function otherQuantityLabel(quantities: number[]): string {
  const min = Math.min(...quantities);
  const max = Math.max(...quantities);
  return min === max ? `${min}` : `${min}–${max}`;
}

export default function PlanCards({
  plans,
  onSelect,
  people,
  nonSpicyPeople,
  disabled = false,
}: {
  plans: Plan[];
  onSelect: (plan: Plan) => void;
  people?: number;
  nonSpicyPeople?: number;
  disabled?: boolean;
}) {
  const reducedMotion = useLiveReducedMotion();
  const submittedPeople = typeof people === 'number' && Number.isInteger(people) && people > 0
    ? people : undefined;
  const requestedNonSpicy = typeof nonSpicyPeople === 'number' && Number.isInteger(nonSpicyPeople) && nonSpicyPeople >= 0
    ? nonSpicyPeople : undefined;
  const baskets = plans.map(basketQuantities);
  const allItems = new Map(plans.flatMap((plan) => plan.items.map((item) => [item.code, item] as const)));

  return (
    <div className="plans-list">
      {plans.map((plan, planIndex) => {
        const presentation = planPresentation[plan.type];
        const Icon = presentation.icon;
        const title = plan.type === 'recommended' || plan.type === 'alternative' ? plan.title : presentation.title;
        const summary = plan.summary.trim();
        const reason = plan.reasons.find((entry) => entry.trim())?.trim();
        const basket = baskets[planIndex];
        const others = baskets.filter((_, index) => index !== planIndex);
        const missingItems = others.length === 0 ? [] : Array.from(allItems.values()).filter(
          (item) => !basket.has(item.code) && others.some((other) => (other.get(item.code) ?? 0) > 0),
        );
        const hasDifferences = others.length > 0 && Array.from(allItems.keys()).some(
          (code) => others.some((other) => (other.get(code) ?? 0) !== (basket.get(code) ?? 0)),
        );
        const nonSpicyShortfall = requestedNonSpicy == null
          ? undefined : Math.max(0, requestedNonSpicy - plan.non_spicy_servings);
        const entrance = cardMotion(planIndex, reducedMotion);

        return (
          <motion.article
            key={plan.plan_id}
            className={`plan-card${plan.type === 'cheapest' || plan.type === 'recommended' ? ' featured' : ''}`}
            aria-label={title}
            {...entrance}
            whileHover={undefined}
          >
            <header className="plan-card-header">
              <h4>
                <span className="card-station-icon" aria-hidden="true"><Icon size={19} /></span>
                <motion.span className="plan-shared-label" {...sharedPlanElement(planTransitionId(plan.plan_id, 'title'), reducedMotion)} transition={{ layout: planSharedTransition }}>{title}</motion.span>
              </h4>
              <span className="plan-label">{presentation.badge}</span>
            </header>

            <div className="plan-card-body">
              <dl className="plan-metrics" aria-label="方案关键对比">
                <div className="plan-metric metric-total">
                  <dt>{plan.price_basis === 'live_quote' ? '官方报价' : '总价'}</dt>
                  <motion.dd className="price" {...sharedPlanElement(planTransitionId(plan.plan_id, 'total'), reducedMotion)} transition={{ layout: planSharedTransition }}><span>¥</span>{formatMoney(plan.total_fen)}</motion.dd>
                </div>
                <div className="plan-metric">
                  <dt>人均</dt>
                  <dd>{submittedPeople ? `¥${formatMoney(plan.total_fen / submittedPeople)}` : '—'}</dd>
                  <small>{submittedPeople ? `按 ${submittedPeople} 人均摊` : '人数未确认'}</small>
                </div>
                <div className={`plan-metric${plan.remaining_fen < 0 ? ' metric-warning' : ''}`}>
                  <dt>{plan.remaining_fen < 0 ? '超预算' : '预算剩余'}</dt>
                  <dd>¥{formatMoney(Math.abs(plan.remaining_fen))}</dd>
                  <small>预算 ¥{formatMoney(plan.budget_fen)}</small>
                </div>
                <div className={`plan-metric metric-non-spicy${nonSpicyShortfall ? ' metric-warning' : ''}`}>
                  <dt>不辣主食</dt>
                  <dd>
                    {requestedNonSpicy == null
                      ? `${plan.non_spicy_servings} 份`
                      : requestedNonSpicy === 0
                        ? '无不辣要求'
                        : `${plan.non_spicy_servings} / 需 ${requestedNonSpicy} 份`}
                  </dd>
                  <small>
                    {requestedNonSpicy == null
                      ? '需求未确认'
                      : requestedNonSpicy === 0
                        ? `本地判断不辣 ${plan.non_spicy_servings} 份`
                        : nonSpicyShortfall
                          ? `还差 ${nonSpicyShortfall} 份`
                          : '已满足 · 本地判断'}
                  </small>
                </div>
              </dl>

              {plan.quote_breakdown && <div className="quote-breakdown">{Object.entries({ packingPrice: '包装费', tablewarePrice: '餐具费', deliveryPrice: '配送费', discount: '优惠' }).map(([key, label]) => plan.quote_breakdown?.[key] == null ? null : <span key={key}>{label} ¥{formatMoney(plan.quote_breakdown[key]!)}</span>)}</div>}
              {summary && <p className="plan-spec plan-summary">{summary}</p>}

              <div className="plan-items">
                {plan.items.map((item, itemIndex) => {
                  const quantity = basket.get(item.code) ?? 0;
                  const otherQuantities = others.map((other) => other.get(item.code) ?? 0);
                  const firstWithCode = plan.items.findIndex((entry) => entry.code === item.code) === itemIndex;
                  const isDifferent = firstWithCode && otherQuantities.some((otherQuantity) => otherQuantity !== quantity);

                  return (
                  <div className="food-item" key={`${item.code}-${itemIndex}`}>
                    <FoodImage item={item} transitionId={planTransitionId(plan.plan_id, `food-${itemIndex}`)} />
                    <div className="food-info">
                      <strong>{item.name}</strong>
                      <p>
                        <span>× {item.quantity}</span>
                        <span>{item.line_price_basis === 'live_quote' ? '小计' : '参考'} ¥{formatMoney(item.line_total_fen)}</span>
                        <span className={`spicy-tag ${item.spicy_level}`}>
                          {spicyLabels[item.spicy_level]}
                        </span>
                      </p>
                      {item.spec?.trim() && <p className="plan-spec">{item.spec}</p>}
                      {isDifferent && (
                        <p className="item-comparison" aria-label={`${item.name}的方案差异`}>
                          {otherQuantities.every((otherQuantity) => otherQuantity === 0)
                            ? '本方案独有'
                            : `本方案共 × ${quantity} · 其他方案 × ${otherQuantityLabel(otherQuantities)}`}
                        </p>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>

              {missingItems.length > 0 && (
                <p className="plan-comparison-note" aria-label="未含的差异商品">
                  未含：{missingItems.map((item) => `${item.name}（其他方案 × ${otherQuantityLabel(others.map((other) => other.get(item.code) ?? 0))}）`).join('、')}
                </p>
              )}
              {others.length > 0 && !hasDifferences && <p className="plan-comparison-note">各方案商品与数量相同</p>}

              {reason && reason !== summary && <p className="plan-reason">{reason}</p>}

              {plan.checks.length > 0 && (
                <div className="plan-checks" aria-label="方案约束检查">
                  {plan.checks.map((check, checkIndex) => (
                    <span
                      key={`${check.name}-${checkIndex}`}
                      className={check.satisfied ? undefined : 'failed'}
                      title={check.detail}
                    >
                      {check.satisfied
                        ? <CheckCircle2 size={12} aria-hidden="true" />
                        : <XCircle size={12} aria-hidden="true" />}
                      {check.name}：{check.satisfied ? '已满足' : '未满足'}
                    </span>
                  ))}
                </div>
              )}

              <div className="plan-bottom">
                <span className="plan-serving-count">共 {plan.servings_total} 份餐品</span>
                <motion.button
                  className="choose-button"
                  type="button"
                  aria-label={`就选这份${title}`}
                  disabled={disabled || !plan.quote_id}
                  onClick={() => onSelect(plan)}
                  {...buttonMotion(reducedMotion, disabled || !plan.quote_id)}
                >
                  就选这份<ArrowRight size={15} aria-hidden="true" />
                </motion.button>
              </div>

              <details className="plan-detail">
                <summary>查看单品营养信息</summary>
                <div className="nutrition-content">
                  {plan.items.map((item, itemIndex) => (
                    <div key={`${item.code}-nutrition-${itemIndex}`}>
                      <strong>{item.name}（单品记录值）</strong>
                      <div>
                        能量 {nutritionValue(item.nutrition?.energy_kcal, 'kcal')} ·{' '}
                        蛋白质 {nutritionValue(item.nutrition?.protein_g, 'g')} ·{' '}
                        脂肪 {nutritionValue(item.nutrition?.fat_g, 'g')} ·{' '}
                        碳水化合物 {nutritionValue(item.nutrition?.carbohydrate_g, 'g')} ·{' '}
                        钠 {nutritionValue(item.nutrition?.sodium_mg, 'mg')}
                      </div>
                      <div>
                        匹配状态：{item.nutrition == null
                          ? '暂无数据'
                          : item.nutrition.matched ? '已匹配' : '未匹配'}
                        {item.nutrition?.source?.trim() && ` · 来源：${item.nutrition.source}`}
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            </div>
          </motion.article>
        );
      })}
    </div>
  );
}
