import type { ComponentType } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import PlanCards from '../src/components/PlanCards'
import type { Plan, PlanItem } from '../src/lib/types'

type ComparisonProps = {
  plans: Plan[]
  onSelect: (plan: Plan) => void
  people?: number
  nonSpicyPeople?: number
}

// The cast lets the pre-feature component run unchanged in the RED phase.
const ComparisonCards = PlanCards as ComponentType<ComparisonProps>

const burger: PlanItem = {
  code: 'burger', name: '测试汉堡', quantity: 2, unit_price_fen: 1200,
  original_unit_price_fen: 1200, line_total_fen: 2400, servings: 1,
  spicy_level: 'unknown', nutrition: null,
}
const fries: PlanItem = { ...burger, code: 'fries', name: '测试薯条', quantity: 1 }
const plan: Plan = {
  plan_id: 'comparison-one', quote_id: 'quote-one', type: 'cheapest', title: '记录中的方案', summary: '',
  items: [burger], subtotal_fen: 9999, total_fen: 2400, budget_fen: 3000,
  remaining_fen: 615, servings_total: 9, non_spicy_servings: 2,
  reasons: [], checks: [],
}

function metric(label: string) {
  const metrics = screen.getByLabelText('方案关键对比')
  return within(metrics).getByText(label).parentElement!
}

describe('plan comparison at a glance', () => {
  it('shows response total and remainder directly and divides by the submitted people, not servings', () => {
    render(<ComparisonCards plans={[plan]} people={2} nonSpicyPeople={2} onSelect={() => {}} />)

    expect(metric('总价')).toHaveTextContent('¥24.00')
    expect(metric('人均')).toHaveTextContent('¥12.00')
    expect(metric('人均')).toHaveTextContent('2 人')
    expect(metric('预算剩余')).toHaveTextContent('¥6.15')
    expect(metric('不辣主食')).toHaveTextContent('2 / 需 2 份')
    expect(metric('不辣主食')).toHaveTextContent('已满足 · 本地判断')
    expect(screen.queryByText('不辣保障')).not.toBeInTheDocument()
    expect(screen.getByRole('article').querySelectorAll('.price')).toHaveLength(1)
  })

  it.each([undefined, 0, -2, Number.NaN, Number.POSITIVE_INFINITY, 1.5])(
    'does not invent a per-person amount for an invalid people count (%s)', (people) => {
      render(<ComparisonCards plans={[plan]} people={people} onSelect={() => {}} />)
      expect(metric('人均')).toHaveTextContent('—')
      expect(metric('人均')).not.toHaveTextContent('¥')
    },
  )

  it('shows an over-budget response as a deficit instead of a negative remaining balance', () => {
    render(<ComparisonCards plans={[{ ...plan, remaining_fen: -250 }]} people={2} onSelect={() => {}} />)
    expect(metric('超预算')).toHaveTextContent('¥2.50')
    expect(metric('超预算')).not.toHaveTextContent('-')
    expect(screen.queryByText('预算剩余')).not.toBeInTheDocument()
  })

  it('does not count unknown-spice items as non-spicy supply', () => {
    render(<ComparisonCards plans={[{ ...plan, non_spicy_servings: 0 }]} people={2} nonSpicyPeople={1} onSelect={() => {}} />)
    expect(metric('不辣主食')).toHaveTextContent('0 / 需 1 份')
    expect(metric('不辣主食')).toHaveTextContent('还差 1 份')
    expect(metric('不辣主食')).not.toHaveTextContent('已满足')
    expect(screen.getByText('辣度未知')).toBeInTheDocument()
  })

  it('distinguishes no non-spicy requirement from a missing requirement', () => {
    const { rerender } = render(<ComparisonCards plans={[plan]} people={2} nonSpicyPeople={0} onSelect={() => {}} />)
    expect(metric('不辣主食')).toHaveTextContent('无不辣要求')
    expect(metric('不辣主食')).not.toHaveTextContent('还差')

    rerender(<ComparisonCards plans={[plan]} people={2} onSelect={() => {}} />)
    expect(metric('不辣主食')).toHaveTextContent('需求未确认')
    expect(metric('不辣主食')).not.toHaveTextContent('已满足')
  })

  it('marks unique products by code and shows products omitted from the current plan', () => {
    const second: Plan = { ...plan, plan_id: 'comparison-two', type: 'balanced', items: [burger, fries] }
    render(<ComparisonCards plans={[plan, second]} people={2} nonSpicyPeople={2} onSelect={() => {}} />)

    const firstCard = screen.getByRole('article', { name: '最省方案' })
    const secondCard = screen.getByRole('article', { name: '搭配多样' })
    expect(within(secondCard).getByLabelText('测试薯条的方案差异')).toHaveTextContent('本方案独有')
    expect(within(firstCard).getByLabelText('未含的差异商品')).toHaveTextContent('测试薯条')
    expect(within(firstCard).queryByLabelText('测试汉堡的方案差异')).not.toBeInTheDocument()
  })

  it('aggregates repeated product codes before comparing quantities', () => {
    const first: Plan = { ...plan, items: [{ ...burger, quantity: 1 }, { ...burger, quantity: 2 }] }
    const second: Plan = { ...plan, plan_id: 'comparison-two', type: 'balanced', items: [{ ...burger, quantity: 1 }] }
    render(<ComparisonCards plans={[first, second]} people={2} onSelect={() => {}} />)
    const firstCard = screen.getByRole('article', { name: '最省方案' })
    const difference = within(firstCard).getByLabelText('测试汉堡的方案差异')
    expect(difference).toHaveTextContent('本方案共 × 3')
    expect(difference).toHaveTextContent('其他方案 × 1')
    expect(within(firstCard).getAllByLabelText('测试汉堡的方案差异')).toHaveLength(1)
  })

  it('does not treat two product codes with the same name as identical', () => {
    const second: Plan = { ...plan, plan_id: 'comparison-two', type: 'taste', items: [{ ...burger, code: 'different-burger' }] }
    render(<ComparisonCards plans={[plan, second]} people={2} onSelect={() => {}} />)
    const firstCard = screen.getByRole('article', { name: '最省方案' })
    expect(within(firstCard).getByLabelText('测试汉堡的方案差异')).toHaveTextContent('本方案独有')
    expect(within(firstCard).getByLabelText('未含的差异商品')).toHaveTextContent('测试汉堡')
  })

  it('does not invent a product difference for a single plan or identical baskets', () => {
    const { rerender } = render(<ComparisonCards plans={[plan]} people={2} onSelect={() => {}} />)
    expect(screen.queryByLabelText('测试汉堡的方案差异')).not.toBeInTheDocument()
    expect(screen.queryByText('各方案商品与数量相同')).not.toBeInTheDocument()

    rerender(<ComparisonCards plans={[plan, { ...plan, plan_id: 'comparison-two', type: 'taste' }]} people={2} onSelect={() => {}} />)
    expect(screen.getAllByText('各方案商品与数量相同')).toHaveLength(2)
  })

  it('keeps the selection contract and collapsible unknown nutrition while deduplicating identical explanation text', () => {
    const onSelect = vi.fn()
    const documented = { ...plan, summary: '原始搭配说明', reasons: ['原始搭配说明'] }
    render(<ComparisonCards plans={[documented]} people={2} onSelect={onSelect} />)
    expect(screen.getAllByText('原始搭配说明')).toHaveLength(1)
    expect(screen.getByRole('article').querySelector('details')).not.toHaveAttribute('open')
    expect(screen.getByText(/能量 暂无数据 · 蛋白质 暂无数据/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '就选这份最省方案' }))
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(documented)
  })
})
