import { describe, expect, it } from 'vitest'
import type { UserConfig } from 'vite'
import config from '../vite.config'

describe('development session isolation', () => {
  it('does not reload the live form when verification copies create HTML or TypeScript configs', () => {
    expect((config as UserConfig).server?.watch?.ignored).toContain('**/verification/**')
  })
})
