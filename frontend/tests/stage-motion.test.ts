import { describe, expect, it } from 'vitest'
import { kitchenDepth } from '../src/lib/stage-motion'

describe('bounded kitchen depth', () => {
  const rect = { left: 20, top: 30, width: 600, height: 400 }
  it('rests at the pointer center', () => {
    expect(kitchenDepth(320, 230, rect)).toEqual({ x: 0, y: 0, rotateX: 0, rotateY: 0 })
  })
  it('caps motion even when the pointer leaves the illustration', () => {
    expect(kitchenDepth(9999, -9999, rect)).toEqual({ x: 4, y: -3, rotateX: .45, rotateY: .65 })
  })
  it('returns a still frame for missing dimensions and invalid coordinates', () => {
    expect(kitchenDepth(320, 230, { ...rect, width: 0 })).toEqual({ x: 0, y: 0, rotateX: 0, rotateY: 0 })
    expect(kitchenDepth(NaN, 230, rect)).toEqual({ x: 0, y: 0, rotateX: 0, rotateY: 0 })
  })
})
