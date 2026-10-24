import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import * as api from '../src/lib/api'
import type { OrderResponse } from '../src/lib/types'
import type { StoreListResponse } from '../src/lib/types'

describe('kitchen ordering experience', () => {
  it('shows the kitchen brief and an honestly labelled offline mode', async () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: /今天.*开饭/ })).toBeInTheDocument()
    expect(screen.getByText('离线样例')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /广州今日广场/ })).toBeInTheDocument()
  })

  it('generates plans and completes a clearly simulated order', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '生成省钱方案' }))
    const choose = await screen.findAllByRole('button', { name: /就选这份/ })
    fireEvent.click(choose[0])
    expect(await screen.findByRole('dialog', { name: '确认这份开饭计划' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认模拟结算' }))
    expect(await screen.findByText('开饭计划已就位！')).toBeInTheDocument()
    expect(screen.getByText(/未产生真实订单或扣款/)).toBeInTheDocument()
  })

  it('clears old plans immediately when a requirement changes', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '生成省钱方案' }))
    await screen.findAllByRole('button', { name: /就选这份/ })
    fireEvent.click(screen.getByRole('button', { name: '增加用餐人数' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /就选这份/ })).not.toBeInTheDocument())
    expect(screen.getByLabelText('用餐人数')).toHaveValue(7)
  })

  it('displays an explicit no-solution state instead of inventing cheap plans', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.change(screen.getByLabelText('预算金额'), { target: { value: '30' } })
    fireEvent.click(screen.getByRole('button', { name: '生成省钱方案' }))
    expect(await screen.findByRole('heading', { name: '这单，还差一点点' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /就选这份/ })).not.toBeInTheDocument()
  })

  it('does not silently discard a parsed appetite constraint', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.change(screen.getByLabelText(/和小厨神说一声/), { target: { value: '6人，总预算150元，2人不吃辣，饭量大' } })
    fireEvent.click(screen.getByRole('button', { name: '发送点餐需求' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled())
    expect(await screen.findByText(/饭量与口味规划尚待接入/)).toBeInTheDocument()
  })

  it('ignores an old checkout response after its dialog was closed', async () => {
    let resolveOrder!: (order: OrderResponse) => void
    const orderSpy = vi.spyOn(api, 'createOrder').mockImplementationOnce(() => new Promise((resolve) => { resolveOrder = resolve }))
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(screen.getByRole('button', { name: '生成省钱方案' }))
    const choose = await screen.findAllByRole('button', { name: /就选这份/ })
    fireEvent.click(choose[0])
    fireEvent.click(await screen.findByRole('button', { name: '确认模拟结算' }))
    fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    fireEvent.click(choose[1])
    await act(async () => resolveOrder({ order_id: 'late', plan_id: 'late', store_code: '1400011', items: [], total_fen: 10140, status: 'simulated', is_demo: true, notice: 'old response', created_at: '2026-10-24' }))
    expect(screen.getByRole('dialog', { name: '确认这份开饭计划' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '开饭计划已就位！' })).not.toBeInTheDocument()
    orderSpy.mockRestore()
  })

  it('keeps an unreleased preference pending when only the budget is corrected', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.change(screen.getByLabelText(/和小厨神说一声/), { target: { value: '饭量大' } })
    fireEvent.click(screen.getByRole('button', { name: '发送点餐需求' }))
    await screen.findByText(/饭量与口味规划尚待接入/)
    fireEvent.change(screen.getByLabelText(/和小厨神说一声/), { target: { value: '预算改为180元' } })
    fireEvent.click(screen.getByRole('button', { name: '发送点餐需求' }))
    await screen.findByDisplayValue('180')
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
  })

  it('does not let a late default-store response clear an unreleased preference', async () => {
    const stores = await api.getStores('demo', '广州', '')
    let resolveStores!: (value: StoreListResponse) => void
    const storeSpy = vi.spyOn(api, 'getStores').mockImplementationOnce(() => new Promise((resolve) => { resolveStores = resolve }))
    render(<App />)
    fireEvent.change(screen.getByLabelText(/和小厨神说一声/), { target: { value: '饭量大' } })
    fireEvent.click(screen.getByRole('button', { name: '发送点餐需求' }))
    await screen.findByText(/饭量与口味规划尚待接入/)
    await act(async () => resolveStores(stores))
    await screen.findByRole('button', { name: /广州今日广场/ })
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
    storeSpy.mockRestore()
  })

  it('clears generated plans when the user switches store', async () => {
    render(<App />)
    const storeButton = await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(screen.getByRole('button', { name: '生成省钱方案' }))
    await screen.findAllByRole('button', { name: /就选这份/ })
    fireEvent.click(storeButton)
    fireEvent.click(await screen.findByRole('button', { name: /广州维多利广场/ }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /就选这份/ })).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '麦当劳广州维多利广场餐厅，切换门店' })).toBeInTheDocument()
  })

  it('requires an explicit base-requirements choice to clear a pending preference', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.change(screen.getByLabelText(/和小厨神说一声/), { target: { value: '饭量大' } })
    fireEvent.click(screen.getByRole('button', { name: '发送点餐需求' }))
    await screen.findByText(/饭量与口味规划尚待接入/)
    fireEvent.change(screen.getByLabelText('预算金额'), { target: { value: '180' } })
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '使用基础需求' }))
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeEnabled()
  })
})
