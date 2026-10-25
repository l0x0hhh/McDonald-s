import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import SettingsDialog from '../src/components/SettingsDialog'
import stores from '../../docs/api-examples/stores.json'
import { planResponse } from './helpers/online-api'

const configured = { provider: 'deepseek', model: 'deepseek-flash', deepseek_configured: true, mcd_mcp_configured: true }
const envelope = (data: unknown) => new Response(JSON.stringify({ code: 0, data, msg: 'ok' }))
let twoWays: boolean
const plans = () => {
  const data = planResponse()
  data.plans = data.plans.slice(0, 1).map(plan => ({ ...plan, take_way_options: twoWays ? [{ code: 'TAKE', title: '到店自取' }, { code: 'EAT', title: '堂食' }] : [{ code: 'TAKE', title: '到店自取' }] }))
  return data
}
let save: () => Promise<Response>
let order: () => Promise<Response>
let settingsPosts: number
let orderPosts: number
beforeEach(() => {
  sessionStorage.clear(); settingsPosts = 0; orderPosts = 0; twoWays = false
  save = async () => envelope(configured)
  order = async () => envelope({ order_id: 'fixture-order', plan_id: plans().plans[0].plan_id, store_code: '1400011', items: [], total_fen: plans().plans[0].total_fen, status: 'cancelled', created_at: 'now' })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    if (String(url).includes('/stores?')) return envelope({ ...stores.data, data_source: 'live_mcp' })
    if (String(url).endsWith('/plans')) return envelope(plans())
    if (String(url).endsWith('/orders')) { orderPosts += 1; return order() }
    if (String(url).endsWith('/settings')) {
      if (init.method === 'GET') return envelope(configured)
      settingsPosts += 1; return save()
    }
    throw new Error('Unexpected fixture URL')
  }))
})
afterEach(() => vi.unstubAllGlobals())

async function start() {
  render(<App navigate={vi.fn()} />)
  await screen.findByRole('button', { name: /广州今日广场/ })
}
async function open() {
  fireEvent.click(screen.getByRole('button', { name: '设置' }))
  const dialog = screen.getByRole('dialog', { name: '设置' })
  await waitFor(() => expect(within(dialog).getByLabelText('DeepSeek 配置状态')).toHaveTextContent('已配置'))
  return dialog
}
async function generate() {
  fireEvent.click(screen.getByRole('button', { name: '看看怎么点' }))
  return await screen.findByRole('button', { name: /就选这份/ })
}

describe('app-wide settings and checkout transaction lock', () => {
  it('keeps a pending save locked across close/reopen, then permits a later explicit save', async () => {
    let resolve!: (value: Response) => void
    save = () => new Promise(yes => { resolve = yes })
    await start()
    let dialog = await open()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭弹窗' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    dialog = await open()
    expect(within(dialog).getByRole('button', { name: '正在保存…' })).toBeDisabled()
    fireEvent.submit(dialog.querySelector('form')!)
    expect(settingsPosts).toBe(1)
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/正在保存/)
    await act(async () => resolve(envelope(configured)))
    await waitFor(() => expect(within(dialog).getByRole('button', { name: '保存设置' })).toBeEnabled())
    save = async () => envelope(configured)
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await within(dialog).findByText('设置已保存。')
    expect(settingsPosts).toBe(2)
  })

  it('invalidates old plans as saving starts and rejects a same-tick stale plan click', async () => {
    let resolve!: (value: Response) => void
    save = () => new Promise(yes => { resolve = yes })
    await start()
    const staleChoose = await generate()
    const dialog = await open()
    const saveButton = within(dialog).getByRole('button', { name: '保存设置' })
    act(() => { saveButton.click(); staleChoose.click() })
    expect(orderPosts).toBe(0)
    expect(settingsPosts).toBe(1)
    await waitFor(() => expect(screen.queryAllByRole('button', { name: /就选这份/ })).toHaveLength(0))
    expect(screen.getByRole('button', { name: '看看怎么点' })).toBeDisabled()
    await act(async () => resolve(envelope(configured)))
    await within(dialog).findByText('设置已保存。')
  })

  it('rejects settings submission while an order is already submitting and permits a retry after it settles', async () => {
    let resolve!: (value: Response) => void
    const completed = await order()
    order = () => new Promise(yes => { resolve = yes })
    await start()
    const choose = await generate()
    const trigger = screen.getByRole('button', { name: '设置' })
    // Batched clicks exercise stale rendered controls before orderBusy disables the trigger.
    act(() => { trigger.click(); choose.click() })
    const dialog = screen.getByRole('dialog', { name: '设置' })
    await waitFor(() => expect(within(dialog).getByLabelText('DeepSeek API Key')).toBeEnabled())
    expect(orderPosts).toBe(1)
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/订单正在提交/)
    expect(settingsPosts).toBe(0)
    await act(async () => resolve(completed))
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await within(dialog).findByText('设置已保存。')
    expect(settingsPosts).toBe(1)
  })

  it('rejects a stale checkout pay handler in the same tick that settings saving starts', async () => {
    twoWays = true
    let resolve!: (value: Response) => void
    save = () => new Promise(yes => { resolve = yes })
    await start()
    fireEvent.click(await generate())
    const receipt = screen.getByRole('dialog', { name: '选择取餐方式' })
    fireEvent.change(within(receipt).getByLabelText('取餐方式'), { target: { value: 'TAKE' } })
    const stalePay = within(receipt).getByRole('button', { name: '前往支付' })
    // Exercise an already-rendered checkout callback directly; Modal's inert UI is
    // not a transaction lock and cannot replace the synchronous checkout guard.
    const dialog = await open()
    const saveButton = within(dialog).getByRole('button', { name: '保存设置' })
    act(() => { saveButton.click(); stalePay.click() })
    expect(orderPosts).toBe(0)
    expect(settingsPosts).toBe(1)
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '选择取餐方式' })).not.toBeInTheDocument())
    await act(async () => resolve(envelope(configured)))
    await within(dialog).findByText('设置已保存。')
  })

  it('keeps an invalidated plan callback stale after saving settles while its exit animation is still present', async () => {
    let resolve!: (value: Response) => void
    save = () => new Promise(yes => { resolve = yes })
    await start()
    const staleChoose = await generate()
    const dialog = await open()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await act(async () => resolve(envelope(configured)))
    expect(within(dialog).getByRole('button', { name: '保存设置' })).toBeEnabled()
    expect(staleChoose).toBeInTheDocument()
    fireEvent.click(staleChoose)
    expect(orderPosts).toBe(0)
  })

  it('makes a refused parent save acquisition visible and never dispatches or settles an unowned save', async () => {
    const settled = vi.fn()
    render(<SettingsDialog globalSaving={false} onSaveStart={() => false} onSaved={vi.fn()} onSettled={settled} />)
    await waitFor(() => expect(screen.getByLabelText('DeepSeek API Key')).toBeEnabled())
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), { target: { value: 'fixture-only-key' } })
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/正在保存|订单正在提交/)
    expect(screen.getByLabelText('DeepSeek API Key')).toHaveValue('fixture-only-key')
    expect(settingsPosts).toBe(0)
    expect(settled).not.toHaveBeenCalled()
  })
})
