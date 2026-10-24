import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import PlanCards from '../src/components/PlanCards'
import type { Plan } from '../src/lib/types'

const plan: Plan = {
  plan_id: 'plan-one',
  type: 'cheapest',
  title: '记录中的方案',
  summary: '',
  items: [{
    code: 'food-one',
    name: '测试汉堡',
    quantity: 2,
    unit_price_fen: 1200,
    original_unit_price_fen: 1200,
    line_total_fen: 2400,
    servings: 1,
    spicy_level: 'unknown',
    spec: '  ',
    nutrition: null,
  }],
  subtotal_fen: 2400,
  total_fen: 2400,
  budget_fen: 3000,
  remaining_fen: 600,
  servings_total: 2,
  non_spicy_servings: 0,
  reasons: [],
  checks: [],
}

describe('plan cards', () => {
  it('keeps the station emblem decorative and the card heading accessible', () => {
    render(<PlanCards plans={[plan]} onSelect={() => {}} />)

    const heading = screen.getByRole('heading', { name: '最省方案' })
    const stationIcon = heading.querySelector('.card-station-icon')
    expect(stationIcon).toHaveAttribute('aria-hidden', 'true')
    expect(stationIcon?.querySelector('svg')).toBeInTheDocument()
    expect(screen.getByRole('article', { name: '最省方案' })).not.toHaveAttribute('tabindex')
  })

  it('renders only the supplied plans and selects the exact chosen plan', () => {
    const onSelect = vi.fn()
    const secondPlan: Plan = { ...plan, plan_id: 'plan-two', type: 'taste' }
    const { rerender } = render(<PlanCards plans={[plan, secondPlan]} onSelect={onSelect} />)

    expect(screen.getAllByRole('article')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: '就选这份口味匹配' }))
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(secondPlan)

    rerender(<PlanCards plans={[]} onSelect={onSelect} />)
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /就选这份/ })).not.toBeInTheDocument()
  })

  it('retains unknown spice and nutrition values without inventing a specification', () => {
    render(<PlanCards plans={[plan]} onSelect={() => {}} />)

    const card = screen.getByRole('article', { name: '最省方案' })
    expect(within(card).getByText('辣度未知')).toBeInTheDocument()
    expect(within(card).getByText('× 2')).toBeInTheDocument()
    expect(card.querySelector('.plan-spec')).not.toBeInTheDocument()

    const details = card.querySelector('details')
    expect(details).not.toHaveAttribute('open')
    expect(within(card).getByText('查看单品营养信息')).toBeInTheDocument()
    expect(within(card).getByText(/能量 未知 · 蛋白质 未知 · 脂肪 未知 · 碳水化合物 未知 · 钠 未知/)).toBeInTheDocument()
    expect(within(card).getByText('匹配状态：未知')).toBeInTheDocument()
  })
})
