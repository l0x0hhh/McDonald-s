import { installOnlineApi } from './helpers/online-api'
installOnlineApi()
import { StrictMode, useRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import OpeningCopy from '../src/components/OpeningCopy'

const playback = vi.hoisted(() => ({ animate: vi.fn(), stop: vi.fn() }))
vi.mock('motion/react', async (original) => ({
  ...await original<typeof import('motion/react')>(),
  useAnimate: () => [useRef(null), playback.animate],
}))

beforeEach(() => {
  playback.animate.mockReset().mockReturnValue({ stop: playback.stop })
  playback.stop.mockReset()
})

function copy(onClick = vi.fn()) {
  return <>
    <div className="eyebrow">开店啦</div>
    <h1>今天，开饭！</h1>
    <p className="hero-description">把预算花在喜欢的东西上。</p>
    <div className="hero-actions"><button onClick={onClick}>立刻开始</button></div>
    <div className="hero-promises">预算有数</div>
  </>
}

describe('opening-day copy choreography', () => {
  it('uses direct ordering copy without food, budget or store-status guarantees', () => {
    render(<App />)
    expect(screen.getByText('按人数和预算，搭配这一餐')).toBeInTheDocument()
    expect(document.querySelector('.hero-description')).toHaveTextContent('选一家店，填好人数、预算和忌口。看看有哪些搭配，再到官方页面付款。')
    expect(document.querySelector('.hero-promises')).toHaveTextContent('按预算选餐列清商品份数说明忌口')
    expect(document.querySelector('.open-sign')).toHaveTextContent('开饭准备中')
    expect(document.querySelector('.open-sign')).not.toHaveTextContent('营业中')
    expect(document.querySelector('.art-caption')).toHaveTextContent('选好搭配，再去付款')
    expect(screen.getByText('选好门店，再填人数和预算')).toBeInTheDocument()
    expect(screen.getByLabelText(/这顿想怎么吃/)).toBeInTheDocument()
    expect(screen.getByRole('heading',{name:'先填人数和预算'})).toBeInTheDocument()
    expect(screen.getByText('选好门店，看看有哪些搭配。')).toBeInTheDocument()
    expect(document.querySelector('.empty-options')).toHaveTextContent('人数预算忌口')
    expect(screen.getByText('配餐方案与官方报价')).toBeInTheDocument()
    expect(screen.queryByText(/大家吃得够|预算不超标|把好吃留给你|精打细算派/)).not.toBeInTheDocument()
    expect(screen.getByRole('heading',{name:/今天.*开饭/})).toBeInTheDocument()
    expect(screen.getByRole('heading',{name:'点餐小纸条'})).toBeInTheDocument()
    expect(screen.getByRole('heading',{name:'小厨房出餐台'})).toBeInTheDocument()
  })

  it('names ordering tasks and nutrition features without marketing or implementation narration', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: '这顿怎么点' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '我的订单' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '使用说明' })).toHaveLength(2)
    expect(screen.getByText('填写点餐需求')).toBeInTheDocument()
    expect(screen.getByText('选择配餐方案')).toBeInTheDocument()
    expect(screen.getByText('确认订单并付款')).toBeInTheDocument()
    expect(screen.getByText('价格明细')).toBeInTheDocument()
    expect(screen.getByText('查看总价、人均和预算剩余')).toBeInTheDocument()
    expect(screen.getByText('营养信息')).toBeInTheDocument()
    expect(screen.getByText('查看热量与营养成分')).toBeInTheDocument()
    expect(screen.queryByText('有数据就展示，未知的不会当作零。')).not.toBeInTheDocument()
    expect(screen.queryByText('每一分钱，都有来处')).not.toBeInTheDocument()
  })

  it('opens help using the same plain-language label as the navigation', () => {
    render(<App />)
    fireEvent.click(screen.getAllByRole('button', { name: '使用说明' })[0])
    expect(screen.getByRole('dialog', { name: '怎么点餐' })).toBeInTheDocument()
  })

  it('keeps the ordering action live inside an independently choreographed copy rail', () => {
    const scroll = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {})
    render(<App />)
    const action = screen.getByRole('button', { name: '开始配餐' })
    expect(action.closest('.hero-copy')).toHaveClass('opening-copy')
    expect(action).toBeEnabled()
    fireEvent.click(action)
    expect(scroll).toHaveBeenCalledOnce()
  })

  it('never fades, moves, wraps or delays the action rail', () => {
    const onClick = vi.fn()
    const { container } = render(<OpeningCopy reducedMotion={false}>{copy(onClick)}</OpeningCopy>)
    const wrapper = container.querySelector('.opening-copy')!
    const action = screen.getByRole('button', { name: '立刻开始' })
    expect(wrapper.children).toHaveLength(5)
    expect(action.parentElement?.parentElement).toBe(wrapper)
    expect(action.parentElement).not.toHaveAttribute('style')
    expect(wrapper).not.toHaveAttribute('style')
    fireEvent.click(action)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('orchestrates only copy cues and finishes its single opening inside 800ms', () => {
    render(<OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy>)
    expect(playback.animate).toHaveBeenCalledOnce()
    const sequence = playback.animate.mock.calls[0][0] as [string, unknown, { at: number; duration: number }][]
    expect(sequence.map(([selector]) => selector)).toEqual([
      ':scope > .eyebrow', ':scope > h1', ':scope > .hero-description', ':scope > .hero-promises',
    ])
    const duration = Math.max(...sequence.map(([, , transition]) => transition.at + transition.duration))
    expect(duration).toBeGreaterThanOrEqual(.6)
    expect(duration).toBeLessThanOrEqual(.8)
    expect(sequence.some(([selector]) => selector.includes('hero-actions'))).toBe(false)
  })

  it('does not replay the opening when the application updates', () => {
    const { rerender } = render(<OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy>)
    rerender(<OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy>)
    expect(playback.animate).toHaveBeenCalledOnce()
  })

  it('starts static for reduced motion and never replays on preference recovery', () => {
    const { container, rerender } = render(<OpeningCopy reducedMotion>{copy()}</OpeningCopy>)
    expect(playback.animate).not.toHaveBeenCalled()
    expect(container.querySelector('h1')).toHaveStyle({ opacity: '1', transform: 'none' })
    rerender(<OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy>)
    expect(playback.animate).not.toHaveBeenCalled()
  })

  it('cancels and settles all copy immediately if reduced motion is enabled during opening', () => {
    const { container, rerender } = render(<OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy>)
    const title = container.querySelector('h1')!
    title.style.opacity = '.65'
    title.style.transform = 'translateY(6px)'
    rerender(<OpeningCopy reducedMotion>{copy()}</OpeningCopy>)
    expect(playback.stop).toHaveBeenCalledOnce()
    expect(title).toHaveStyle({ opacity: '1', transform: 'none' })
    expect(container.querySelector('.hero-actions')).not.toHaveAttribute('style')
    rerender(<OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy>)
    expect(playback.animate).toHaveBeenCalledOnce()
  })

  it('stops scoped animation when the copy is unmounted', () => {
    const { unmount } = render(<OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy>)
    unmount()
    expect(playback.stop).toHaveBeenCalledOnce()
  })

  it('restarts the same visible opening after StrictMode mount preflight', () => {
    render(<StrictMode><OpeningCopy reducedMotion={false}>{copy()}</OpeningCopy></StrictMode>)
    expect(playback.animate).toHaveBeenCalledTimes(2)
    expect(playback.stop).toHaveBeenCalledOnce()
  })
})
