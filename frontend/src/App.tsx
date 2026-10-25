import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { AnimatePresence, LayoutGroup, MotionConfig, motion, usePresence } from 'motion/react'
import { ArrowDown, ArrowRight, ArrowUpRight, BadgeCheck, BookOpen, ChefHat, Check, ChevronDown, CircleHelp, ClipboardList, CookingPot, Flame, Heart, Info, Leaf, LoaderCircle, LockKeyhole, MapPin, Minus, Monitor, Plus, ReceiptText, RotateCcw, Search, Send, ShieldCheck, Sparkles, Store as StoreIcon, Users, Utensils, Wallet, Wifi, X } from 'lucide-react'
import PlanCards, { FoodImage } from './components/PlanCards'
import HeroScene from './components/HeroScene'
import KitchenFeedback from './components/KitchenFeedback'
import OpeningCopy from './components/OpeningCopy'
import SettingsDialog from './components/SettingsDialog'
import { Settings2 } from 'lucide-react'
import { ApiError, createOrder, formatMoney, getOrder, getPlans, getStores, officialPaymentUrl, parseChat } from './lib/api'
import type { ChatResponse, OrderResponse, Plan, PlanResponse, Requirements, RequirementsDraft, Store, StoreListResponse } from './lib/types'
import { buttonMotion, dialogMotion, gameSpring } from './lib/motion-presets'
import { planSharedTransition, planTitles, planTransitionId, sharedPlanElement } from './lib/plan-transition'
import { useLiveReducedMotion } from './lib/use-live-reduced-motion'
import { newIdempotencyKey } from './lib/idempotency-key'

type PaymentIntent = { order_id: string; plan_id: string; store_code: string; total_fen: number }
type PaymentApproval = PaymentIntent & { payment_url: string }
const orderStatuses = ['pending_payment','paid','preparing','delivering','completed','cancelled','unknown']
function samePaymentFacts(value: OrderResponse | null, intent: PaymentIntent | null): boolean {
  return Boolean(value && intent && value.order_id === intent.order_id && value.plan_id === intent.plan_id
    && value.store_code === intent.store_code && value.total_fen === intent.total_fen)
}
function verifiedPaymentUrl(value: OrderResponse | null, approval: PaymentApproval | null): string | null {
  if (!value || !approval || value.status !== 'pending_payment' || !samePaymentFacts(value, approval)) return null
  const url = officialPaymentUrl(value.payment_url)
  if (!url || url !== approval.payment_url || (value.expires_at && (!Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= Date.now()))) return null
  return url
}

// A GET may supply a reviewable order, never an implicit payment authorization.
function reviewableOrder(value: OrderResponse | null): OrderResponse | null {
  if (!value || !value.order_id || !value.plan_id || !value.store_code
    || !Number.isInteger(value.total_fen) || value.total_fen < 0 || !Array.isArray(value.items)) return null
  const payment_url = officialPaymentUrl(value.payment_url)
  if (!payment_url || !verifiedPaymentUrl(value, { ...value, payment_url })) return null
  return { ...value, payment_url }
}

function sameReviewedOrder(expected: OrderResponse, value: OrderResponse): boolean {
  return value.order_id === expected.order_id && value.plan_id === expected.plan_id
    && value.store_code === expected.store_code && value.total_fen === expected.total_fen
    && value.status === expected.status && value.payment_url === expected.payment_url
    && (value.expires_at ?? null) === (expected.expires_at ?? null)
    && JSON.stringify(value.items) === JSON.stringify(expected.items)
}

type Brief = { people: number; budget: string; budgetType: 'total' | 'per_person'; nonSpicy: number }
const defaultBrief: Brief = { people: 6, budget: '150', budgetType: 'total', nonSpicy: 2 }
const spring = gameSpring

// 用餐方式：堂食 / 外卖 / 自提。官方报价返回的取餐方式代码随门店与渠道变化，
// 这里只把期望方式映射到官方选项，最终取餐方式仍以官方算价返回的 takeWay 为准。
type DiningMode = 'dine_in' | 'delivery' | 'pickup'
const diningModes: { key: DiningMode; label: string }[] = [
  { key: 'dine_in', label: '堂食' },
  { key: 'delivery', label: '外卖' },
  { key: 'pickup', label: '自提' },
]
const diningModeHints: Record<DiningMode, string> = {
  dine_in: '店内用餐 · 取餐方式以官方报价为准',
  delivery: '配送到家 · 配送地址在官方页面确认',
  pickup: '到店自取 · 取餐方式以官方报价为准',
}
const takeWayMatchers: Record<DiningMode, RegExp> = {
  dine_in: /eat|dine|堂食|店内/i,
  delivery: /deliver|外送|外卖|麦乐送|配送/i,
  pickup: /take|pickup|外带|自提|取餐|带走/i,
}
function diningModeLabel(mode: DiningMode): string {
  return diningModes.find(item => item.key === mode)?.label ?? '堂食'
}
function preferredTakeWay(options: { code: string; title: string; subtitle?: string }[], mode: DiningMode) {
  return options.find(option => takeWayMatchers[mode].test(`${option.code} ${option.title} ${option.subtitle ?? ''}`))
}
const DINING_MODE_KEY = 'kitchen-dining-mode'
function readDiningMode(): DiningMode {
  try {
    const saved = localStorage.getItem(DINING_MODE_KEY)
    if (saved === 'dine_in' || saved === 'delivery' || saved === 'pickup') return saved
  } catch { /* no storage */ }
  return 'dine_in'
}


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

export default function App({ navigate = (url: string) => window.location.assign(url) }: { navigate?: (url: string) => void } = {}) {
  const reducedMotion = useLiveReducedMotion()
  const planTransitionScope = useId()
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
  const [parsedRequirements, setParsedRequirements] = useState<Requirements | null>(null)
  const [chatBusy, setChatBusy] = useState(false)
  const [plansBusy, setPlansBusy] = useState(false)
  const [planError, setPlanError] = useState('')
  const [response, setResponse] = useState<PlanResponse | null>(null)
  const [selectedPlan, setSelectedPlan] = useState<Plan | null>(null)
  const [checkoutSession, setCheckoutSession] = useState(0)
  const [order, setOrder] = useState<OrderResponse | null>(null)
  const [lastOrder, setLastOrder] = useState<OrderResponse | null>(() => { try { return JSON.parse(sessionStorage.getItem('kitchen-order') || 'null') } catch { return null } })
  const [paymentApproval, setPaymentApproval] = useState<PaymentApproval | null>(() => { try { return JSON.parse(sessionStorage.getItem('kitchen-payment-approval') || 'null') } catch { return null } })
  const [paymentIntent, setPaymentIntent] = useState<PaymentIntent | null>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem('kitchen-payment-intent') || 'null') as PaymentIntent | null
      if (!value || ![value.order_id, value.plan_id, value.store_code].every(item => typeof item === 'string' && item.trim())
        || !Number.isSafeInteger(value.total_fen) || value.total_fen < 0) return null
      return { order_id: value.order_id, plan_id: value.plan_id, store_code: value.store_code, total_fen: value.total_fen }
    } catch { return null }
  })
  const currentOrder = useRef(lastOrder)
  const currentApproval = useRef(paymentApproval)
  const currentIntent = useRef(paymentIntent)
  const orderGeneration = useRef(0)
  const statusQuery = useRef(0)
  const [submissionUnknown, setSubmissionUnknown] = useState(() => sessionStorage.getItem('kitchen-submission') === 'unknown')
  const unknownSubmission = useRef(submissionUnknown)
  const [takeWayCode, setTakeWayCode] = useState('')
  const [diningMode, setDiningModeState] = useState<DiningMode>(readDiningMode)
  const orderLock = useRef(false)
  const orderBlocked = useRef(submissionUnknown || lastOrder?.status === 'pending_payment' || lastOrder?.status === 'unknown')
  const orderKeys = useRef(new Map<string, string>())
  const [statusError, setStatusError] = useState('')
  const [recoverySnapshot, setRecoverySnapshot] = useState<OrderResponse | null>(null)
  const [recoveryReview, setRecoveryReview] = useState<OrderResponse | null>(null)
  const recoveryReviewRef = useRef<OrderResponse | null>(null)
  const [recoveryBusy, setRecoveryBusy] = useState(false)
  const [recoveryError, setRecoveryError] = useState('')
  const [orderBusy, setOrderBusy] = useState(false)
  const [error, setError] = useState('')
  const [orderError, setOrderError] = useState('')
  const [helpOpen, setHelpOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSession, setSettingsSession] = useState(0)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const settingsSaveLock = useRef(false)
  const [toast, setToast] = useState('')
  const requestId = useRef(0)
  const orderRequestId = useRef(0)
  const storeRequestId = useRef(0)
  // Exiting Motion children may retain a handler after invalidation and unlock.
  // A render's quote epoch prevents that old handler from reviving its old quote.
  const renderedQuoteEpoch = requestId.current
  const taskRef = useRef<HTMLElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)
  const baseRequirements = readBrief(brief)
  const requirements = baseRequirements ? { ...baseRequirements, ...parsedRequirements, people: baseRequirements.people, budget_type: baseRequirements.budget_type, budget_fen: baseRequirements.budget_fen, dietary_constraints: baseRequirements.dietary_constraints } : null

  // Every changed input invalidates the selection and rejects stale async responses.
  const invalidate = useCallback(() => {
    requestId.current += 1
    orderRequestId.current += 1
    setResponse(null); setSelectedPlan(null); setOrder(null); setPlansBusy(false)
    setChatBusy(false); setError(''); setPlanError(''); setOrderError(''); setUnresolvedChat(false)
  }, [])

  // 用餐方式只影响取餐方式的匹配，不需要丢弃已生成的方案；记住选择即可。
  const setDiningMode = useCallback((mode: DiningMode) => {
    setDiningModeState(mode)
    setOrderError('')
    try { localStorage.setItem(DINING_MODE_KEY, mode) } catch { /* no storage */ }
  }, [])


  const loadStores = useCallback(async (searchCity: string, searchKeyword: string, pickDefault = false) => {
    const id = ++storeRequestId.current
    setStoreLoading(true); setStoreError('')
    try {
      const result = await getStores(searchCity, searchKeyword)
      if (id !== storeRequestId.current) return
      setStoreData(result)
      if (pickDefault) {
        setSelectedStore(result.stores.find((store) => store.store_code === '1400011') ?? result.stores[0] ?? null)
      }
    } catch (cause) {
      if (id === storeRequestId.current) { setStoreError(cause instanceof Error ? cause.message : '门店读取失败，请重试。'); setStoreData(null) }
    } finally { if (id === storeRequestId.current) setStoreLoading(false) }
  }, [invalidate])

  const startSettingsSave = useCallback(() => {
    // Synchronous refs arbitrate against checkout even before React commits busy UI.
    if (settingsSaveLock.current || orderLock.current) return false
    settingsSaveLock.current = true
    setSettingsSaving(true)
    invalidate()
    return true
  }, [invalidate])

  const settleSettingsSave = useCallback(() => {
    settingsSaveLock.current = false
    setSettingsSaving(false)
  }, [])

  const settingsSaved = useCallback(() => {
    invalidate()
    setChat(null); setChatDraft(null); setSelectedStore(null)
    void loadStores(city, keyword, true)
  }, [invalidate, loadStores, city, keyword])

  useEffect(() => {
    void loadStores('广州', '', true)
    return () => { storeRequestId.current += 1 }
  }, [loadStores])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3500)
    return () => window.clearTimeout(timer)
  }, [toast])

  function updateBrief(patch: Partial<Brief>) {
    invalidate(); setChat(null); setChatDraft(null); setBrief((old) => ({ ...old, ...patch }))
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault()
    if (!message.trim() || chatBusy || settingsSaveLock.current) return
    invalidate()
    const id = requestId.current
    setChatBusy(true)
    try {
      const result = await parseChat(message.trim(), chatDraft ?? requirements)
      if (id !== requestId.current) return
      setChatDraft(result.draft ?? null)
      setChat(result); setUnresolvedChat(!result.ready)
      if (result.requirements) {
        setParsedRequirements(result.requirements)
        setBrief(fromRequirements(result.requirements))
      } else if (result.draft) {
        const draft = result.draft
        const fields = Object.fromEntries(Object.entries(draft).filter(([key,value]) => value != null && !['pending_fields','notes'].includes(key)))
        setParsedRequirements(old => ({ ...requirements, ...old, ...fields }) as Requirements)
        setBrief(old => ({ ...old,
          ...(typeof draft.people === 'number' ? { people:draft.people } : {}),
          ...(typeof draft.budget_fen === 'number' ? { budget:String(draft.budget_fen / 100) } : {}),
          ...(draft.budget_type ? {budgetType:draft.budget_type} : {}),
          ...(draft.dietary_constraints ? {nonSpicy:draft.dietary_constraints.reduce((sum,item)=>sum+item.people,0)} : {}),
        }))
      }
      setMessage('')
    } catch (cause) { if (id === requestId.current) setError(cause instanceof Error ? cause.message : '需求解析失败，请重试。') }
    finally { if (id === requestId.current) setChatBusy(false) }
  }

  async function generatePlans(event?: FormEvent) {
    event?.preventDefault()
    if (!requirements || !selectedStore || unresolvedChat || plansBusy || chatBusy || storeLoading || settingsSaveLock.current) return
    invalidate()
    const id = requestId.current
    setPlansBusy(true)
    try {
      const result = await getPlans(selectedStore.store_code, requirements)
      if (id !== requestId.current) return
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

  function rememberPaymentIntent(value: PaymentIntent | null) {
    currentIntent.current = value
    setPaymentIntent(value)
    try {
      if (value) sessionStorage.setItem('kitchen-payment-intent', JSON.stringify(value))
      else sessionStorage.removeItem('kitchen-payment-intent')
    } catch { /* A matched checkout remains recoverable on this page without storage. */ }
  }

  function rememberOrder(value: OrderResponse, approval: PaymentApproval | null, clearSubmission = false) {
    currentOrder.current = value
    currentApproval.current = approval
    orderBlocked.current = unknownSubmission.current || value.status === 'pending_payment' || value.status === 'unknown'
    setLastOrder(value); setPaymentApproval(approval)
    try {
      sessionStorage.setItem('kitchen-order', JSON.stringify(value))
      if (approval) sessionStorage.setItem('kitchen-payment-approval', JSON.stringify(approval))
      else sessionStorage.removeItem('kitchen-payment-approval')
      // Only this create's confirmed outcome may clear its own submission marker.
      if (clearSubmission) sessionStorage.removeItem('kitchen-submission')
    } catch { /* Current-page reconciliation remains available without storage. */ }
  }

  async function refreshOrder() {
    const expected = currentOrder.current
    if (!expected || orderLock.current || unknownSubmission.current) return
    const generation = orderGeneration.current
    const query = ++statusQuery.current
    const isCurrent = () => generation === orderGeneration.current && query === statusQuery.current
      && currentOrder.current?.order_id === expected.order_id && !orderLock.current && !unknownSubmission.current
    setStatusError('')
    setRecoverySnapshot(null)
    try {
      const value = await getOrder(expected.order_id)
      if (!isCurrent()) return
      // Only a live GET for this known order can offer an explicit review.
      if (value.order_id === expected.order_id && orderStatuses.includes(value.status)) setRecoverySnapshot(reviewableOrder(value))
      if (value.order_id !== expected.order_id || value.plan_id !== expected.plan_id
        || value.store_code !== expected.store_code || value.total_fen !== expected.total_fen
        || !orderStatuses.includes(value.status)) {
        rememberPaymentIntent(null)
        rememberOrder({ ...expected, payment_url:null }, null)
        throw new Error('订单状态响应与当前订单不一致，请在官方订单中核实。')
      }
      if (currentIntent.current && !samePaymentFacts(value, currentIntent.current)) rememberPaymentIntent(null)
      // A refreshed status is not a new approval. Unknown submissions use the
      // existing explicit review flow before the user follows a payment link.
      const approval = verifiedPaymentUrl(value, currentApproval.current) ? currentApproval.current : null
      if (!['pending_payment', 'unknown'].includes(value.status)) rememberPaymentIntent(null)
      rememberOrder(approval ? value : { ...value, payment_url:null }, approval)
      if (value.status === 'unknown') setStatusError('订单当前状态待核实，请在官方订单中核对，勿重复下单。')
      else if (value.status === 'pending_payment') {
        if (reviewableOrder(value)) setOrderError('')
        if (approval) { setOrderError(''); setStatusError('订单待支付，核对后可继续前往官方支付。') }
        else setStatusError(currentIntent.current ? '订单待支付，但支付链接缺失、未通过校验或已过期，请在官方订单中核对。' : '订单待支付，但本页缺少此前确认的付款记录，请到官方订单中核对后付款。')
      } else { setOrderError(''); setStatusError(`订单当前状态：${statusLabels[value.status]}。`) }
    } catch (cause) { if (isCurrent()) setStatusError(cause instanceof Error ? cause.message : '订单状态暂未核实，请稍后查询。') }
  }

  function closeRecoveryReview() {
    recoveryReviewRef.current = null
    setRecoveryReview(null)
  }

  function openRecoveryReview() {
    if (settingsSaveLock.current || orderLock.current || unknownSubmission.current
      || recoverySnapshot?.order_id !== currentOrder.current?.order_id) return
    const snapshot = reviewableOrder(recoverySnapshot)
    if (!snapshot) { setStatusError('支付链接未通过校验或已过期，请查询订单状态。'); return }
    recoveryReviewRef.current = snapshot
    setRecoveryReview(snapshot); setRecoveryError('')
  }

  async function confirmRecovery() {
    const snapshot = recoveryReviewRef.current
    if (!snapshot || settingsSaveLock.current || orderLock.current || unknownSubmission.current
      || snapshot.order_id !== currentOrder.current?.order_id) return
    if (!reviewableOrder(snapshot)) { setRecoveryError('支付链接已过期，请关闭并重新查询订单状态。'); return }
    // Reuse the synchronous transaction lock: settings and stale status requests
    // cannot race this explicit approval, and a double click sends only one GET.
    orderLock.current = true
    const generation = ++orderGeneration.current
    statusQuery.current += 1
    const isCurrent = () => generation === orderGeneration.current && recoveryReviewRef.current === snapshot
      && currentOrder.current?.order_id === snapshot.order_id && !unknownSubmission.current
    setRecoveryBusy(true); setRecoveryError('')
    try {
      const value = await getOrder(snapshot.order_id)
      if (!isCurrent()) return
      if (value.order_id !== snapshot.order_id || !orderStatuses.includes(value.status)) {
        setRecoverySnapshot(null)
        setRecoveryError('订单状态响应与当前订单不一致，请关闭并重新核对官方订单。')
        return
      }
      const verified = reviewableOrder(value)
      // A terminal status is authoritative, not a reason to start another order.
      if (!verified) {
        rememberOrder({ ...value, payment_url: null }, null)
        setRecoverySnapshot(null); closeRecoveryReview()
        setStatusError(value.status === 'paid' ? '官方订单已支付。' : '订单状态或支付链接已变化，请重新核对官方订单。')
        return
      }
      if (!sameReviewedOrder(snapshot, verified)) {
        rememberOrder({ ...verified, payment_url: null }, null)
        setRecoverySnapshot(verified)
        recoveryReviewRef.current = verified
        setRecoveryReview(verified)
        setRecoveryError('官方订单信息已变化，请重新核对后确认。')
        return
      }
      const approval: PaymentApproval = { order_id: verified.order_id, plan_id: verified.plan_id,
        store_code: verified.store_code, total_fen: verified.total_fen, payment_url: verified.payment_url! }
      rememberOrder(verified, approval)
      setRecoverySnapshot(null); closeRecoveryReview(); setStatusError('')
      try { navigate(approval.payment_url) } catch { setStatusError('跳转未完成，请使用下方同一支付链接继续。') }
    } catch (cause) {
      if (isCurrent()) setRecoveryError(cause instanceof Error ? cause.message : '订单状态暂未核实，请重新查询。')
    } finally { orderLock.current = false; setRecoveryBusy(false) }
  }
  useEffect(() => {
    if (!lastOrder) return
    const onReturn = () => { void refreshOrder() }
    window.addEventListener('focus', onReturn); window.addEventListener('pageshow', onReturn)
    return () => { window.removeEventListener('focus', onReturn); window.removeEventListener('pageshow', onReturn) }
  }, [lastOrder?.order_id])
  useEffect(() => { if (lastOrder) void refreshOrder() }, [])

  async function checkout(plan: Plan, takeWay: string) {
    if (renderedQuoteEpoch !== requestId.current || settingsSaveLock.current || orderLock.current || orderBlocked.current || submissionUnknown || lastOrder?.status === 'pending_payment' || !plan.quote_id || !plan.take_way_options?.some(option => option.code === takeWay)) return
    // Prepare identity before acquiring the submit lock. No request has been sent
    // if local preparation fails, so neither an unknown marker nor a lock is valid.
    const fingerprint = `${plan.plan_id}:${plan.quote_id}:${takeWay}`
    let key: string
    try {
      key = orderKeys.current.get(fingerprint) ?? newIdempotencyKey()
      orderKeys.current.set(fingerprint, key)
    } catch {
      setOrderError('下单准备失败，请再试一次。')
      return
    }
    orderLock.current = true
    const generation = ++orderGeneration.current
    statusQuery.current += 1
    const expectedStore = selectedStore?.store_code
    currentApproval.current = null; setPaymentApproval(null)
    rememberPaymentIntent(null)
    try { sessionStorage.removeItem('kitchen-payment-approval') } catch { /* no storage */ }
    const id = requestId.current
    const orderId = ++orderRequestId.current
    setOrderBusy(true); setOrderError(''); setStatusError(''); setSelectedPlan(null)
    try {
      // Persist before dispatch: a tab return/reload must not create a second order.
      try { sessionStorage.setItem('kitchen-submission', 'unknown') } catch { /* no storage */ }
      const result = await createOrder({ plan_id: plan.plan_id, quote_id: plan.quote_id, take_way_code: takeWay, idempotency_key: key })
      if (typeof result.order_id !== 'string' || !result.order_id.trim() || !orderStatuses.includes(result.status)) throw new ApiError('订单提交结果待核实，请勿重复下单。', 'E_ORDER_SUBMISSION_UNKNOWN')
      // A known order is retained for reconciliation, but no link is authorized until every quote fact matches.
      rememberOrder({ ...result, payment_url:null }, null)
      if (result.plan_id !== plan.plan_id || result.store_code !== expectedStore || result.total_fen !== plan.total_fen) {
        throw new ApiError('订单信息与所选方案不一致，已保留订单号待核实，请勿重复下单。', 'E_ORDER_RESULT_UNVERIFIED')
      }
      if (['pending_payment', 'unknown'].includes(result.status)) {
        rememberPaymentIntent({ order_id: result.order_id, plan_id: plan.plan_id, store_code: expectedStore!, total_fen: plan.total_fen })
      }
      if (result.status !== 'pending_payment') {
        rememberOrder({ ...result, payment_url:null }, null, true)
        if (result.status === 'unknown') setOrderError('订单已创建，当前状态待核实，请查询订单状态；请勿重复下单。')
        else setStatusError(`订单已创建，当前状态：${statusLabels[result.status]}。`)
        return
      }
      const candidate = { order_id:result.order_id, plan_id:plan.plan_id, store_code:expectedStore!, total_fen:plan.total_fen, payment_url:officialPaymentUrl(result.payment_url) ?? '' }
      const url = verifiedPaymentUrl(result, candidate)
      if (!url) {
        rememberOrder({ ...result, payment_url:null }, null, true)
        throw new ApiError('支付链接未通过校验或已过期，请查询订单状态。', 'E_PAYMENT_URL')
      }
      rememberOrder(result, candidate, true)
      if (id !== requestId.current || orderId !== orderRequestId.current || generation !== orderGeneration.current) return
      try { navigate(url) } catch { setOrderError('跳转未完成，请使用下方同一支付链接继续。') }
    } catch (cause) {
      const uncertain = !(cause instanceof ApiError) || ['E_ORDER_SUBMISSION_UNKNOWN','E_ORDER_RESULT_UNVERIFIED'].includes(cause.code)
      if (uncertain) {
        orderBlocked.current = true
        unknownSubmission.current = true
        setSubmissionUnknown(true)
        try { sessionStorage.setItem('kitchen-submission', 'unknown') } catch { /* no storage */ }
        setOrderError('订单提交结果待核实，请先在官方订单中核对，勿重复下单。')
      } else {
        try { sessionStorage.removeItem('kitchen-submission') } catch { /* no storage */ }
        if (id !== requestId.current || orderId !== orderRequestId.current) return
        if (cause.code === 'E_PRICE_CHANGED' && cause.data?.updated_plan) {
          const updated = cause.data.updated_plan as Plan
          if (updated.plan_id !== plan.plan_id || !updated.quote_id) { setOrderError('更新报价格式异常，请重新配餐。'); return }
          setResponse(old => old ? { ...old, plans: old.plans.map(item => item.plan_id === plan.plan_id ? updated : item) } : old)
          setOrderError('价格已更新，请核对新报价后再次点击“就选这份”。')
        } else setOrderError(cause.message)
      }
    } finally { orderLock.current = false; setOrderBusy(false) }
  }

  const closeCheckout = () => { orderRequestId.current += 1; setSelectedPlan(null); setOrder(null) }
  const choosePlan = (plan: Plan) => {
    if (renderedQuoteEpoch !== requestId.current || settingsSaveLock.current || orderLock.current || orderBlocked.current || submissionUnknown || lastOrder?.status === 'pending_payment') return
    const options = plan.take_way_options ?? []
    if (!plan.quote_id || !options.length) { setOrderError('方案缺少有效报价或取餐方式，请重新配餐。'); return }
    if (options.length === 1) { void checkout(plan, options[0].code); return }
    // 按用餐方式预选官方取餐方式；官方没有对应渠道时仍列出官方选项，不伪造报价。
    const preferred = preferredTakeWay(options, diningMode)
    setCheckoutSession(session => session + 1); setTakeWayCode(preferred?.code ?? ''); setSelectedPlan(plan); setOrder(null)
    setOrderError(preferred ? '' : diningMode === 'delivery'
      ? '当前门店的官方报价未提供外卖（麦乐送）取餐方式，请改用到店取餐或自提。'
      : '官方报价未返回与所选用餐方式一致的选项，请以下方取餐方式为准。')
  }
  const scrollToTask = () => { taskRef.current?.scrollIntoView({ behavior: 'smooth' }); window.setTimeout(() => document.getElementById('meal-message')?.focus({ preventScroll: true }), 400) }
  const sourceText = response?.data_source === 'live_mcp' ? '在线菜单' : '数据来源待确认'
  const algorithmLabel = response?.algorithm_source === 'llm_skill' ? ' · 模型搭配' : ''
  const pendingOrder = lastOrder?.status === 'pending_payment'
  const unresolvedOrder = pendingOrder || lastOrder?.status === 'unknown'
  const recoveryUrl = !submissionUnknown ? verifiedPaymentUrl(lastOrder, paymentApproval) : null
  const statusLabels = { pending_payment: '待支付', paid: '已支付', preparing: '配餐中', delivering: '配送中', completed: '已完成', cancelled: '已取消', unknown: '状态待核实' }

  return <MotionConfig transition={spring} reducedMotion={reducedMotion ? 'always' : 'never'}>
    <LayoutGroup id={planTransitionScope}>
    <div className="app-shell" data-reduced-motion={reducedMotion}>
      <header className="site-header"><div className="container header-inner">
        <a className="brand" href="#" aria-label="麦门省钱助手首页"><span className="brand-mark"><ChefHat size={32} strokeWidth={2.3} /></span><span><span className="brand-name">麦门省钱助手</span><small>MAIMEN KITCHEN</small></span></a>
        <nav className="main-nav" aria-label="主导航"><button className="nav-link active" onClick={scrollToTask}>开饭工作台</button><button className="nav-link" onClick={() => { if (lastOrder) { closeCheckout(); setOrder(lastOrder) } else setToast('还没有订单，先选一份方案吧。') }}>我的订单</button><button className="nav-link" onClick={() => setHelpOpen(true)}>使用说明 <ArrowUpRight size={11} /></button></nav>
        <div className="header-tools"><motion.button className="settings-trigger" type="button" disabled={orderBusy} onClick={() => { setSettingsSession(session => session + 1); setSettingsOpen(true) }} {...buttonMotion(reducedMotion, orderBusy)}><Settings2 size={16} aria-hidden="true" />设置</motion.button><span className="online-badge"><Wifi size={16} />在线小厨房</span></div>
      </div></header>

      <main>
        <section className="hero"><div className="container hero-inner">
          <OpeningCopy reducedMotion={reducedMotion}>
            <div className="eyebrow"><Sparkles size={15} /> 按人数和预算，搭配这一餐</div>
            <h1>今天，<span className="red-word">开饭！</span></h1>
            <p className="hero-description">选一家店，填好人数、预算和忌口。<br />看看有哪些搭配，再到官方页面付款。</p>
            <div className="hero-actions"><motion.button className="primary-button" {...buttonMotion(reducedMotion)} onClick={scrollToTask}>开始配餐 <ArrowDown size={16} /></motion.button><span className="small-note">下单前会再次核价。</span></div>
            <div className="hero-promises"><span><BadgeCheck size={13} />按预算选餐</span><span><Users size={13} />列清商品份数</span><span><Heart size={13} />说明忌口</span></div>
          </OpeningCopy>
          <HeroScene paused={storeOpen || Boolean(selectedPlan) || Boolean(order) || helpOpen || settingsOpen} />
        </div></section>

        <div className="kitchen-strip"><div className="container steps" aria-label="点餐步骤"><div className="step active"><span className="step-number">1</span>填写点餐需求<small>说说你的需求</small></div><span className="step-line" /><div className={`step ${response?.plans.length ? 'active' : ''}`}><span className="step-number">2</span>选择配餐方案<small>明明白白选餐</small></div><span className="step-line" /><div className={`step ${order ? 'active' : ''}`}><span className="step-number">3</span>确认订单并付款<small>前往官方支付</small></div></div></div>

        <section className="workbench" ref={taskRef}><div className="container">
          <div className="section-heading"><div><h2><CookingPot size={25} />这顿怎么点</h2><p>选好门店，再填人数和预算</p></div><span className="tiny-label"><span className="status-dot" />门店菜单 · 按需配餐</span></div>
          <div className="workbench-grid">
            <section className="brief-panel" aria-label="点餐需求">
              <div className="panel-heading"><h3><ClipboardList size={19} />点餐小纸条</h3><span className="ticket-id">ORDER / 001</span></div>
              <div className="brief-body">
                <button className="store-button" onClick={() => { setStoreOpen(true); setKeyword(''); setCity('广州'); void loadStores('广州', '') }} aria-label={selectedStore ? `${selectedStore.name}，切换门店` : '选择用餐门店'}><MapPin size={20} /><span className="store-copy"><strong>{selectedStore?.name.replace('麦当劳', '') || (storeLoading ? '正在查看菜单…' : '选择用餐门店')}</strong><small>{selectedStore ? `${diningModeLabel(diningMode)} · ${selectedStore.business_status === true ? '营业中' : selectedStore.business_status === false ? '已休息' : '营业状态未知'}` : '按城市与关键词查找门店'}</small></span><ChevronDown size={15} /></button>
                {storeError && <div className="inline-error" role="alert">{storeError}<button onClick={() => void loadStores('广州', '', true)}>重试</button></div>}
                <label className="chat-label" htmlFor="meal-message"><ChefHat size={16} />这顿想怎么吃<span>人数、预算、口味都可以说</span></label>
                <form className="chat-composer" onSubmit={sendMessage}><textarea id="meal-message" rows={2} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="比如：6个人，总预算150元，2人不吃辣" /><div className="composer-footer"><span>或者，直接编辑下方的小纸条 ↓</span><motion.button type="submit" className="send-button" aria-label="发送点餐需求" disabled={!message.trim() || chatBusy || settingsSaving} {...buttonMotion(reducedMotion, !message.trim() || chatBusy || settingsSaving)}>{chatBusy ? <LoaderCircle size={15} className="spinner" /> : <Send size={15} />}</motion.button></div></form>

                {chat && <div className="assistant-reply" role="status"><ChefHat size={15} /><span>{chat.follow_up || '已更新人数和预算。'}<br /><small>{chat.parse_source === 'llm' ? '需求已识别' : '需求已记录'}{chat.notes.length ? ` · ${chat.notes.join(' ')}` : ''}</small></span></div>}
                <div className="form-divider">确认一下，这次怎么吃</div>
                <form onSubmit={generatePlans}>
                  <div className="field-grid"><div className="field"><span className="field-label"><Users size={13} />几位一起吃</span><Stepper label="用餐人数" prefix="用餐人数" value={brief.people} min={1} max={20} onChange={(people) => updateBrief({ people })} /></div>
                    <div className="field"><label htmlFor="meal-budget"><Wallet size={13} />预算多少</label><div className="budget-input"><span>¥</span><input id="meal-budget" type="number" min="0" step="0.01" aria-label="预算金额" value={brief.budget} onChange={(event) => updateBrief({ budget: event.target.value })} /></div><div className="budget-type"><button type="button" className={brief.budgetType === 'total' ? 'active' : ''} aria-pressed={brief.budgetType === 'total'} onClick={() => updateBrief({ budgetType: 'total' })}>总预算</button><button type="button" className={brief.budgetType === 'per_person' ? 'active' : ''} aria-pressed={brief.budgetType === 'per_person'} onClick={() => updateBrief({ budgetType: 'per_person' })}>每人预算</button></div></div>
                    <div className="field"><span className="field-label"><Flame size={13} />几位不吃辣</span><Stepper label="不吃辣人数" prefix="不吃辣人数" value={brief.nonSpicy} min={0} max={20} onChange={(nonSpicy) => updateBrief({ nonSpicy })} /><div className="sub-field">辣度按餐品名称判断</div></div>
                    <div className="field"><span className="field-label"><Utensils size={13} />用餐方式</span><div className="dining-mode" role="group" aria-label="用餐方式">{diningModes.map(mode => <button key={mode.key} type="button" className={diningMode === mode.key ? 'active' : ''} aria-pressed={diningMode === mode.key} onClick={() => setDiningMode(mode.key)}>{mode.label}</button>)}</div><div className="sub-field">{diningModeHints[diningMode]}</div></div></div>
                  <div className="preference-controls">
                    <label><input type="checkbox" checked={Boolean(parsedRequirements?.exclude_beverage)} onChange={event => { invalidate(); setChatDraft(null); setParsedRequirements(old => ({...(old ?? requirements!), exclude_beverage: event.target.checked})) }} />不带饮料</label>
                    <label>饭量<select aria-label="饭量" value={parsedRequirements?.appetite ?? 'normal'} onChange={event => { invalidate(); setChatDraft(null); setParsedRequirements(old => ({...(old ?? requirements!), appetite:event.target.value as Requirements['appetite']})) }}><option value="light">小份</option><option value="normal">正常</option><option value="heavy">多一点</option></select></label>
                    {parsedRequirements?.preferred_tags?.length ? <p>口味：{parsedRequirements.preferred_tags.join('、')}</p> : null}
                  </div>
                  {!requirements && <div className="inline-error" role="alert">人数请填 1–20 人；预算保留最多两位小数；不辣人数应在 0 与总人数之间。</div>}
                  {error && <div className="inline-error" role="alert">{error}</div>}
                  <motion.button type="submit" className="primary-button generate-button" aria-label="看看怎么点" disabled={!requirements || !selectedStore || unresolvedChat || plansBusy || chatBusy || storeLoading || settingsSaving} {...buttonMotion(reducedMotion, !requirements || !selectedStore || unresolvedChat || plansBusy || chatBusy || storeLoading || settingsSaving)}>{plansBusy ? <LoaderCircle size={17} className="spinner" /> : <Sparkles size={17} />}{plansBusy ? '正在搭配…' : '看看怎么点'}{!plansBusy && <ArrowRight size={16} />}</motion.button>
                  <p className="form-footnote">选择方案后将创建待支付订单，前往官方页面付款。</p>
                </form>
              </div>
            </section>

            <section className="results-panel" ref={resultsRef} aria-label="配餐结果" aria-busy={plansBusy}>
              <div className="panel-heading results-heading"><h3><ReceiptText size={19} />小厨房出餐台</h3><small>{response ? `${response.plans.length} 份方案` : '配餐方案与官方报价'}{Boolean(response?.plans.length) && <span className="results-scroll-hint">上下滚动查看方案</span>}</small></div>
              <div className="payment-feedback" aria-live="polite">
                {orderBusy && <p role="status">正在前往支付…</p>}
                {orderError && !selectedPlan && <p className="inline-error" role="alert">{orderError}</p>}
                {submissionUnknown && !orderError && <p className="inline-error" role="alert">订单提交结果待核实，请先在官方订单中核对，勿重复下单。</p>}
                {lastOrder && <div className="order-recovery"><p>官方订单 {lastOrder.order_id} · {statusLabels[lastOrder.status]} · ¥{formatMoney(lastOrder.total_fen)}</p><button className="secondary-button" disabled={submissionUnknown || orderBusy || recoveryBusy} onClick={()=>void refreshOrder()}>查询订单状态</button>{!order && recoveryUrl && <a className="secondary-button" href={recoveryUrl}>继续前往官方支付</a>}{!submissionUnknown && !recoveryUrl && recoverySnapshot && <button className="secondary-button" disabled={settingsSaving || orderBusy || recoveryBusy} onClick={openRecoveryReview}>核对订单后支付</button>}{statusError && <p role="status">{statusError}</p>}</div>}
              </div>
              <motion.div className="results-content" role="region" aria-label="方案列表" tabIndex={0} inert={settingsSaving} aria-disabled={settingsSaving} layoutScroll>
              <AnimatePresence mode="wait" initial={false}>
                {plansBusy ? <KitchenFeedback key="loading" state="loading" />
                  : planError ? <KitchenFeedback key="error" state="error" message={planError} onRetry={() => { void generatePlans() }} retryDisabled={!requirements || !selectedStore || unresolvedChat || plansBusy || chatBusy || storeLoading || settingsSaving} />
                  : response ? <motion.div key="results" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                    {response.plans.length > 0 && <KitchenFeedback state="success" count={response.plans.length} />}
                    <div className="source-note"><Info size={13} /><span>{sourceText} · {response.fetched_at}{algorithmLabel}<br />总价来自官方报价，行价为菜单参考；下单前会再次核价。</span></div>
                    {response.plans.length ? <PlanCards plans={response.plans} people={response.requirements.people} nonSpicyPeople={response.requirements.dietary_constraints.reduce((sum, constraint) => sum + constraint.people, 0)} onSelect={choosePlan} disabled={settingsSaving || orderBusy || submissionUnknown || Boolean(unresolvedOrder)} /> : <div className="no-solution"><CookingPot size={47} strokeWidth={1.4} /><h4>暂时没有合适的搭配</h4><p>{response.unsatisfiable_reason || '暂时没有符合条件的方案，请调整需求后再试。'}</p></div>}
                    <p className="results-footnote">{response.suggestions.join(' ')}</p>
                  </motion.div>
                    : <motion.div key="empty" className="empty-kitchen" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}><span className="empty-stamp">等你<br />点餐</span><div className="plate"><ChefHat size={57} strokeWidth={1.5} /></div><h4>先填人数和预算</h4><p>选好门店，看看有哪些搭配。</p><div className="empty-options"><div className="empty-option"><Wallet size={19} /><span>人数</span></div><div className="empty-option"><Utensils size={19} /><span>预算</span></div><div className="empty-option"><Heart size={19} /><span>忌口</span></div></div><p className="empty-help">输入人数和预算，看看怎么点。</p></motion.div>}
              </AnimatePresence>
              </motion.div>
            </section>
          </div>

          <div className="bottom-feature-strip"><div className="bottom-feature"><span className="feature-icon"><ShieldCheck size={19} /></span><div><strong>价格明细</strong><p>查看总价、人均和预算剩余</p></div></div><div className="bottom-feature"><span className="feature-icon"><Leaf size={19} /></span><div><strong>营养信息</strong><p>查看热量与营养成分</p></div></div><div className="bottom-feature"><span className="feature-icon"><ReceiptText size={19} /></span><div><strong>官方支付</strong><p>官方页面付款，订单状态可查询。</p></div></div></div>
        </div></section>
      </main>
      <footer className="site-footer"><div className="container footer-inner"><div className="footer-brand"><ChefHat size={17} />麦门省钱助手</div><p>独立点餐辅助工具 · 非麦当劳官方产品 · 价格以门店实际结算为准</p><button className="footer-help" onClick={() => setHelpOpen(true)}><CircleHelp size={12} />使用说明</button><span>GOOD FOOD. GOOD MOOD.</span></div></footer>
    </div>

    <AnimatePresence>
      {recoveryReview && <Modal key="recovery-review" title="核对官方订单" onClose={closeRecoveryReview}>
        <p>官方订单 {recoveryReview.order_id}</p>
        <p className="order-store"><MapPin size={14} />{storeData?.stores.find(store => store.store_code === recoveryReview.store_code)?.name ?? '官方门店'} · 门店编号 {recoveryReview.store_code}</p>
        <div className="order-items">{recoveryReview.items.map((item, index) => <div className="order-item" key={`${item.code}-${index}`}><div className="food-info"><strong>{item.name}</strong><p>× {item.quantity}{item.spec ? ` · ${item.spec}` : ''}</p></div></div>)}</div>
        <div className="order-total"><span>官方订单金额</span><strong>¥{formatMoney(recoveryReview.total_fen)}</strong></div>
        <p>请核对门店、商品和金额。确认后将再次查询同一官方订单，再前往官方页面支付，不会创建新订单。</p>
        {recoveryError && <p className="inline-error" role="alert">{recoveryError}</p>}
        <motion.button className="primary-button full-width" onClick={()=>void confirmRecovery()} disabled={recoveryBusy || settingsSaving || submissionUnknown} {...buttonMotion(reducedMotion,recoveryBusy || settingsSaving || submissionUnknown)}>{recoveryBusy ? '正在核实订单…' : '确认订单，前往支付'}<ArrowRight size={16}/></motion.button>
      </Modal>}
      {settingsOpen && <Modal key={`settings-${settingsSession}`} title="设置" onClose={() => setSettingsOpen(false)}><SettingsDialog globalSaving={settingsSaving} onSaveStart={startSettingsSave} onSaved={settingsSaved} onSettled={settleSettingsSave} /></Modal>}
      {storeOpen && <Modal key="store" title="选择用餐门店" onClose={() => setStoreOpen(false)}><form className="store-search" onSubmit={(event) => { event.preventDefault(); void loadStores(city, keyword) }}><input aria-label="城市" value={city} onChange={(event) => setCity(event.target.value)} placeholder="城市" /><input aria-label="门店关键词" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜门店名、商圈或地址" /><button aria-label="搜索门店" disabled={storeLoading}>{storeLoading ? <LoaderCircle className="spinner" size={17} /> : <Search size={17} />}</button></form>{storeError && <div className="inline-error" role="alert">{storeError}</div>}<div className="store-results">{storeData?.stores.map((store) => <button className={`store-option ${store.store_code === selectedStore?.store_code ? 'selected' : ''}`} key={store.store_code} onClick={() => { if (store.store_code !== selectedStore?.store_code) { invalidate(); setChatDraft(null); setChat(null); setSelectedStore(store) }; setStoreOpen(false) }}><MapPin size={19} /><span><strong>{store.name.replace('麦当劳', '')}</strong><small>{store.address}<br />{store.business_status === true ? '营业中' : store.business_status === false ? '已休息' : '营业状态未知'} · {store.business_hours || '营业时间未知'}</small></span>{store.store_code === selectedStore?.store_code && <Check size={17} />}</button>)}</div>{!storeLoading && !storeError && !storeData?.stores.length && <p className="store-disclaimer">没有找到门店，换个关键词试试。</p>}<p className="store-disclaimer">在线门店数据 · {storeData?.fetched_at || '等待数据'}</p></Modal>}

      {selectedPlan && <Modal key={`checkout-${selectedPlan.plan_id}-${checkoutSession}`} title="选择取餐方式" onClose={closeCheckout} sharedPlanId={selectedPlan.plan_id}>
        <motion.h4 className="order-plan-heading" {...sharedPlanElement(planTransitionId(selectedPlan.plan_id, 'title'), reducedMotion)} transition={{ layout: planSharedTransition }}>{selectedPlan.type === 'recommended' || selectedPlan.type === 'alternative' ? selectedPlan.title : planTitles[selectedPlan.type]}</motion.h4>
        <p className="order-store"><MapPin size={14} />{selectedStore?.name} · {diningModeLabel(diningMode)}</p>
        <div className="order-items">{selectedPlan.items.map((item,index)=><div className="order-item" key={`${item.code}-${index}`}><FoodImage item={item} transitionId={planTransitionId(selectedPlan.plan_id, `food-${index}`)} /><div className="food-info"><strong>{item.name}</strong><p>× {item.quantity}{item.spec ? ` · ${item.spec}` : ''}</p></div><span>参考 ¥{formatMoney(item.line_total_fen)}</span></div>)}</div>
        <div className="order-total"><span>官方报价</span><motion.strong {...sharedPlanElement(planTransitionId(selectedPlan.plan_id,'total'),reducedMotion)} transition={{layout:planSharedTransition}}>¥{formatMoney(selectedPlan.total_fen)}</motion.strong></div>
        <label className="take-way-label">取餐方式（官方报价）<select aria-label="取餐方式" value={takeWayCode} onChange={event=>setTakeWayCode(event.target.value)}><option value="">请选择</option>{selectedPlan.take_way_options?.map(option=><option key={option.code} value={option.code}>{option.title}{option.subtitle ? ` · ${option.subtitle}` : ''}</option>)}</select></label>
        <p className="take-way-note">用餐方式「{diningModeLabel(diningMode)}」{preferredTakeWay(selectedPlan.take_way_options ?? [], diningMode) ? '已按官方选项预选' : '暂无对应的官方选项，请手动选择'}；最终取餐方式与费用以官方页面为准。</p>
        {orderError && <p className="inline-error" role="alert">{orderError}</p>}
        <motion.button className="primary-button full-width" onClick={()=>void checkout(selectedPlan,takeWayCode)} disabled={!takeWayCode || orderBusy || settingsSaving} {...buttonMotion(reducedMotion,!takeWayCode || orderBusy || settingsSaving)}>前往支付<ArrowRight size={16}/></motion.button>
      </Modal>}
      {order && <Modal key="order" title="官方订单" onClose={closeCheckout}><p>订单 {order.order_id}</p><p>¥{formatMoney(lastOrder?.total_fen ?? order.total_fen)} · {statusLabels[lastOrder?.status ?? order.status]}</p><button className="secondary-button" disabled={submissionUnknown || orderBusy} onClick={()=>void refreshOrder()}>查询订单状态</button>{recoveryUrl && <a className="secondary-button" href={recoveryUrl}>继续前往官方支付</a>}</Modal>}

      {helpOpen && <Modal key="help" title="怎么点餐" onClose={() => setHelpOpen(false)}><div className="help-steps"><div className="help-step"><span>1</span><div><strong>先选门店，再写需求</strong><p>填好人数与预算，或直接说出饭量和口味。</p></div></div><div className="help-step"><span>2</span><div><strong>看清搭配和官方报价</strong><p>比较总价和商品份数，看看哪份合适。</p></div></div><div className="help-step"><span>3</span><div><strong>就选这份，前往支付</strong><p>点击后创建待支付订单，再到官方页面完成付款。返回本页不会被视为付款成功。</p></div></div></div></Modal>}

    </AnimatePresence>
    <AnimatePresence>{toast && <motion.div className="toast" role="status" key={toast} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}>{toast}</motion.div>}</AnimatePresence>
    </LayoutGroup>
  </MotionConfig>
}
