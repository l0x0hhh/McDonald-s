import type { PlanType } from './types'

export const planTitles: Record<PlanType, string> = {
  cheapest: '最省方案',
  balanced: '搭配多样',
  taste: '口味匹配',
}

// A short, interruptible hand-off, not a gate before checkout can be used.
export const planSharedTransition = {
  type: 'tween' as const,
  duration: .32,
  ease: [.22, 1, .36, 1] as [number, number, number, number],
}

export function planTransitionId(planId: string, part: 'title' | 'total' | `food-${number}`) {
  return `kitchen-plan:${encodeURIComponent(planId)}:${part}`
}

export function sharedPlanElement(id: string | undefined, reducedMotion: boolean) {
  const activeId = reducedMotion ? undefined : id
  return {
    layoutId: activeId,
    layout: activeId ? 'position' as const : false,
    'data-plan-shared-id': activeId,
  }
}
