import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Check, KeyRound, LoaderCircle, Save } from 'lucide-react'
import { motion } from 'motion/react'
import { getSettings, saveSettings } from '../lib/api'
import type { AccessSettings, UpdateAccessSettings } from '../lib/types'
import { buttonMotion } from '../lib/motion-presets'
import { useLiveReducedMotion } from '../lib/use-live-reduced-motion'
import './settings-dialog.css'

/** Content only: App's existing Modal owns Escape, focus trapping and restoration. */
export default function SettingsDialog({ globalSaving, onSaveStart, onSaved, onSettled }: {
  globalSaving: boolean;
  onSaveStart: () => boolean;
  onSaved: () => void;
  onSettled: () => void;
}) {
  const reducedMotion = useLiveReducedMotion()
  const id = useId()
  const [settings, setSettings] = useState<AccessSettings | null>(null)
  const [deepseekKey, setDeepseekKey] = useState('')
  const [mcpKey, setMcpKey] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const active = useRef(true)
  const loadId = useRef(0)
  const saveLock = useRef(false)
  const initiatedHere = useRef(false)
  const previousGlobalSaving = useRef(globalSaving)

  const load = useCallback(async () => {
    const request = ++loadId.current
    setLoading(true); setError('')
    try {
      const result = await getSettings()
      if (active.current && request === loadId.current) setSettings(result)
    } catch (cause) {
      if (active.current && request === loadId.current) setError(cause instanceof Error ? cause.message : '配置读取失败，请重试。')
    } finally {
      if (active.current && request === loadId.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    active.current = true
    void load()
    return () => { active.current = false; loadId.current += 1 }
  }, [load])

  useEffect(() => {
    const released = previousGlobalSaving.current && !globalSaving
    previousGlobalSaving.current = globalSaving
    // A reopened dialog may have read the old configured flags while another
    // instance was still saving. Refresh that status once the global save ends.
    if (released && !initiatedHere.current) void load()
  }, [globalSaving, load])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (saveLock.current || loading || !settings) return
    if (!onSaveStart()) {
      setError('设置正在保存或订单正在提交，请稍后再试。')
      return
    }
    saveLock.current = true
    initiatedHere.current = true
    setSaving(true); setError(''); setSuccess(false)
    // Only transient React state holds entered values. A blank field is not a deletion.
    const payload: UpdateAccessSettings = {}
    if (deepseekKey.trim()) payload.deepseek_api_key = deepseekKey.trim()
    if (mcpKey.trim()) payload.mcd_mcp_key = mcpKey.trim()
    try {
      const result = await saveSettings(payload)
      if (active.current) {
        setSettings(result); setDeepseekKey(''); setMcpKey(''); setSuccess(true)
      }
      // Persistence can finish after the user closes the dialog. Invalidate old quotes
      // in that case too, without retaining the keys in App or browser storage.
      onSaved()
    } catch (cause) {
      if (active.current) setError(cause instanceof Error ? cause.message : '保存失败，请重试。')
    } finally {
      saveLock.current = false
      // The lock is App-owned, so closing/unmounting this content must not leak it.
      onSettled()
      if (active.current) setSaving(false)
    }
  }

  const savePending = saving || globalSaving
  const disabled = loading || savePending || !settings
  const edit = () => { setSuccess(false); setError('') }
  return <form className="settings-form" onSubmit={submit} aria-busy={loading || savePending}>
    <div className="settings-model"><span className="settings-model-icon"><KeyRound size={21} aria-hidden="true" /></span><div><strong>接入小厨房</strong><p>固定使用 DeepSeek · <span>deepseek-flash</span></p></div></div>
    <p className="settings-intro" id={`${id}-hint`}>填入新的 Key 即可更新，留空保留原配置。</p>
    <div className="settings-field">
      <div className="settings-label"><label htmlFor={`${id}-deepseek`}>DeepSeek API Key</label><span className={`settings-configured ${settings?.deepseek_configured ? 'is-configured' : ''}`} aria-label="DeepSeek 配置状态">{settings ? settings.deepseek_configured ? '已配置' : '未配置' : '待确认'}</span></div>
      <input id={`${id}-deepseek`} type="password" value={deepseekKey} onChange={event => { setDeepseekKey(event.target.value); edit() }} disabled={disabled} placeholder="填写新 Key" autoComplete="new-password" autoCapitalize="none" spellCheck={false} aria-describedby={`${id}-hint`} />
    </div>
    <div className="settings-field">
      <div className="settings-label"><label htmlFor={`${id}-mcp`}>麦当劳 MCP Key</label><span className={`settings-configured ${settings?.mcd_mcp_configured ? 'is-configured' : ''}`} aria-label="麦当劳 MCP 配置状态">{settings ? settings.mcd_mcp_configured ? '已配置' : '未配置' : '待确认'}</span></div>
      <input id={`${id}-mcp`} type="password" value={mcpKey} onChange={event => { setMcpKey(event.target.value); edit() }} disabled={disabled} placeholder="填写新 Key" autoComplete="new-password" autoCapitalize="none" spellCheck={false} aria-describedby={`${id}-hint`} />
    </div>
    <p className="settings-note">Key 保存到服务端，本页不会回显已有值。已配置不代表已验证连接。</p>
    <div className="settings-feedback" aria-live="polite">
      {loading && <p className="settings-loading" role="status"><LoaderCircle className="spinner" size={15} aria-hidden="true" />正在读取配置…</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {success && <p className="settings-success" role="status"><Check size={16} aria-hidden="true" />设置已保存。</p>}
    </div>
    {!loading && !settings && <button className="secondary-button settings-retry" type="button" onClick={() => void load()}>重新读取</button>}
    <motion.button className="primary-button settings-save" type="submit" disabled={disabled} {...buttonMotion(reducedMotion, disabled)}>{savePending ? <LoaderCircle className="spinner" size={17} aria-hidden="true" /> : <Save size={17} aria-hidden="true" />}{savePending ? '正在保存…' : '保存设置'}</motion.button>
  </form>
}
