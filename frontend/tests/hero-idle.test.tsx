import { installOnlineApi } from './helpers/online-api'
installOnlineApi()
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'

const preference = vi.hoisted(() => ({ reduced: false }))
vi.mock('motion/react', async (original) => ({
  ...await original<typeof import('motion/react')>(),
  useReducedMotion: () => preference.reduced,
}))

let onIntersection: IntersectionObserverCallback | undefined
let motionQuery: MediaQueryList
class SceneObserver {
  constructor(callback: IntersectionObserverCallback) { onIntersection = callback }
  observe() {}
  unobserve() {}
  disconnect() {}
}

async function showScene() {
  vi.stubGlobal('IntersectionObserver', SceneObserver)
  motionQuery = Object.assign(new EventTarget(), {
    matches: preference.reduced,
    media: '(prefers-reduced-motion: reduce)',
    onchange: null,
    addListener() {},
    removeListener() {},
  }) as MediaQueryList
  vi.spyOn(window, 'matchMedia').mockReturnValue(motionQuery)
  render(<App />)
  await screen.findByRole('button', { name: /广州今日广场/ })
  act(() => onIntersection?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
  return document.querySelector('.hero-art')!
}

afterEach(() => { preference.reduced = false; onIntersection = undefined; vi.unstubAllGlobals() })

describe('quiet kitchen idle ambience', () => {
  it('starts only when visible and keeps every operating control outside the animated visual', async () => {
    const scene = await showScene()
    expect(scene).toHaveAttribute('data-idle', 'running')
    const visual = scene.querySelector('.hero-visual')!
    expect(visual).toBeInTheDocument()
    expect(visual.querySelector('button, input, textarea, a')).toBeNull()
    expect(visual.querySelector('.kitchen-steam')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByRole('button', { name: '开始配餐' }).closest('.hero-visual')).toBeNull()
  })

  it('lets the user pause and resume without affecting the ordering button', async () => {
    const scene = await showScene()
    fireEvent.click(screen.getByRole('button', { name: '暂停动效' }))
    expect(scene).toHaveAttribute('data-idle', 'paused')
    expect(screen.getByRole('button', { name: '开启动效' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '开始配餐' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '开启动效' }))
    expect(scene).toHaveAttribute('data-idle', 'running')
  })

  it('pauses when offscreen and resumes only when visible again', async () => {
    const scene = await showScene()
    act(() => onIntersection?.([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(scene).toHaveAttribute('data-idle', 'paused')
    act(() => onIntersection?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(scene).toHaveAttribute('data-idle', 'running')
  })

  it('pauses in a hidden document', async () => {
    const scene = await showScene()
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    fireEvent(document, new Event('visibilitychange'))
    expect(scene).toHaveAttribute('data-idle', 'paused')
    visibility.mockReturnValue('visible')
    fireEvent(document, new Event('visibilitychange'))
    expect(scene).toHaveAttribute('data-idle', 'running')
  })

  it('keeps the scene static for reduced motion', async () => {
    preference.reduced = true
    const scene = await showScene()
    expect(scene).toHaveAttribute('data-idle', 'paused')
    expect(scene).toHaveAttribute('data-reduced-motion', 'true')
    expect(screen.queryByRole('button', { name: '暂停动效' })).not.toBeInTheDocument()
  })

  it('resumes when the system turns reduced motion off without remounting', async () => {
    preference.reduced = true
    const scene = await showScene()
    expect(scene).toHaveAttribute('data-idle', 'paused')
    act(() => {
      Object.defineProperty(motionQuery, 'matches', { value: false, configurable: true })
      motionQuery.dispatchEvent(new Event('change'))
    })
    expect(scene).toHaveAttribute('data-reduced-motion', 'false')
    expect(scene).toHaveAttribute('data-idle', 'running')
    expect(screen.getByRole('button', { name: '暂停动效' })).toBeInTheDocument()
  })

  it('stops when the system turns reduced motion on without remounting', async () => {
    const scene = await showScene()
    expect(scene).toHaveAttribute('data-idle', 'running')
    act(() => {
      Object.defineProperty(motionQuery, 'matches', { value: true, configurable: true })
      motionQuery.dispatchEvent(new Event('change'))
    })
    expect(scene).toHaveAttribute('data-reduced-motion', 'true')
    expect(scene).toHaveAttribute('data-idle', 'paused')
    expect(screen.queryByRole('button', { name: '暂停动效' })).not.toBeInTheDocument()
  })

  it('pauses while a dialog is open without changing the user pause setting', async () => {
    const scene = await showScene()
    fireEvent.click(screen.getByRole('button', { name: /广州今日广场/ }))
    await screen.findByRole('dialog')
    expect(scene).toHaveAttribute('data-idle', 'paused')
    fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(scene).toHaveAttribute('data-idle', 'running')
  })
})
