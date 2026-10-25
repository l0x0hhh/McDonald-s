import { afterEach, describe, expect, it, vi } from 'vitest'
import { newIdempotencyKey } from '../src/lib/idempotency-key'

afterEach(() => vi.unstubAllGlobals())
describe('cryptographic checkout request identity', () => {
  it('uses a native UUID when available', () => {
    const randomUUID = vi.fn(() => 'native-id')
    vi.stubGlobal('crypto', { randomUUID })
    expect(newIdempotencyKey()).toBe('native-id')
    expect(randomUUID).toHaveBeenCalledTimes(1)
  })
  it('constructs v4 version and variant bits from 16 cryptographic bytes on LAN HTTP', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => bytes.fill(0xff))
    vi.stubGlobal('crypto', { getRandomValues })
    expect(newIdempotencyKey()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff')
    expect(getRandomValues).toHaveBeenCalledTimes(1)
    expect(getRandomValues.mock.calls[0][0]).toHaveLength(16)
  })
  it('does not use weak random fallbacks if no cryptographic source is available', () => {
    vi.stubGlobal('crypto', {})
    const weak = vi.spyOn(Math, 'random')
    expect(() => newIdempotencyKey()).toThrow()
    expect(weak).not.toHaveBeenCalled()
  })
})
