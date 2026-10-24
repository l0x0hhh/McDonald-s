import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useLiveReducedMotion } from '../src/lib/use-live-reduced-motion'

function mediaQuery(matches: boolean) {
  return Object.assign(new EventTarget(), { matches, media: '(prefers-reduced-motion: reduce)' }) as MediaQueryList
}

describe('live reduced-motion preference', () => {
  it('reads the native preference at mount and follows changes in both directions', () => {
    const preference = mediaQuery(true)
    vi.spyOn(window, 'matchMedia').mockReturnValue(preference)
    const { result } = renderHook(useLiveReducedMotion)
    expect(result.current).toBe(true)
    act(() => {
      Object.defineProperty(preference, 'matches', { value: false, configurable: true })
      preference.dispatchEvent(new Event('change'))
    })
    expect(result.current).toBe(false)
    act(() => {
      Object.defineProperty(preference, 'matches', { value: true, configurable: true })
      preference.dispatchEvent(new Event('change'))
    })
    expect(result.current).toBe(true)
  })

  it('closes a preference change between the render snapshot and effect subscription', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValueOnce(mediaQuery(false)).mockReturnValue(mediaQuery(true))
    const { result } = renderHook(useLiveReducedMotion)
    expect(result.current).toBe(true)
  })

  it('removes its exact native change listener on unmount', () => {
    const preference = mediaQuery(false)
    const add = vi.spyOn(preference, 'addEventListener')
    const remove = vi.spyOn(preference, 'removeEventListener')
    vi.spyOn(window, 'matchMedia').mockReturnValue(preference)
    const { unmount } = renderHook(useLiveReducedMotion)
    expect(add).toHaveBeenCalledExactlyOnceWith('change', expect.any(Function))
    const callback = add.mock.calls[0][1]
    unmount()
    expect(remove).toHaveBeenCalledExactlyOnceWith('change', callback)
  })

  it('also subscribes and cleans up browsers with legacy media listeners', () => {
    const preference = { matches: false, addListener: vi.fn(), removeListener: vi.fn() }
    vi.spyOn(window, 'matchMedia').mockReturnValue(preference as unknown as MediaQueryList)
    const { result, unmount } = renderHook(useLiveReducedMotion)
    expect(result.current).toBe(false)
    const update = preference.addListener.mock.calls[0][0] as () => void
    act(() => { preference.matches = true; update() })
    expect(result.current).toBe(true)
    unmount()
    expect(preference.removeListener).toHaveBeenCalledExactlyOnceWith(update)
  })
})
