import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import * as api from '../src/lib/api'
import stores from '../../docs/api-examples/stores.json'
import type { OrderResponse } from '../src/lib/types'
import { planResponse } from './helpers/online-api'

const officialOrder = (): OrderResponse => ({
  order_id: 'existing-order-8340', plan_id: 'existing-plan', store_code: '1400011',
  items: [{ code: 'burger', name: '官方双层汉堡', quantity: 2, unit_price_fen: 4170, original_unit_price_fen: 4170, line_total_fen: 8340, servings: 2, spicy_level: 'mild' }],
  total_fen: 8340, status: 'pending_payment', payment_url: 'https://m.mcd.cn/pay?id=existing-order-8340',
  expires_at: '2099-10-25T23:59:59+08:00', created_at: '2026-10-25T12:00:00+08:00',
})
const navigate = vi.fn()
beforeEach(() => {
  sessionStorage.clear(); navigate.mockReset()
  vi.spyOn(api, 'getStores').mockResolvedValue({ ...structuredClone(stores.data), data_source: 'live_mcp' })
  vi.spyOn(api, 'getOrder').mockResolvedValue(officialOrder())
  vi.spyOn(api, 'createOrder').mockResolvedValue(officialOrder())
})
function mountOldOrder() {
  sessionStorage.setItem('kitchen-order', JSON.stringify({ ...officialOrder(), status: 'unknown', payment_url: null }))
  return render(<App navigate={navigate} />)
}
async function openReview() {
  fireEvent.click(await screen.findByRole('button', { name: '核对订单后支付' }))
  return screen.getByRole('dialog', { name: '核对官方订单' })
}

describe('explicit existing official order payment recovery', () => {
  it('never substitutes cached payment intent for the explicit displayed-order approval', async () => {
    const { order_id, plan_id, store_code, total_fen } = officialOrder()
    sessionStorage.setItem('kitchen-payment-intent', JSON.stringify({ order_id, plan_id, store_code, total_fen }))
    mountOldOrder()
    await screen.findByRole('button', { name: '核对订单后支付' })
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('requires displayed-order confirmation after a matched create unknown becomes pending on GET', async () => {
    const plans = planResponse()
    plans.plans = plans.plans.slice(0, 1).map(plan => ({ ...plan, take_way_options: [{ code: 'TAKE', title: '到店自取' }] }))
    vi.spyOn(api, 'getPlans').mockResolvedValue(plans)
    const created = { ...officialOrder(), plan_id: plans.plans[0].plan_id, total_fen: plans.plans[0].total_fen }
    vi.mocked(api.createOrder).mockResolvedValue({ ...created, status: 'unknown', payment_url: null })
    vi.mocked(api.getOrder).mockResolvedValue(created)
    render(<App navigate={navigate} />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(screen.getByRole('button', { name: '看看怎么点' }))
    fireEvent.click(await screen.findByRole('button', { name: /就选这份/ }))
    await screen.findByText(/· 状态待核实 ·/)
    fireEvent.click(screen.getByRole('button', { name: '查询订单状态' }))
    await screen.findByRole('button', { name: '核对订单后支付' })
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(sessionStorage.getItem('kitchen-submission')).toBeNull()
    expect(navigate).not.toHaveBeenCalled()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })

  it('requires an official GET and two explicit clicks before authorizing the displayed old unknown order', async () => {
    let resolve!: (order: OrderResponse) => void
    vi.mocked(api.getOrder).mockImplementationOnce(() => new Promise(yes => { resolve = yes }))
    mountOldOrder()
    await screen.findByRole('button', { name: /广州今日广场/ })
    expect(screen.queryByRole('button', { name: '核对订单后支付' })).not.toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
    await act(async () => resolve(officialOrder()))
    const dialog = await openReview()
    expect(within(dialog).getByText(/existing-order-8340/)).toBeInTheDocument()
    expect(within(dialog).getByText(/1400011/)).toBeInTheDocument()
    expect(within(dialog).getByText('官方双层汉堡')).toBeInTheDocument()
    expect(within(dialog).getByText(/× 2/)).toBeInTheDocument()
    expect(within(dialog).getByText('¥83.40')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '确认订单，前往支付' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(navigate).not.toHaveBeenCalled()
    expect(api.getOrder).toHaveBeenCalledTimes(1)
    fireEvent.click(within(dialog).getByRole('button', { name: '确认订单，前往支付' }))
    await waitFor(() => expect(navigate).toHaveBeenCalledExactlyOnceWith(officialOrder().payment_url))
    expect(api.getOrder).toHaveBeenCalledTimes(2)
    expect(api.getOrder).toHaveBeenLastCalledWith('existing-order-8340')
    expect(api.createOrder).not.toHaveBeenCalled()
    expect(JSON.parse(sessionStorage.getItem('kitchen-payment-approval')!)).toMatchObject({
      order_id: 'existing-order-8340', plan_id: 'existing-plan', store_code: '1400011', total_fen: 8340,
      payment_url: 'https://m.mcd.cn/pay?id=existing-order-8340',
    })
    expect(await screen.findByRole('link', { name: '继续前往官方支付' })).toHaveAttribute('href', officialOrder().payment_url)
  })

  it('does not navigate while the confirmation GET is pending and deduplicates repeated confirms', async () => {
    mountOldOrder()
    const dialog = await openReview()
    let resolve!: (order: OrderResponse) => void
    vi.mocked(api.getOrder).mockImplementationOnce(() => new Promise(yes => { resolve = yes }))
    const confirm = within(dialog).getByRole('button', { name: '确认订单，前往支付' })
    act(() => { confirm.click(); confirm.click() })
    expect(navigate).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(api.getOrder).toHaveBeenCalledTimes(2)
    await act(async () => resolve(officialOrder()))
    expect(navigate).toHaveBeenCalledExactlyOnceWith(officialOrder().payment_url)
    expect(api.createOrder).not.toHaveBeenCalled()
  })

  it.each([
    ['order', { order_id: 'different-order' }],
    ['plan', { plan_id: 'changed-plan' }],
    ['store', { store_code: 'changed-store' }],
    ['amount', { total_fen: 9340 }],
    ['url', { payment_url: 'https://m.mcd.cn/pay?id=changed-url' }],
    ['expiry', { expires_at: '2099-10-26T23:59:59+08:00' }],
    ['items', { items: [{ ...officialOrder().items[0], quantity: 3 }] }],
    ['status', { status: 'paid' as const }],
  ])('does not authorize when the confirmation GET changes %s', async (_field, patch) => {
    mountOldOrder()
    const dialog = await openReview()
    vi.mocked(api.getOrder).mockResolvedValue({ ...officialOrder(), ...patch })
    fireEvent.click(within(dialog).getByRole('button', { name: '确认订单，前往支付' }))
    await screen.findAllByText(/重新核对|已支付|响应与当前订单不一致/)
    expect(navigate).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(api.createOrder).not.toHaveBeenCalled()
  })

  it('requires another explicit review after amount drift before a later stable GET can authorize', async () => {
    mountOldOrder()
    let dialog = await openReview()
    vi.mocked(api.getOrder).mockResolvedValue({ ...officialOrder(), total_fen: 9340 })
    fireEvent.click(within(dialog).getByRole('button', { name: '确认订单，前往支付' }))
    await screen.findByText(/重新核对/)
    expect(navigate).not.toHaveBeenCalled()
    dialog = screen.getByRole('dialog', { name: '核对官方订单' })
    expect(within(dialog).getByText('¥93.40')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '确认订单，前往支付' }))
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1))
    expect(JSON.parse(sessionStorage.getItem('kitchen-payment-approval')!).total_fen).toBe(9340)
    expect(api.createOrder).not.toHaveBeenCalled()
  })

  it.each([
    { payment_url: 'https://attacker.test/pay' },
    { expires_at: '2020-01-01T00:00:00+08:00' },
    { expires_at: 'invalid-expiry' },
    { status: 'paid' as const },
  ])('does not offer payment review for an invalid or nonpending official order %j', async patch => {
    vi.mocked(api.getOrder).mockResolvedValue({ ...officialOrder(), ...patch })
    mountOldOrder()
    await waitFor(() => expect(api.getOrder).toHaveBeenCalledTimes(1))
    await act(async () => {})
    expect(screen.queryByRole('button', { name: '核对订单后支付' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
    expect(api.createOrder).not.toHaveBeenCalled()
  })

  it('discards a pending confirmation when the user closes its review dialog', async () => {
    mountOldOrder()
    const dialog = await openReview()
    let resolve!: (order: OrderResponse) => void
    vi.mocked(api.getOrder).mockImplementationOnce(() => new Promise(yes => { resolve = yes }))
    fireEvent.click(within(dialog).getByRole('button', { name: '确认订单，前往支付' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭弹窗' }))
    await act(async () => resolve(officialOrder()))
    expect(navigate).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(api.createOrder).not.toHaveBeenCalled()
  })

  it('keeps an uncertain submission fully blocked even with an older known pending order', async () => {
    sessionStorage.setItem('kitchen-submission', 'unknown')
    mountOldOrder()
    await screen.findByRole('button', { name: /广州今日广场/ })
    expect(screen.queryByRole('button', { name: '核对订单后支付' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查询订单状态' })).toBeDisabled()
    expect(api.getOrder).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
    expect(api.createOrder).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('kitchen-submission')).toBe('unknown')
  })
})
