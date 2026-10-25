import { describe, expect, it } from 'vitest'
import type { ProxyOptions, UserConfig } from 'vite'
import config from '../vite.config'

describe('same-origin local API forwarding', () => {
  it.each(['server', 'preview'] as const)('forwards /api on %s without exposing a browser loopback URL', (mode) => {
    const proxy = (config as UserConfig)[mode]?.proxy?.['/api'] as ProxyOptions | undefined
    expect(proxy?.target).toBe('http://127.0.0.1:18000')
    // Preserve the browser-facing authority for same-origin key settings checks.
    expect(proxy?.changeOrigin).toBe(false)
    expect(proxy?.rewrite).toBeUndefined()
  })
})
