import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import stores from '../../docs/api-examples/stores.json'
import sample from '../../docs/api-examples/plans_6人150元2人不吃辣.json'

const configured = { provider: 'deepseek', model: 'deepseek-flash', deepseek_configured: true, mcd_mcp_configured: false }
const envelope = (data: unknown) => new Response(JSON.stringify({ code: 0, data, msg: 'ok' }))
let save: (body: Record<string, string>) => Promise<Response>
let load: () => Promise<Response>
let posted: Record<string, string>[]
let storeReads: number

beforeEach(() => {
  sessionStorage.clear()
  posted = []; storeReads = 0
  load = async () => envelope(configured)
  save = async () => envelope({ ...configured, mcd_mcp_configured: true })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    if (String(url).includes('/stores?')) { storeReads += 1; return envelope({ ...stores.data, data_source: 'live_mcp' }) }
    if (String(url).endsWith('/plans')) return envelope({ ...sample.data, data_source: 'live_mcp', algorithm_source: 'llm_skill' })
    if (String(url).endsWith('/settings')) {
      if (init.method === 'GET') return load()
      const body = JSON.parse(String(init.body)) as Record<string, string>
      posted.push(body)
      return save(body)
    }
    throw new Error('Unexpected fixture URL')
  }))
})
afterEach(() => vi.unstubAllGlobals())

async function openSettings() {
  render(<App />)
  await screen.findByRole('button', { name: /广州今日广场/ })
  const button = screen.getByRole('button', { name: '设置' })
  button.focus(); fireEvent.click(button)
  return screen.getByRole('dialog', { name: '设置' })
}
async function ready() {
  const dialog = await openSettings()
  await waitFor(() => expect(within(dialog).getByLabelText('DeepSeek API Key')).toBeEnabled())
  return dialog
}

describe('browser Key settings', () => {
  it('shows loading, password inputs, fixed DeepSeek model and configured-only status', async () => {
    let resolve!: (value: Response) => void
    load = () => new Promise(yes => { resolve = yes })
    const dialog = await openSettings()
    expect(within(dialog).getByRole('status')).toHaveTextContent('正在读取配置')
    expect(within(dialog).getByRole('button', { name: '保存设置' })).toBeDisabled()
    await act(async () => resolve(envelope(configured)))
    expect(within(dialog).getByLabelText('DeepSeek API Key')).toHaveAttribute('type', 'password')
    expect(within(dialog).getByLabelText('麦当劳 MCP Key')).toHaveAttribute('type', 'password')
    expect(within(dialog).getByLabelText('DeepSeek 配置状态')).toHaveTextContent('已配置')
    expect(within(dialog).getByLabelText('麦当劳 MCP 配置状态')).toHaveTextContent('未配置')
    expect(within(dialog).getByText('deepseek-flash')).toBeInTheDocument()
    expect(within(dialog).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/URL/)).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/连接成功/)).not.toBeInTheDocument()
  })

  it('submits exactly the two entered keys, clears both after success and writes no browser storage or cookie', async () => {
    const dialog = await ready()
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const originalCookie = document.cookie
    fireEvent.change(within(dialog).getByLabelText('DeepSeek API Key'), { target: { value: 'fixture-deepseek-key' } })
    fireEvent.change(within(dialog).getByLabelText('麦当劳 MCP Key'), { target: { value: 'fixture-mcp-key' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await within(dialog).findByText('设置已保存。')
    expect(posted).toEqual([{ deepseek_api_key: 'fixture-deepseek-key', mcd_mcp_key: 'fixture-mcp-key' }])
    expect(within(dialog).getByLabelText('DeepSeek API Key')).toHaveValue('')
    expect(within(dialog).getByLabelText('麦当劳 MCP Key')).toHaveValue('')
    expect(within(dialog).getByLabelText('麦当劳 MCP 配置状态')).toHaveTextContent('已配置')
    expect(storage).not.toHaveBeenCalled()
    expect(document.cookie).toBe(originalCookie)
  })

  it('omits both empty fields so saving preserves stored keys', async () => {
    const dialog = await ready()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await within(dialog).findByText('设置已保存。')
    expect(posted).toEqual([{}])
    expect(within(dialog).getByText(/留空保留/)).toBeInTheDocument()
  })

  it('omits a whitespace-only field when updating only MCP', async () => {
    const dialog = await ready()
    fireEvent.change(within(dialog).getByLabelText('DeepSeek API Key'), { target: { value: '   ' } })
    fireEvent.change(within(dialog).getByLabelText('麦当劳 MCP Key'), { target: { value: 'fixture-mcp-key' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await within(dialog).findByText('设置已保存。')
    expect(posted).toEqual([{ mcd_mcp_key: 'fixture-mcp-key' }])
  })

  it('retains entered keys after a save error, makes one request and allows an explicit retry', async () => {
    save = async () => new Response(JSON.stringify({ code: 'E_SETTINGS_SAVE', data: null, msg: '保存失败，请重试。' }), { status: 500 })
    const dialog = await ready()
    fireEvent.change(within(dialog).getByLabelText('DeepSeek API Key'), { target: { value: 'fixture-deepseek-key' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('保存失败')
    expect(within(dialog).getByLabelText('DeepSeek API Key')).toHaveValue('fixture-deepseek-key')
    expect(within(dialog).getByRole('button', { name: '保存设置' })).toBeEnabled()
    expect(posted).toHaveLength(1)
    save = async () => envelope(configured)
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await within(dialog).findByText('设置已保存。')
    expect(posted).toHaveLength(2)
  })

  it('locks form actions while a save is pending', async () => {
    let resolve!: (value: Response) => void
    save = () => new Promise(yes => { resolve = yes })
    const dialog = await ready()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    expect(within(dialog).getByRole('button', { name: '正在保存…' })).toBeDisabled()
    expect(within(dialog).getByLabelText('DeepSeek API Key')).toBeDisabled()
    expect(within(dialog).getByLabelText('麦当劳 MCP Key')).toBeDisabled()
    await act(async () => resolve(envelope(configured)))
    expect(posted).toHaveLength(1)
  })

  it('offers a load retry and does not allow saving before settings are read', async () => {
    load = async () => new Response(JSON.stringify({ code: 'E_SETTINGS', data: null, msg: '配置读取失败。' }), { status: 503 })
    const dialog = await openSettings()
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('配置读取失败')
    expect(within(dialog).getByRole('button', { name: '保存设置' })).toBeDisabled()
    load = async () => envelope(configured)
    fireEvent.click(within(dialog).getByRole('button', { name: '重新读取' }))
    await waitFor(() => expect(within(dialog).getByLabelText('DeepSeek API Key')).toBeEnabled())
    expect(posted).toEqual([])
  })

  it('reuses the focus trap, closes with Escape and restores the settings trigger', async () => {
    const dialog = await ready()
    const close = within(dialog).getByRole('button', { name: '关闭弹窗' })
    const saveButton = within(dialog).getByRole('button', { name: '保存设置' })
    close.focus(); fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(saveButton).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(close).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '设置' })).toHaveFocus()
    expect(document.querySelector<HTMLElement>('.app-shell')?.inert).toBe(false)
  })

  it('invalidates old plans and reloads stores after saving', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(screen.getByRole('button', { name: '看看怎么点' }))
    await screen.findAllByRole('button', { name: /就选这份/ })
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    const dialog = screen.getByRole('dialog', { name: '设置' })
    await waitFor(() => expect(within(dialog).getByLabelText('DeepSeek API Key')).toBeEnabled())
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    await within(dialog).findByText('设置已保存。')
    await waitFor(() => expect(screen.queryAllByRole('button', { name: /就选这份/ })).toHaveLength(0))
    await waitFor(() => expect(storeReads).toBe(2))
  })

  it('still refreshes the app when a pending save succeeds after the dialog is closed', async () => {
    let resolve!: (value: Response) => void
    save = () => new Promise(yes => { resolve = yes })
    const dialog = await ready()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存设置' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭弹窗' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await act(async () => resolve(envelope(configured)))
    await waitFor(() => expect(storeReads).toBe(2))
    expect(posted).toEqual([{}])
  })
})
