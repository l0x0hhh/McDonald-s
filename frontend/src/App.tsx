import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { AnimatePresence, LayoutGroup, MotionConfig, motion, usePresence } from 'motion/react'
import { ArrowDown, ArrowRight, ArrowUpRight, BadgeCheck, BookOpen, ChefHat, Check, ChevronDown, CircleHelp, ClipboardList, CookingPot, Flame, Heart, Info, Leaf, LoaderCircle, LockKeyhole, MapPin, Minus, Monitor, Plus, ReceiptText, RotateCcw, Search, Send, ShieldCheck, Sparkles, Store as StoreIcon, Users, Utensils, Wallet, Wifi, X } from 'lucide-react'
import PlanCards, { FoodImage } from './components/PlanCards'
import HeroScene from './components/HeroScene'
import KitchenFeedback from './components/KitchenFeedback'
import OpeningCopy from './components/OpeningCopy'
import { ApiError, clearDemoPlans, createOrder, DEMO_NOTICE, formatMoney, getPlans, getStores, parseChat } from './lib/api'
import type { ChatResponse, Mode, OrderResponse, Plan, PlanResponse, Requirements, RequirementsDraft, Store, StoreListResponse } from './lib/types'
import { buttonMotion, dialogMotion, gameSpring } from './lib/motion-presets'
import { planSharedTransition, planTitles, planTransitionId, sharedPlanElement } from './lib/plan-transition'
import { useLiveReducedMotion } from './lib/use-live-reduced-motion'

type Brief = { people: number; budget: string; budgetType: 'total' | 'per_person'; nonSpicy: number }
const defaultBrief: Brief = { people: 6, budget: '150', budgetType: 'total', nonSpicy: 2 }
const spring = gameSpring

function readBrief(brief: Brief): Requirements | null {
  if (!Number.isInteger(brief.people) || brief.people < 1 || brief.people > 20 || !/^\d+(\.\d{1,2})?$/.test(brief.budget)
    || !Number.isInteger(brief.nonSpicy) || brief.nonSpicy < 0 || brief.nonSpicy > brief.people) return null
  return { people: brief.people, budget_type: brief.budgetType, budget_fen: Math.round(Number(brief.budget) * 100), dietary_constraints: [{ kind: 'non_spicy', people: brief.nonSpicy }], appetite: 'normal', preferred_tags: [] }
}

function fromRequirements(req: Requirements): Brief {
  return { people: req.people, budget: String(req.budget_fen / 100), budgetType: req.budget_type, nonSpicy: req.dietary_constraints.reduce((sum, item) => sum + item.people, 0) }
}

// Dialogs trap keyboard focus, restore it on exit, and keep background content inert.
function Modal({ title, onClose, children, sharedPlanId }: { title: string; onClose: () => void; children: ReactNode; sharedPlanId?: string }) {
  const reducedMotion = useLiveReducedMotion()
  const [isPresent, safeToRemove] = usePresence()
  const [exitFinished, setExitFinished] = useState(false)
  const dialog = useRef<HTMLDivElement>(null)
  const restorePage = useRef<() => void>(() => {})
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    if (isPresent) return
    // Layout measurement can disappear on resize/navigation. Never leave a faded
    // receipt trapping focus after its bounded return-to-tray transition.
    const deadline = window.setTimeout(() => {
      restorePage.current()
      setExitFinished(true)
    }, reducedMotion ? 0 : sharedPlanId ? planSharedTransition.duration * 1000 : 180)
    return () => window.clearTimeout(deadline)
  }, [isPresent, reducedMotion, sharedPlanId])
  useEffect(() => {
    // Commit the empty subtree first: projection children must unregister before
    // completing our own presence registration, otherwise an old exit can linger.
    if (exitFinished) safeToRemove?.()
  }, [exitFinished, safeToRemove])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const shell = document.querySelector<HTMLElement>('.app-shell')
    if (shell) shell.inert = true
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.current?.querySelector<HTMLElement>('button, input, [tabindex="0"]')?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current()
      if (event.key !== 'Tab') return
      const items = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href], select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')
      if (!items?.length) return
      const first = items[0], last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    let restored = false
    restorePage.current = () => {
      if (restored) return
      restored = true
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
      if (shell) shell.inert = false
      if (previous?.isConnected) previous.focus()
    }
    return () => restorePage.current()
  }, [])
  if (exitFinished) return null
  const linkedReceipt = Boolean(sharedPlanId)
  return <motion.div className="modal-backdrop" data-reduced-motion={reducedMotion} layoutRoot initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: .18 }} onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <motion.div className="modal" ref={dialog} role="dialog" aria-modal="true" aria-label={title} layoutScroll
      {...(linkedReceipt ? { initial: { opacity: 1 }, animate: { opacity: 1 }, exit: { opacity: 0 } } : dialogMotion(reducedMotion))}
      transition={linkedReceipt ? { opacity: { duration: .14 } } : dialogMotion(reducedMotion).transition}>
      <div className="modal-top"><div className="modal-handle" aria-hidden="true" /><h3>{title}</h3><motion.button className="icon-button" aria-label="关闭弹窗" onClick={onClose} {...buttonMotion(reducedMotion)}><X size={17} /></motion.button></div>
      <div className="modal-content">{children}</div>
    </motion.div>
  </motion.div>
}

function Stepper({ label, value, min, max, onChange, prefix }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void; prefix: string }) {
  const reducedMotion = useLiveReducedMotion()
  return <div className="stepper">
    <motion.button type="button" aria-label={`减少${prefix}`} disabled={value <= min} {...buttonMotion(reducedMotion, value <= min)} onClick={() => onChange(value - 1)}><Minus size={14} /></motion.button>
    <input type="number" aria-label={label} min={min} max={max} value={Number.isNaN(value) ? '' : value} onChange={(event) => onChange(event.target.value === '' ? Number.NaN : Number(event.target.value))} />
    <span>人</span>
    <motion.button type="button" aria-label={`增加${prefix}`} disabled={value >= max} {...buttonMotion(reducedMotion, value >= max)} onClick={() => onChange(Number.isNaN(value) ? min : value + 1)}><Plus size={14} /></motion.button>
  </div>
}

export default function App() {
  const reducedMotion = useLiveReducedMotion()
  const planTransitionScope = useId()
  const [mode, setMode] = useState<Mode>('demo')
  const [brief, setBrief] = useState<Brief>(defaultBrief)
  const [storeData, setStoreData] = useState<StoreListResponse | null>(null)
  const [selectedStore, setSelectedStore] = useState<Store | null>(null)
  const [storeOpen, setStoreOpen] = useState(false)
  const [storeLoading, setStoreLoading] = useState(true)
  const [city, setCity] = useState('广州')
  const [keyword, setKeyword] = useState('')
  const [storeError, setStoreError] = useState('')
  const [message, setMessage] = useState('')
  const [chat, setChat] = useState<ChatResponse | null>(null)
  const [chatDraft, setChatDraft] = useState<RequirementsDraft | null>(null)
  const [unresolvedChat, setUnresolvedChat] = useState(false)
  const [pendingPreferences, setPendingPreferences] = useState(false)
  const [chatBusy, setChatBusy] = useState(false)
  const [plansBusy, setPlansBusy] = useState(false)
  const [planError, setPlanError] = useState('')
  const [response, setResponse] = useState<PlanResponse | null>(null)
  const [selectedPlan, setSelectedPlan] = useState<Plan | null>(null)
  const [checkoutSession, setCheckoutSession] = useState(0)
  const [order, setOrder] = useState<OrderResponse | null>(null)
  const [lastOrder, setLastOrder] = useState<OrderResponse | null>(null)
  const [orderBusy, setOrderBusy] = useState(false)
  const [error, setError] = useState('')
  const [orderError, setOrderError] = useState('')
  const [helpOpen, setHelpOpen] = useState(false)
  const [toast, setToast] = useState('')
  const requestId = useRef(0)
  const orderRequestId = useRef(0)
  const storeRequestId = useRef(0)
  const taskRef = useRef<HTMLElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)
  const requirements = readBrief(brief)

  // Every changed input invalidates the selection and rejects stale async responses.
  const invalidate = useCallback(() => {
    requestId.current += 1
    orderRequestId.current += 1
    clearDemoPlans()
    setResponse(null); setSelectedPlan(null); setOrder(null); setPlansBusy(false)
    setChatBusy(false); setOrderBusy(false); setError(''); setPlanError(''); setOrderError(''); setUnresolvedChat(false)
  }, [])

  const loadStores = useCallback(async (currentMode: Mode, searchCity: string, searchKeyword: string, pickDefault = false) => {
    const id = ++storeRequestId.current
    setStoreLoading(true); setStoreError('')
    try {
      const result = await getStores(currentMode, searchCity, searchKeyword)
      if (id !== storeRequestId.current) return
      setStoreData(result)
      if (pickDefault) {
        invalidate()
        setSelectedStore(result.stores.find((store) => store.store_code === '1400011') ?? result.stores[0] ?? null)
      }
    } catch (cause) {
      if (id === storeRequestId.current) { setStoreError(cause instanceof Error ? cause.message : '门店读取失败，请重试。'); setStoreData(null) }
    } finally { if (id === storeRequestId.current) setStoreLoading(false) }
  }, [invalidate])

  useEffect(() => {
    void loadStores(mode, '广州', '', true)
    return () => { storeRequestId.current += 1 }
  }, [mode, loadStores])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3500)
    return () => window.clearTimeout(timer)
  }, [toast])

  function updateBrief(patch: Partial<Brief>) {
    invalidate(); setChat(null); setChatDraft(null); setBrief((old) => ({ ...old, ...patch }))
  }

  function changeMode(next: Mode) {
    if (next === mode) return
    invalidate(); setChat(null); setChatDraft(null); setSelectedStore(null); setStoreData(null); setLastOrder(null)
    setCity('广州'); setKeyword(''); setMode(next)
  }

  function resetSample() {
    invalidate(); setPendingPreferences(false); setBrief(defaultBrief); setChat(null); setChatDraft(null)
    const sampleStore = storeData?.stores.find((store) => store.store_code === '1400011')
    if (sampleStore) setSelectedStore(sampleStore)
    else { setSelectedStore(null); void loadStores(mode, '广州', '', true) }
    setToast('已填好：6 人、总预算 150 元、2 人不辣')
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault()
    if (!message.trim() || chatBusy) return
    invalidate()
    const id = requestId.current
    setChatBusy(true)
    try {
      const result = await parseChat(mode, message.trim(), mode === 'api' && chatDraft ? chatDraft : requirements)
      if (id !== requestId.current) return
      // V1.1 drafts stay authoritative across follow-ups; form defaults must not fill missing fields.
      setChatDraft(mode === 'api' ? result.draft ?? null : null)
      // New backend constraints remain blocked until this UI can submit them faithfully or the user explicitly clears them.
      const receivedPreferences = [result.requirements, result.draft].some((value) => value && (
        (value.appetite != null && value.appetite !== 'normal') || (value.preferred_tags?.length ?? 0) > 0
        || value.exclude_beverage === true || (value.membership_status != null && value.membership_status !== 'unknown')
        || value.meal_frequency_per_month != null || value.habit_horizon_months != null || (value.habitual_tags?.length ?? 0) > 0
      ))
      if (receivedPreferences || pendingPreferences) {
        setPendingPreferences(true)
        setChat({ ...result, ready: false, follow_up: [result.follow_up, '已保留当前前端尚未开放的新增要求；如继续体验基础流程，请明确采用基础需求。'].filter(Boolean).join(' ') })
        setUnresolvedChat(true)
      } else { setChat(result); setUnresolvedChat(!result.ready) }
      if (result.requirements) setBrief(fromRequirements(result.requirements))
      setMessage('')
    } catch (cause) { if (id === requestId.current) setError(cause instanceof Error ? cause.message : '需求解析失败，请重试。') }
    finally { if (id === requestId.current) setChatBusy(false) }
  }

  async function generatePlans(event?: FormEvent) {
    event?.preventDefault()
    if (!requirements || !selectedStore || unresolvedChat || pendingPreferences || plansBusy || chatBusy || storeLoading) return
    invalidate()
    const id = requestId.current
    setPlansBusy(true)
    try {
      const result = await getPlans(mode, selectedStore.store_code, requirements)
      if (id !== requestId.current) { clearDemoPlans(); return }
      setResponse(result)
      if (window.innerWidth <= 720) resultsRef.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' })
    } catch (cause) {
      if (id === requestId.current) {
        setPlanError(cause instanceof Error ? cause.message : '方案生成失败，请重试。')
        if (window.innerWidth <= 720) resultsRef.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' })
      }
    }
    finally { if (id === requestId.current) setPlansBusy(false) }
  }

  async function checkout() {
    if (!selectedPlan || orderBusy) return
    const id = requestId.current
    const orderId = ++orderRequestId.current
    setOrderBusy(true); setOrderError('')
    try {
      const result = await createOrder(mode, selectedPlan.plan_id)
      if (id !== requestId.current || orderId !== orderRequestId.current) return
      if (result.status !== 'simulated' || result.is_demo !== true) throw new Error('结算状态与模拟模式不一致，请重新生成方案。')
      if (result.total_fen !== selectedPlan.total_fen) throw new Error('结算金额已变化，请重新生成方案后确认。')
      setOrder(result); setLastOrder(result)
    } catch (cause) {
      if (id !== requestId.current || orderId !== orderRequestId.current) return
      if (cause instanceof ApiError && ['E_PLAN_EXPIRED', 'E_PLAN_NOT_FOUND'].includes(cause.code)) {
        invalidate(); setError(`${cause.message} 请重新生成开饭计划。`)
      } else setOrderError(cause instanceof Error ? cause.message : '模拟结算失败，请重试。')
    } finally { if (orderId === orderRequestId.current) setOrderBusy(false) }
  }

  const closeCheckout = () => { orderRequestId.current += 1; setSelectedPlan(null); setOrder(null); setOrderError(''); setOrderBusy(false) }
  const choosePlan = (plan: Plan) => { orderRequestId.current += 1; setCheckoutSession((session) => session + 1); setOrderBusy(false); setSelectedPlan(plan); setOrder(null); setOrderError('') }
  const scrollToTask = () => { taskRef.current?.scrollIntoView({ behavior: 'smooth' }); window.setTimeout(() => document.getElementById('meal-message')?.focus({ preventScroll: true }), 400) }
  const sourceText = response?.data_source === 'live_mcp' ? '在线菜单' : '历史快照'
  const demoAlgorithm = response?.algorithm_source === 'demo_sample'
    || (response?.algorithm_source == null && response?.suggestions.some((item) => item.includes('demo_sample')))
  const algorithmLabel = response?.algorithm_source === 'skill' ? ' · 正式方案算法'
    : demoAlgorithm ? ' · 演示算法 demo_sample' : response?.algorithm_source === 'unavailable' ? ' · 方案算法暂不可用' : ''

  return <MotionConfig transition={spring} reducedMotion={reducedMotion ? 'always' : 'never'}>
    <LayoutGroup id={planTransitionScope}>
    <div className="app-shell" data-reduced-motion={reducedMotion}>
      <header className="site-header"><div className="container header-inner">
        <a className="brand" href="#" aria-label="麦门省钱助手首页"><span className="brand-mark"><ChefHat size={32} strokeWidth={2.3} /></span><span><span className="brand-name">麦门省钱助手</span><small>MAIMEN KITCHEN</small></span></a>
        <nav className="main-nav" aria-label="主导航"><button className="nav-link active" onClick={scrollToTask}>开饭工作台</button><button className="nav-link" onClick={() => { if (lastOrder) { closeCheckout(); setOrder(lastOrder) } else setToast('还没有开饭计划，先选一份方案吧。') }}>我的开饭计划</button><button className="nav-link" onClick={() => setHelpOpen(true)}>玩法说明 <ArrowUpRight size={11} /></button></nav>
        <div className="mode-switch" aria-label="数据模式"><button className={mode === 'demo' ? 'selected' : ''} aria-pressed={mode === 'demo'} onClick={() => changeMode('demo')}><Monitor size={12} />离线样例</button><button className={mode === 'api' ? 'selected' : ''} aria-pressed={mode === 'api'} onClick={() => changeMode('api')}><Wifi size={12} />连接后端</button></div>
      </div></header>

      <main>
        <section className="hero"><div className="container hero-inner">
          <OpeningCopy reducedMotion={reducedMotion}>
            <div className="eyebrow"><Sparkles size={15} /> 省钱这件事，交给小厨神。</div>
            <h1>今天，<span className="red-word">开饭！</span></h1>
            <p className="hero-description">几个人，多少钱，想吃点什么？<br />把纠结交给小厨房，把预算花在想吃的东西上。</p>
            <div className="hero-actions"><motion.button className="primary-button" {...buttonMotion(reducedMotion)} onClick={scrollToTask}>开始配餐 <ArrowDown size={16} /></motion.button><span className="small-note">不必精打细算，也能好好吃饭。</span></div>
            <div className="hero-promises"><span><BadgeCheck size={13} />预算有数</span><span><Users size={13} />大家吃得够</span><span><Heart size={13} />口味被照顾</span></div>
          </OpeningCopy>
          <HeroScene paused={storeOpen || Boolean(selectedPlan) || Boolean(order) || helpOpen} />
        </div></section>

        <div className="kitchen-strip"><div className="container steps" aria-label="点餐步骤"><div className="step active"><span className="step-number">1</span>写下开饭任务<small>说说你的需求</small></div><span className="step-line" /><div className={`step ${response?.plans.length ? 'active' : ''}`}><span className="step-number">2</span>挑一份好方案<small>明明白白选餐</small></div><span className="step-line" /><div className={`step ${order ? 'active' : ''}`}><span className="step-number">3</span>就位，准备开饭<small>模拟结算确认</small></div></div></div>

        <section className="workbench" ref={taskRef}><div className="container">
          <div className="section-heading"><div><h2><CookingPot size={25} />你的开饭任务</h2><p>人数、预算和小偏好，都可以在这里告诉我。</p></div><span className="tiny-label"><span className="status-dot" />{mode === 'demo' ? '离线预览 · 固定样例数据' : '后端接口 · 以实际响应为准'}</span></div>
          <div className="workbench-grid">
            <section className="brief-panel" aria-label="点餐需求">
              <div className="panel-heading"><h3><ClipboardList size={19} />点餐小纸条</h3><span className="ticket-id">ORDER / 001</span></div>
              <div className="brief-body">
                <button className="store-button" onClick={() => { setStoreOpen(true); setKeyword(''); setCity('广州'); void loadStores(mode, '广州', '') }} aria-label={selectedStore ? `${selectedStore.name}，切换门店` : '选择用餐门店'}><MapPin size={20} /><span className="store-copy"><strong>{selectedStore?.name.replace('麦当劳', '') || (storeLoading ? '正在找小厨房…' : '选择用餐门店')}</strong><small>{selectedStore ? `到店自取 · ${selectedStore.business_status === true ? '营业中' : selectedStore.business_status === false ? '已休息' : '营业状态未知'}` : '按城市与关键词查找门店'}</small></span><ChevronDown size={15} /></button>
                {storeError && <div className="inline-error" role="alert">{storeError}<button onClick={() => void loadStores(mode, '广州', '', true)}>重试</button></div>}
                <label className="chat-label" htmlFor="meal-message"><ChefHat size={16} />和小厨神说一声<span>{mode === 'demo' ? '固定句式预览' : '需求解析由后端提供'}</span></label>
                <form className="chat-composer" onSubmit={sendMessage}><textarea id="meal-message" rows={2} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="比如：6个人，总预算150元，2人不吃辣" /><div className="composer-footer"><span>或者，直接编辑下方的小纸条 ↓</span><motion.button type="submit" className="send-button" aria-label="发送点餐需求" disabled={!message.trim() || chatBusy} {...buttonMotion(reducedMotion, !message.trim() || chatBusy)}>{chatBusy ? <LoaderCircle size={15} className="spinner" /> : <Send size={15} />}</motion.button></div></form>
                <div className="quick-examples"><button onClick={resetSample}><Users size={10} />6 人聚餐 · 150 元</button><button onClick={() => { invalidate(); setPendingPreferences(false); setChat(null); setChatDraft(null); setBrief({ ...defaultBrief, budget: '30' }); setToast('已填入预算无解示例，点击生成看看。') }}><Wallet size={10} />预算紧一点？</button></div>
                {chat && <div className="assistant-reply" role="status"><ChefHat size={15} /><span>{chat.follow_up || '收到！需求已经填进小纸条，确认后就可以配餐了。'}<br /><small>{chat.parse_source === 'llm' ? '大模型解析' : chat.parse_source === 'rule' ? '固定规则解析' : '离线固定句式'}{chat.notes.length ? ` · ${chat.notes.join(' ')}` : ''}</small></span></div>}
                <div className="form-divider">确认一下，这次怎么吃</div>
                <form onSubmit={generatePlans}>
                  <div className="field-grid"><div className="field"><span className="field-label"><Users size={13} />几位一起吃</span><Stepper label="用餐人数" prefix="用餐人数" value={brief.people} min={1} max={20} onChange={(people) => updateBrief({ people })} /></div>
                    <div className="field"><label htmlFor="meal-budget"><Wallet size={13} />预算多少</label><div className="budget-input"><span>¥</span><input id="meal-budget" type="number" min="0" step="0.01" aria-label="预算金额" value={brief.budget} onChange={(event) => updateBrief({ budget: event.target.value })} /></div><div className="budget-type"><button type="button" className={brief.budgetType === 'total' ? 'active' : ''} aria-pressed={brief.budgetType === 'total'} onClick={() => updateBrief({ budgetType: 'total' })}>总预算</button><button type="button" className={brief.budgetType === 'per_person' ? 'active' : ''} aria-pressed={brief.budgetType === 'per_person'} onClick={() => updateBrief({ budgetType: 'per_person' })}>每人预算</button></div></div>
                    <div className="field"><span className="field-label"><Flame size={13} />几位不吃辣</span><Stepper label="不吃辣人数" prefix="不吃辣人数" value={brief.nonSpicy} min={0} max={20} onChange={(nonSpicy) => updateBrief({ nonSpicy })} /><div className="sub-field">只保障已核实的不辣餐份</div></div>
                    <div className="field"><span className="field-label"><Utensils size={13} />用餐方式</span><div className="budget-input" style={{ fontSize: 11, color: '#9b805e', gap: 8 }}><StoreIcon size={13} />到店自取</div><div className="sub-field">这一餐，堂堂正正好好吃</div></div></div>
                  <div className="preference-preview"><button type="button" disabled title="当前前端界面尚未接入无饮料条件"><LockKeyhole size={12} />不带饮料 <span className="coming-badge">待接入</span></button><button type="button" disabled title="当前前端界面尚未接入饭量与口味编辑"><LockKeyhole size={12} />饭量 / 口味 <span className="coming-badge">待接入</span></button></div>
                  {pendingPreferences && <div className="inline-error" role="alert">饭量与口味规划尚待接入当前前端界面；无饮料等新增要求也会保留，当前不会作为已执行条件。选择“使用基础需求”会移除这些新增要求。<button type="button" onClick={() => { invalidate(); setPendingPreferences(false); setChat(null); setChatDraft(null) }}>使用基础需求</button></div>}
                  {!requirements && <div className="inline-error" role="alert">人数请填 1–20 人；预算保留最多两位小数；不辣人数应在 0 与总人数之间。</div>}
                  {error && <div className="inline-error" role="alert">{error}</div>}
                  <motion.button type="submit" className="primary-button generate-button" aria-label="生成省钱方案" disabled={!requirements || !selectedStore || unresolvedChat || pendingPreferences || plansBusy || chatBusy || storeLoading} {...buttonMotion(reducedMotion, !requirements || !selectedStore || unresolvedChat || pendingPreferences || plansBusy || chatBusy || storeLoading)}>{plansBusy ? <LoaderCircle size={17} className="spinner" /> : <Sparkles size={17} />}{plansBusy ? '小厨房正在配餐…' : '生成省钱方案'}{!plansBusy && <ArrowRight size={16} />}</motion.button>
                  <p className="form-footnote">{mode === 'demo' ? '当前是固定离线样例，不会实时计算优惠。' : '价格与资格以接口返回为准，当前仅支持模拟结算。'}</p>
                </form>
              </div>
            </section>

            <section className="results-panel" ref={resultsRef} aria-label="配餐结果" aria-busy={plansBusy}>
              <div className="panel-heading results-heading"><h3><ReceiptText size={19} />小厨房出餐台</h3><small>{response ? `${response.plans.length} 份方案 · 供你挑选` : '你的好好吃饭计划，正在这里等你'}{Boolean(response?.plans.length) && <span className="results-scroll-hint">上下滚动查看方案</span>}</small></div>
              <motion.div className="results-content" role="region" aria-label="方案列表" tabIndex={0} layoutScroll>
              <AnimatePresence mode="wait" initial={false}>
                {plansBusy ? <KitchenFeedback key="loading" state="loading" mode={mode} />
                  : planError ? <KitchenFeedback key="error" state="error" message={planError} onRetry={() => { void generatePlans() }} retryDisabled={!requirements || !selectedStore || unresolvedChat || pendingPreferences || plansBusy || chatBusy || storeLoading} />
                  : response ? <motion.div key="results" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                    {response.plans.length > 0 && <KitchenFeedback state="success" count={response.plans.length} />}
                    <div className="source-note"><Info size={13} /><span>{sourceText} · {response.fetched_at}{algorithmLabel}<br />{mode === 'demo' ? DEMO_NOTICE : response.degraded_reason || '菜单参考价格；在线数据来源不等于最终收银报价。'}</span></div>
                    {response.plans.length ? <PlanCards plans={response.plans} people={response.requirements.people} nonSpicyPeople={response.requirements.dietary_constraints.reduce((sum, constraint) => sum + constraint.people, 0)} onSelect={choosePlan} /> : <div className="no-solution"><CookingPot size={47} strokeWidth={1.4} /><h4>这单，还差一点点</h4><p>{response.unsatisfiable_reason || '暂时没有符合条件的方案，请调整需求后再试。'}</p><button className="secondary-button" onClick={resetSample}><RotateCcw size={13} />恢复 150 元聚餐样例</button></div>}
                    <p className="results-footnote">{response.suggestions.filter((item) => item !== DEMO_NOTICE).join(' ')}</p>
                  </motion.div>
                    : <motion.div key="empty" className="empty-kitchen" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}><span className="empty-stamp">用心<br />配一餐</span><div className="plate"><ChefHat size={57} strokeWidth={1.5} /></div><h4>把需求交给我，<br />把好吃留给你。</h4><p>填好左边的小纸条，小厨房就会为你端上<br />有明细、有理由的开饭方案。</p><div className="empty-options"><div className="empty-option"><Wallet size={19} /><span>精打细算派</span></div><div className="empty-option"><Utensils size={19} /><span>样样都想吃</span></div><div className="empty-option"><Heart size={19} /><span>就爱这个味</span></div></div><p className="empty-help">先试试「6 人聚餐 · 150 元」固定样例吧。</p></motion.div>}
              </AnimatePresence>
              </motion.div>
            </section>
          </div>

          <div className="bottom-feature-strip"><div className="bottom-feature"><span className="feature-icon"><ShieldCheck size={19} /></span><div><strong>每一分钱，都有来处</strong><p>明细、数据来源与预算，一起说清楚。</p></div></div><div className="bottom-feature"><span className="feature-icon"><Leaf size={19} /></span><div><strong>营养信息，按需看看</strong><p>有数据就展示，未知的不会当作零。</p></div></div><div className="bottom-feature"><span className="feature-icon"><ReceiptText size={19} /></span><div><strong>放心试，这里是模拟结算</strong><p>只确认开饭计划，不产生真实扣款。</p></div></div></div>
        </div></section>
      </main>
      <footer className="site-footer"><div className="container footer-inner"><div className="footer-brand"><ChefHat size={17} />麦门省钱助手</div><p>独立点餐辅助演示 · 非麦当劳官方产品 · 价格以门店实际结算为准</p><button className="footer-help" onClick={() => setHelpOpen(true)}><CircleHelp size={12} />玩法说明</button><span>GOOD FOOD. GOOD MOOD.</span></div></footer>
    </div>

    <AnimatePresence>
      {storeOpen && <Modal key="store" title="选一家，今天的小厨房" onClose={() => setStoreOpen(false)}><form className="store-search" onSubmit={(event) => { event.preventDefault(); void loadStores(mode, city, keyword) }}><input aria-label="城市" value={city} onChange={(event) => setCity(event.target.value)} placeholder="城市" /><input aria-label="门店关键词" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜门店名、商圈或地址" /><button aria-label="搜索门店" disabled={storeLoading}>{storeLoading ? <LoaderCircle className="spinner" size={17} /> : <Search size={17} />}</button></form>{storeError && <div className="inline-error" role="alert">{storeError}</div>}<div className="store-results">{storeData?.stores.map((store) => <button className={`store-option ${store.store_code === selectedStore?.store_code ? 'selected' : ''}`} key={store.store_code} onClick={() => { if (store.store_code !== selectedStore?.store_code) { invalidate(); setChatDraft(null); setChat(null); setSelectedStore(store) }; setStoreOpen(false) }}><MapPin size={19} /><span><strong>{store.name.replace('麦当劳', '')}</strong><small>{store.address}<br />{store.business_status === true ? '营业中' : store.business_status === false ? '已休息' : '营业状态未知'} · {store.business_hours || '营业时间未知'}</small></span>{store.store_code === selectedStore?.store_code && <Check size={17} />}</button>)}</div>{!storeLoading && !storeError && !storeData?.stores.length && <p className="store-disclaimer">没有找到门店，换个关键词试试。</p>}<p className="store-disclaimer">{mode === 'demo' ? '离线样例仅展示广州历史门店快照，非实时定位。固定方案样例对应今日广场餐厅。' : `${storeData?.data_source === 'live_mcp' ? '在线门店数据' : '演示快照'} · ${storeData?.fetched_at || '等待数据'}。`}{storeData?.degraded_reason && mode === 'api' ? ` ${storeData.degraded_reason}` : ''}</p></Modal>}

      {(selectedPlan || order) && <Modal key={`checkout-${selectedPlan?.plan_id ?? order?.plan_id}-${checkoutSession}`} title={order ? '开饭计划已就位！' : '确认这份开饭计划'} onClose={closeCheckout} sharedPlanId={selectedPlan?.plan_id}>
        {order ? <div className="success-view"><motion.div className="success-seal" initial={{ scale: reducedMotion ? 1 : .3, rotate: reducedMotion ? 0 : -30 }} animate={{ scale: 1, rotate: 0 }}><Check size={40} /></motion.div><h4>这一餐，安排明白了。</h4><p>模拟结算金额 <strong>¥{formatMoney(order.total_fen)}</strong><br />未产生真实订单或扣款，请到门店实际确认。</p><div className="order-number">模拟单号：{order.order_id}</div><p>{order.notice}</p><button className="secondary-button" onClick={closeCheckout}>回到开饭工作台 <ArrowRight size={13} /></button></div>
          : selectedPlan && <><motion.h4 className="order-plan-heading" {...sharedPlanElement(planTransitionId(selectedPlan.plan_id, 'title'), reducedMotion)} transition={{ layout: planSharedTransition }}>{planTitles[selectedPlan.type]}</motion.h4><p className="order-store"><MapPin size={13} />{selectedStore?.name} · 到店自取</p><div className="order-items">{selectedPlan.items.map((item, index) => <div className="order-item" key={`${item.code}-${index}`}><FoodImage item={item} transitionId={planTransitionId(selectedPlan.plan_id, `food-${index}`)} /><div className="food-info"><strong>{item.name}</strong><p>× {item.quantity}{item.spec ? ` · ${item.spec}` : ''}</p></div><strong>¥{formatMoney(item.line_total_fen)}</strong></div>)}</div><div className="order-total"><span>本次模拟结算</span><motion.strong {...sharedPlanElement(planTransitionId(selectedPlan.plan_id, 'total'), reducedMotion)} transition={{ layout: planSharedTransition }}>¥{formatMoney(selectedPlan.total_fen)}</motion.strong></div><div className="order-notice"><Info size={14} /><span>演示，不会扣款。当前操作不会向门店下单，金额与商品仍需在实际点餐时确认。</span></div>{orderError && <div className="inline-error" role="alert">{orderError}</div>}<motion.button className="primary-button full-width" aria-label="确认模拟结算" onClick={checkout} disabled={orderBusy} {...buttonMotion(reducedMotion, orderBusy)}>{orderBusy ? <LoaderCircle size={16} className="spinner" /> : <ReceiptText size={16} />}{orderBusy ? '正在确认…' : '确认模拟结算'}<ArrowRight size={15} /></motion.button></>}
      </Modal>}

      {helpOpen && <Modal key="help" title="小厨房，怎么玩？" onClose={() => setHelpOpen(false)}><div className="help-steps"><div className="help-step"><span>1</span><div><strong>先选门店，再写需求</strong><p>说清人数、预算口径和不辣人数。聊天识别后，可以直接在小纸条里修改。</p></div></div><div className="help-step"><span>2</span><div><strong>看清明细，选喜欢的</strong><p>方案有几份就展示几份；无解会说明原因。辣度未知不会被当成不辣。</p></div></div><div className="help-step"><span>3</span><div><strong>模拟结算，心里有数</strong><p>确认方案金额与明细，这里不产生真实订单，也不会扣款。</p></div></div></div><div className="help-note"><strong>关于首版能力</strong><br />离线模式是「6 人 / 总预算 150 元 / 2 人不辣」的固定历史样例；将预算改为 30 元可体验无解。其他条件需连接后端。无饮料与饭量/口味编辑仍待前端接入；开卡比较等待可核实的报价与资格数据。</div><p className="store-disclaimer"><BookOpen size={11} /> 动效采用 Motion for React 的弹簧过渡，并尊重系统减少动态效果设置。</p></Modal>}
    </AnimatePresence>
    <AnimatePresence>{toast && <motion.div className="toast" role="status" key={toast} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}>{toast}</motion.div>}</AnimatePresence>
    </LayoutGroup>
  </MotionConfig>
}
