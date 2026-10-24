import { describe, expect, it } from 'vitest'
import { buttonMotion, cardMotion, dialogMotion, gameSpring } from '../src/lib/motion-presets'

describe('tactile kitchen animation presets', () => {
  it('uses a clearly underdamped spring instead of the previous restrained bounce', () => {
    expect(gameSpring.type).toBe('spring')
    expect(gameSpring.stiffness).toBeGreaterThanOrEqual(320)
    expect(gameSpring.damping).toBeLessThan(20)
  })
  it('lifts the key, compresses it on press and springs back', () => {
    const preset = buttonMotion(false)
    expect(preset.whileHover?.y).toBeLessThan(0)
    expect(preset.whileTap?.y).toBeGreaterThanOrEqual(4)
    expect(preset.whileTap?.scale).toBeLessThanOrEqual(.95)
  })
  it('staggers physically raised cards without changing their settled position', () => {
    const first = cardMotion(0, false), next = cardMotion(1, false)
    expect(next.transition.delay).toBeGreaterThan(first.transition.delay)
    expect(first.initial.scale).toBeLessThan(.95)
    expect(first.whileHover?.y).toBeLessThanOrEqual(-8)
    expect(first.animate).toMatchObject({ y: 0, scale: 1, rotate: 0 })
  })
  it('pops the task board in at a visible scale difference', () => {
    const preset = dialogMotion(false)
    expect(preset.initial.scale).toBeLessThanOrEqual(.8)
    expect(preset.initial.y).toBeGreaterThanOrEqual(40)
    expect(preset.animate).toMatchObject({ scale: 1, y: 0, rotate: 0 })
  })
  it('removes spatial gestures and staggering for reduced motion', () => {
    expect(buttonMotion(true).whileHover).toBeUndefined()
    expect(buttonMotion(true).whileTap).toBeUndefined()
    const card = cardMotion(2, true), dialog = dialogMotion(true)
    expect(card.initial).toMatchObject({ y: 0, scale: 1, rotate: 0 })
    expect(card.whileHover).toBeUndefined()
    expect(card.transition.delay).toBe(0)
    expect(dialog.initial).toMatchObject({ y: 0, scale: 1, rotate: 0 })
  })
  it('never lifts or depresses a disabled action', () => {
    // Keep a neutral hover target so pointerleave can clear an already-active Motion gesture.
    expect(buttonMotion(false, true).whileHover).toEqual({ y: 0, scale: 1 })
    expect(buttonMotion(false, true).whileTap).toBeUndefined()
  })
})
