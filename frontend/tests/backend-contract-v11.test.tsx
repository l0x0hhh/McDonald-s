import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import * as api from '../src/lib/api'
import type { ChatResponse, Requirements } from '../src/lib/types'

const pendingDraft = {
  people: 2, budget_type: null, budget_fen: null,
  dietary_constraints: [], appetite: 'normal', preferred_tags: [],
  exclude_beverage: false, pending_fields: ['budget'], notes: [],
}

function incomplete(draft = pendingDraft): ChatResponse {
  return {
    requirements: null, draft, missing_fields: ['budget'],
    follow_up: '请确认这两位的总预算。', ready: false, parse_source: 'rule', notes: [],
  } as ChatResponse
}

function ready(excludeBeverage = false): ChatResponse {
  return {
    requirements: { ...api.DEMO_REQUIREMENTS, people: 2, budget_fen: 10000, dietary_constraints: [], exclude_beverage: excludeBeverage },
    missing_fields: [], ready: true, parse_source: 'rule', notes: [],
  } as ChatResponse
}

async function openApi() {
  const stores = await api.getStores('demo', '广州', '')
  vi.spyOn(api, 'getStores').mockResolvedValue(stores)
  render(<App />)
  await screen.findByRole('button', { name: /广州今日广场/ })
  fireEvent.click(screen.getByRole('button', { name: '连接后端' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeEnabled())
}

async function say(message: string) {
  fireEvent.change(screen.getByLabelText(/和小厨神说一声/), { target: { value: message } })
  fireEvent.click(screen.getByRole('button', { name: '发送点餐需求' }))
  await waitFor(() => expect(screen.getByLabelText(/和小厨神说一声/)).toHaveValue(''))
}

describe('A/B V1.1 contract integration without silent constraint loss', () => {
  it('blocks an unexposed no-beverage constraint until the explicit base-requirements choice', async () => {
    const parse = vi.spyOn(api, 'parseChat').mockResolvedValue(ready(true))
    const plans = vi.spyOn(api, 'getPlans')
    await openApi()
    await say('2人，总预算100元，不要饮料')
    expect(parse).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('无饮料')
    expect(plans).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '使用基础需求' }))
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeEnabled()
  })

  it('keeps a no-beverage constraint from an incomplete draft blocked after a form edit', async () => {
    vi.spyOn(api, 'parseChat').mockResolvedValue(incomplete({ ...pendingDraft, exclude_beverage: true }))
    await openApi()
    await say('两个人，不要饮料')
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('预算金额'), { target: { value: '180' } })
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('无饮料')
  })

  it('passes the previous incomplete backend draft instead of replacing it with complete default form values', async () => {
    const parse = vi.spyOn(api, 'parseChat').mockResolvedValueOnce(incomplete()).mockResolvedValueOnce(ready())
    await openApi()
    await say('这次两个人')
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
    await say('总预算100元')
    expect(parse.mock.calls[1][2]).toEqual(pendingDraft)
    expect(screen.getByLabelText('用餐人数')).toHaveValue(2)
    expect(screen.getByLabelText('预算金额')).toHaveValue(100)
  })

  it.each(['form', 'sample', 'mode', 'store'] as const)('clears a backend draft when the user changes %s', async (action) => {
    const parse = vi.spyOn(api, 'parseChat').mockResolvedValueOnce(incomplete()).mockResolvedValueOnce(ready())
    await openApi()
    await say('这次两个人')
    if (action === 'form') fireEvent.change(screen.getByLabelText('预算金额'), { target: { value: '180' } })
    if (action === 'sample') fireEvent.click(screen.getByRole('button', { name: '6 人聚餐 · 150 元' }))
    if (action === 'mode') {
      fireEvent.click(screen.getByRole('button', { name: '离线样例' }))
      fireEvent.click(screen.getByRole('button', { name: '连接后端' }))
      await screen.findByRole('button', { name: /广州今日广场/ })
    }
    if (action === 'store') {
      fireEvent.click(screen.getByRole('button', { name: /广州今日广场/ }))
      fireEvent.click(await screen.findByRole('button', { name: /广州维多利广场/ }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    }
    await say('总预算100元')
    expect(parse.mock.calls[1][2]).not.toHaveProperty('pending_fields')
    expect(parse.mock.calls[1][2]).toMatchObject({ people: 6, budget_fen: action === 'form' ? 18000 : 15000 })
  })

  it('does not reuse a drink-containing offline fixture for an extended no-beverage request', async () => {
    const result = await api.getPlans('demo', '1400011', { ...api.DEMO_REQUIREMENTS, exclude_beverage: true } as Requirements)
    expect(result.plans).toEqual([])
    expect(result.unsatisfiable_reason).toContain('无可用离线样例')
    expect(result.requirements).toHaveProperty('exclude_beverage', true)
  })

  it('shows the formal Skill separately from the snapshot data source', async () => {
    const result = await api.getPlans('demo', '1400011', api.DEMO_REQUIREMENTS)
    vi.spyOn(api, 'getPlans').mockResolvedValue({ ...result, algorithm_source: 'skill', suggestions: [] } as typeof result)
    await openApi()
    fireEvent.click(screen.getByRole('button', { name: '生成省钱方案' }))
    const label = await screen.findByText(/正式方案算法/)
    expect(label).toHaveTextContent('历史快照')
    expect(label).not.toHaveTextContent('在线菜单')
    expect(label).not.toHaveTextContent('演示算法')
  })

  it('retains the demo algorithm label for an explicit demo_sample response', async () => {
    const result = await api.getPlans('demo', '1400011', api.DEMO_REQUIREMENTS)
    vi.spyOn(api, 'getPlans').mockResolvedValue({ ...result, algorithm_source: 'demo_sample', suggestions: [] } as typeof result)
    await openApi()
    fireEvent.click(screen.getByRole('button', { name: '生成省钱方案' }))
    expect(await screen.findByText(/演示算法 demo_sample/)).toHaveTextContent('历史快照')
  })

  it('describes disabled controls as a frontend integration gap rather than an unpublished backend capability', async () => {
    await openApi()
    const beverage = screen.getByRole('button', { name: /不带饮料/ })
    const preference = screen.getByRole('button', { name: /饭量 \/ 口味/ })
    expect(beverage).toBeDisabled()
    expect(preference).toBeDisabled()
    expect(beverage).toHaveAttribute('title', expect.stringContaining('当前前端界面尚未接入'))
    expect(preference).toHaveAttribute('title', expect.stringContaining('当前前端界面尚未接入'))
  })
})
