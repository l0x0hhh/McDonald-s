import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HeroScene from '../src/components/HeroScene'

let intersect: IntersectionObserverCallback | undefined
class Observer {
  constructor(callback: IntersectionObserverCallback) { intersect = callback }
  observe() {}
  disconnect() {}
}

function setup() {
  vi.stubGlobal('IntersectionObserver', Observer)
  const result = render(<HeroScene paused={false} />)
  act(() => intersect?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
  const scene = result.container.querySelector('.hero-art')!
  const frames = () => Array.from(scene.querySelectorAll<HTMLImageElement>('.chef-blink'))
  const loadFrames = () => frames().forEach((frame) => fireEvent.load(frame))
  return { ...result, scene, frames, loadFrames }
}

afterEach(() => { intersect = undefined; vi.unstubAllGlobals() })

describe('actual chef eye frames', () => {
  it('adds two independent decorative eye frames without replacing the accessible original', () => {
    const { frames, scene } = setup()
    expect(frames()).toHaveLength(2)
    expect(frames().map((frame) => frame.dataset.chef)).toEqual(['boy', 'girl'])
    frames().forEach((frame) => {
      expect(frame).toHaveAttribute('src', '/images/kitchen-chefs-blink.png')
      expect(frame).toHaveAttribute('aria-hidden', 'true')
      expect(frame).toHaveAttribute('alt', '')
    })
    expect(screen.getAllByRole('img')).toHaveLength(1)
    expect(screen.getByRole('img')).toHaveAttribute('src', '/images/kitchen-hero-duo.png')
    expect(scene.querySelector('.hero-canvas')).toContainElement(screen.getByRole('img'))
  })

  it('waits for both eye frames before enabling replay', () => {
    const { scene, frames } = setup()
    const replay = screen.getByRole('button', { name: '重播人物动效' })
    expect(replay).toBeDisabled()
    fireEvent.load(frames()[0])
    expect(replay).toBeDisabled()
    fireEvent.load(frames()[1])
    expect(replay).toBeEnabled()
    expect(scene).toHaveAttribute('data-character-animation', 'blink')
    expect(scene).toHaveAttribute('data-blink-ready', 'true')
  })

  it('restarts just the eye frames and resumes a user-paused scene', () => {
    const { scene, frames, loadFrames } = setup()
    loadFrames()
    const original = screen.getByRole('img')
    const oldFrame = frames()[0]
    fireEvent.click(screen.getByRole('button', { name: '暂停动效' }))
    expect(scene).toHaveAttribute('data-idle', 'paused')
    fireEvent.click(screen.getByRole('button', { name: '重播人物动效' }))
    expect(scene).toHaveAttribute('data-idle', 'running')
    expect(scene).toHaveAttribute('data-replay', '1')
    expect(frames()[0]).not.toBe(oldFrame)
    expect(screen.getByRole('img')).toBe(original)
    expect(scene.querySelector('.hero-visual button')).toBeNull()
  })

  it('falls back to the intact static illustration on frame failure and ignores late load events', () => {
    const { frames, scene } = setup()
    fireEvent.error(frames()[0])
    frames().forEach((frame) => fireEvent.load(frame))
    expect(scene).toHaveAttribute('data-character-animation', 'static')
    expect(scene).toHaveAttribute('data-blink-ready', 'false')
    expect(screen.getByRole('button', { name: '重播人物动效' })).toBeDisabled()
    expect(screen.getByRole('img')).toHaveAttribute('src', '/images/kitchen-hero-duo.png')
  })

  it('keeps replay and pointer depth outside the operating controls and pauses off-screen', () => {
    const { scene, loadFrames } = setup()
    loadFrames()
    const replay = screen.getByRole('button', { name: '重播人物动效' })
    expect(scene.querySelector('.hero-stage')).not.toContainElement(replay)
    expect(scene.querySelector('.hero-lamp-wash')).toHaveAttribute('aria-hidden', 'true')
    act(() => intersect?.([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(replay).toBeDisabled()
    expect(scene).toHaveAttribute('data-idle', 'paused')
  })

  it('keeps the opening entered after leaving and returning, without replaying the stage entrance', () => {
    const { scene } = setup()
    expect(scene).toHaveAttribute('data-stage-entered', 'true')
    act(() => intersect?.([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver))
    act(() => intersect?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(scene).toHaveAttribute('data-stage-entered', 'true')
  })
})
