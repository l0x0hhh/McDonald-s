import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../src/lib/api'

const status = { provider: 'deepseek', model: 'deepseek-flash', deepseek_configured: true, mcd_mcp_configured: false }
afterEach(() => vi.unstubAllGlobals())

describe('settings HTTP contract', () => {
  it('reads only configuration status through the same-origin settings endpoint', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: status, msg: 'ok' })))
    vi.stubGlobal('fetch', fetch)
    expect(await api.getSettings()).toEqual(status)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/settings')
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'omit' })
    expect(fetch.mock.calls[0][1].body).toBeUndefined()
  })

  it('posts exactly the supplied fields and uses the ordinary request error rather than checkout uncertainty', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('fixture network down'))
    vi.stubGlobal('fetch', fetch)
    await expect(api.saveSettings({ deepseek_api_key: 'fixture-deepseek-key', mcd_mcp_key: 'fixture-mcp-key' })).rejects.toMatchObject({ code: 'E_NETWORK' })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/settings')
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'omit' })
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ deepseek_api_key: 'fixture-deepseek-key', mcd_mcp_key: 'fixture-mcp-key' })
  })

  it('sends an empty object as POST when both fields are omitted', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: status, msg: 'ok' })))
    vi.stubGlobal('fetch', fetch)
    expect(await api.saveSettings({})).toEqual(status)
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST', body: '{}' })
  })
})
