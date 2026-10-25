import { installOnlineApi } from './helpers/online-api'
installOnlineApi()
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
// @ts-expect-error The test host provides Node; the browser app intentionally has no Node ambient types.
import { readFileSync } from 'node:fs'
import App from '../src/App'
import { planSharedTransition, planTitles, planTransitionId, sharedPlanElement } from '../src/lib/plan-transition'
const transitionStyles = readFileSync('src/components/plan-transition.css', 'utf8')

afterEach(() => document.querySelectorAll('style[data-transition-test]').forEach((style) => style.remove()))

describe('shared plan transition contract', () => {
  it('keeps plans, parts and repeated food rows in separate namespaces', () => {
    const ids = [
      planTransitionId('plan:one', 'title'),
      planTransitionId('plan%3Aone', 'title'),
      planTransitionId('plan:one', 'total'),
      planTransitionId('plan:one', 'food-0'),
      planTransitionId('plan:one', 'food-1'),
    ]
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('moves text and images by position without stretching their aspect ratio', () => {
    const id = planTransitionId('plan', 'title')
    expect(sharedPlanElement(id, false)).toMatchObject({ layoutId: id, layout: 'position', 'data-plan-shared-id': id })
    expect(sharedPlanElement(planTransitionId('plan', 'food-0'), false).layout).toBe('position')
  })

  it('disables shared layout movement completely for reduced motion or an unpaired image', () => {
    expect(sharedPlanElement('id', true)).toEqual({ layoutId: undefined, layout: false, 'data-plan-shared-id': undefined })
    expect(sharedPlanElement(undefined, false).layout).toBe(false)
  })

  it('uses a short finite handoff with the same honest titles as the serving trays', () => {
    expect(planSharedTransition.duration).toBeGreaterThan(0)
    expect(planSharedTransition.duration).toBeLessThanOrEqual(.36)
    expect(planSharedTransition).not.toHaveProperty('delay')
    expect(planSharedTransition).not.toHaveProperty('repeat')
    expect(planTitles).toEqual({ recommended: '推荐搭配', alternative: '换个搭配', cheapest: '最省方案', balanced: '搭配多样', taste: '口味匹配' })
  })
})

describe('serving tray to checkout continuity', () => {
  it('never shares a layout transform on an operating button or any of its ancestors', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(await screen.findByRole('button', { name: '看看怎么点' }))
    const choose = await screen.findByRole('button', { name: '就选这份搭配多样' }, {timeout: 5000})
    expect(choose.closest('[data-plan-shared-id]')).toBeNull()
    choose.focus()
    fireEvent.click(choose)
    const dialog = await screen.findByRole('dialog', { name: '选择取餐方式' })
    expect(within(dialog).getByRole('button', { name: '前往支付' }).closest('[data-plan-shared-id]')).toBeNull()
    expect(within(dialog).getByRole('button', { name: '关闭弹窗' }).closest('[data-plan-shared-id]')).toBeNull()
    expect(dialog).not.toHaveAttribute('data-plan-shared-id')
    expect(choose.closest('article')).not.toHaveAttribute('data-plan-shared-id')
    expect(dialog.querySelectorAll('[data-plan-shared-id]').length).toBeGreaterThan(0)
  })

  it('removes every source and receipt shared ID when reduced motion changes after opening', async () => {
    // Vitest deliberately disables CSS imports; use the real production sheet
    // for this computed-style assertion, not a duplicated test-only rule.
    const style = document.createElement('style')
    expect(transitionStyles).toContain('data-reduced-motion')
    style.dataset.transitionTest = 'true'
    style.textContent = transitionStyles
    document.head.append(style)
    const query = Object.assign(new EventTarget(), { matches: false, media: '(prefers-reduced-motion: reduce)' }) as MediaQueryList
    vi.spyOn(window, 'matchMedia').mockReturnValue(query)
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(await screen.findByRole('button', { name: '看看怎么点' }))
    fireEvent.click(await screen.findByRole('button', { name: '就选这份搭配多样' }, {timeout: 5000}))
    const dialog = await screen.findByRole('dialog', { name: '选择取餐方式' })
    expect(dialog.querySelectorAll('[data-plan-shared-id]').length).toBeGreaterThan(0)
    expect(document.querySelector('.app-shell [data-plan-shared-id]')).toBeInTheDocument()

    act(() => {
      Object.defineProperty(query, 'matches', { value: true, configurable: true })
      query.dispatchEvent(new Event('change'))
    })

    await waitFor(() => expect(document.querySelector('[data-plan-shared-id]')).not.toBeInTheDocument())
    expect(document.querySelector('.app-shell')).toHaveAttribute('data-reduced-motion', 'true')
    expect(dialog.closest('.modal-backdrop')).toHaveAttribute('data-reduced-motion', 'true')
    expect(getComputedStyle(dialog).transform).toBe('none')
    expect(getComputedStyle(document.querySelector('.plan-card')!).transform).toBe('none')
    expect(getComputedStyle(document.querySelector('.plan-card')!).opacity).toBe('1')
    fireEvent.change(within(dialog).getByLabelText('取餐方式'),{target:{value:'eat-in'}})
    expect(within(dialog).getByRole('button', { name: '前往支付' })).toBeEnabled()

    act(() => {
      Object.defineProperty(query, 'matches', { value: false, configurable: true })
      query.dispatchEvent(new Event('change'))
    })
    await waitFor(() => expect(document.querySelector('.app-shell [data-plan-shared-id]')).toBeInTheDocument())
    expect(dialog.closest('.modal-backdrop')).toHaveAttribute('data-reduced-motion', 'false')
    fireEvent.change(within(dialog).getByLabelText('取餐方式'),{target:{value:'eat-in'}})
    expect(within(dialog).getByRole('button', { name: '前往支付' })).toBeEnabled()
  })

  it('connects only the selected plan title, amount and food thumbnails to the receipt', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(await screen.findByRole('button', { name: '看看怎么点' }))
    const choose = await screen.findByRole('button', { name: '就选这份搭配多样' }, {timeout: 5000})
    const card = choose.closest('article')!
    const sourceTitle = within(card).getByText('搭配多样')
    const sourceAmount = card.querySelector('.metric-total .price')!
    const sourceImages = Array.from(card.querySelectorAll('.food-image'))
    expect(sourceTitle).toHaveAttribute('data-plan-shared-id')
    expect(sourceAmount).toHaveAttribute('data-plan-shared-id')

    choose.focus()
    fireEvent.click(choose)
    const dialog = await screen.findByRole('dialog', { name: '选择取餐方式' })
    const receiptTitle = within(dialog).getByRole('heading', { name: '搭配多样' })
    expect(receiptTitle).toHaveAttribute('data-plan-shared-id', sourceTitle.getAttribute('data-plan-shared-id'))
    expect(dialog.querySelector('.order-total strong')).toHaveAttribute('data-plan-shared-id', sourceAmount.getAttribute('data-plan-shared-id'))
    const receiptImages = Array.from(dialog.querySelectorAll('.food-image'))
    expect(receiptImages).toHaveLength(sourceImages.length)
    receiptImages.forEach((image, index) => {
      expect(image).toHaveAttribute('data-plan-shared-id', sourceImages[index].getAttribute('data-plan-shared-id'))
    })
    fireEvent.change(within(dialog).getByLabelText('取餐方式'),{target:{value:'eat-in'}})
    expect(within(dialog).getByRole('button', { name: '前往支付' })).toBeEnabled()
  })

  it('returns keyboard focus to the same selection without changing its plan or price', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(await screen.findByRole('button', { name: '看看怎么点' }))
    const choose = await screen.findByRole('button', { name: '就选这份口味匹配' }, {timeout: 5000})
    const card = choose.closest('article')!
    const originalAmount = card.querySelector('.metric-total .price')!.textContent
    choose.focus()
    fireEvent.click(choose)
    const dialog = await screen.findByRole('dialog', { name: '选择取餐方式' })
    expect(within(dialog).getByRole('heading', { name: '口味匹配' })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(choose).toHaveFocus()
    expect(card.querySelector('.metric-total .price')).toHaveTextContent(originalAmount!)
    expect(choose).toBeEnabled()
  })

  it('can immediately reopen the same tray after the exit completes', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(await screen.findByRole('button', { name: '看看怎么点' }))
    const choose = await screen.findByRole('button', { name: '就选这份最省方案' }, {timeout: 5000})
    choose.focus()
    fireEvent.click(choose)
    await screen.findByRole('dialog', { name: '选择取餐方式' })
    fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    fireEvent.click(choose)
    const reopened = await screen.findByRole('dialog', { name: '选择取餐方式' })
    fireEvent.change(within(reopened).getByLabelText('取餐方式'),{target:{value:'eat-in'}})
    expect(within(reopened).getByRole('button', { name: '前往支付' })).toBeEnabled()
    expect(document.querySelector<HTMLElement>('.app-shell')?.inert).toBe(true)
  })
})
