import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import * as api from '../src/lib/api'
import type { PlanResponse } from '../src/lib/types'
import sample from '../../docs/api-examples/plans_6人150元2人不吃辣.json'

const motionPreference = vi.hoisted(() => ({ reduced: false }))
vi.mock('motion/react', async (original) => ({
  ...await original<typeof import('motion/react')>(),
  useReducedMotion: () => motionPreference.reduced,
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function response() { return structuredClone(sample.data) as PlanResponse }
async function start() {
  render(<App />)
  await screen.findByRole('button', { name: /广州今日广场/ })
  fireEvent.click(screen.getByRole('button', { name: '生成省钱方案' }))
}
afterEach(() => { motionPreference.reduced = false })

describe('non-blocking kitchen feedback', () => {
  it('shows an honest preparation state without fake progress or blocking the brief', async () => {
    vi.spyOn(api, 'getPlans').mockReturnValueOnce(deferred<PlanResponse>().promise)
    await start()
    expect(await screen.findByRole('heading', { name: '小厨房正在备餐' })).toBeInTheDocument()
    const results = screen.getByRole('region', { name: '配餐结果' })
    expect(results).toHaveAttribute('aria-busy', 'true')
    expect(within(results).getByRole('status')).toHaveAttribute('aria-live', 'polite')
    expect(within(results).getByText(/正在读取已保存的方案样例/)).toBeInTheDocument()
    expect(within(results).queryByRole('progressbar')).toBeNull()
    expect(screen.getByRole('button', { name: '生成省钱方案' })).toBeDisabled()
    expect(screen.getByLabelText('预算金额')).toBeEnabled()
    expect(screen.getByRole('button', { name: '增加用餐人数' })).toBeEnabled()
  })

  it('keeps a failed brief and retries exactly once from the result board', async () => {
    const getPlans = vi.spyOn(api, 'getPlans')
      .mockRejectedValueOnce(new Error('网络暂时断开，请稍后重试。'))
      .mockResolvedValueOnce(response())
    await start()
    const results = screen.getByRole('region', { name: '配餐结果' })
    const alert = await within(results).findByRole('alert')
    expect(alert).toHaveTextContent('网络暂时断开，请稍后重试。')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByLabelText('用餐人数')).toHaveValue(6)
    expect(screen.getByLabelText('预算金额')).toHaveValue(150)
    fireEvent.click(within(alert).getByRole('button', { name: '重新生成省钱方案' }))
    expect(await within(results).findByText('3 份方案已出餐')).toBeInTheDocument()
    expect(within(results).getAllByRole('article')).toHaveLength(3)
    expect(within(results).queryByRole('alert')).toBeNull()
    expect(getPlans).toHaveBeenCalledTimes(2)
    expect(getPlans.mock.calls[1]).toEqual(getPlans.mock.calls[0])
  })

  it('clears preparation on editing and never displays a late success', async () => {
    const pending = deferred<PlanResponse>()
    vi.spyOn(api, 'getPlans').mockReturnValueOnce(pending.promise)
    await start()
    await screen.findByRole('heading', { name: '小厨房正在备餐' })
    fireEvent.click(screen.getByRole('button', { name: '增加用餐人数' }))
    await act(async () => pending.resolve(response()))
    await waitFor(() => expect(screen.queryByText('3 份方案已出餐')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /就选这份/ })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: '配餐结果' })).toHaveAttribute('aria-busy', 'false')
    expect(screen.getByLabelText('用餐人数')).toHaveValue(7)
  })

  it('does not resurrect a stale error when the brief has already changed', async () => {
    const pending = deferred<PlanResponse>()
    vi.spyOn(api, 'getPlans').mockReturnValueOnce(pending.promise)
    await start()
    fireEvent.change(screen.getByLabelText('预算金额'), { target: { value: '180' } })
    await act(async () => pending.reject(new Error('旧请求失败')))
    await waitFor(() => expect(screen.getByRole('region', { name: '配餐结果' })).toHaveAttribute('aria-busy', 'false'))
    expect(screen.queryByText('旧请求失败')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新生成省钱方案' })).not.toBeInTheDocument()
  })

  it('does not announce no-solution as completed plans', async () => {
    const result = response()
    result.plans = []
    result.unsatisfiable_reason = '预算暂时不足'
    vi.spyOn(api, 'getPlans').mockResolvedValueOnce(result)
    await start()
    expect(await screen.findByRole('heading', { name: '这单，还差一点点' })).toBeInTheDocument()
    expect(screen.queryByText(/份方案已出餐/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /就选这份/ })).not.toBeInTheDocument()
  })

  it('keeps the new loading decoration static for reduced motion', async () => {
    motionPreference.reduced = true
    vi.spyOn(api, 'getPlans').mockReturnValueOnce(deferred<PlanResponse>().promise)
    await start()
    await screen.findByRole('heading', { name: '小厨房正在备餐' })
    const feedback = document.querySelector('[data-feedback="loading"]')!
    expect(feedback).toHaveAttribute('data-motion', 'still')
    expect(feedback.querySelector('.feedback-decoration')).toHaveAttribute('aria-hidden', 'true')
    expect(feedback.querySelector('.feedback-decoration button')).toBeNull()
  })

  it('does not mislabel a chat parsing error as a kitchen generation failure', async () => {
    vi.spyOn(api, 'parseChat').mockRejectedValueOnce(new Error('这句需求暂未读懂'))
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.change(screen.getByLabelText(/和小厨神说一声/), { target: { value: '试一下' } })
    fireEvent.click(screen.getByRole('button', { name: '发送点餐需求' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('这句需求暂未读懂')
    expect(within(screen.getByRole('region', { name: '配餐结果' })).queryByRole('alert')).toBeNull()
  })

  it('uses the returned requirement snapshot for comparison rather than food servings', async () => {
    const result = response()
    result.requirements.people = 3
    result.requirements.dietary_constraints = [{ kind: 'non_spicy', people: 1 }]
    vi.spyOn(api, 'getPlans').mockResolvedValueOnce(result)
    await start()
    const first = await screen.findByRole('article', { name: '最省方案' })
    expect(within(first).getByText('按 3 人均摊')).toBeInTheDocument()
    expect(within(first).getByText(`¥${api.formatMoney(result.plans[0].total_fen / 3)}`)).toBeInTheDocument()
    expect(within(first).getByText(`${result.plans[0].non_spicy_servings} / 需 1 份`)).toBeInTheDocument()
  })

  it('does not steal input focus when completed plans arrive', async () => {
    const pending = deferred<PlanResponse>()
    vi.spyOn(api, 'getPlans').mockReturnValueOnce(pending.promise)
    await start()
    const budget = screen.getByLabelText('预算金额')
    budget.focus()
    await act(async () => pending.resolve(response()))
    await screen.findByText('3 份方案已出餐')
    expect(budget).toHaveFocus()
  })
})
