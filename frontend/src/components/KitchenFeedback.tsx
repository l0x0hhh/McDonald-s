import { motion, useReducedMotion } from 'motion/react'
import { Check, CookingPot, ReceiptText, RotateCcw, Utensils } from 'lucide-react'
import { buttonMotion } from '../lib/motion-presets'
import './kitchen-feedback.css'

type FeedbackProps =
  | { state: 'loading' }
  | { state: 'success'; count: number }
  | { state: 'error'; message: string; onRetry: () => void; retryDisabled: boolean }

export default function KitchenFeedback(props: FeedbackProps) {
  const reducedMotion = Boolean(useReducedMotion())
  const { state } = props

  return <motion.div
    className={`kitchen-feedback kitchen-feedback--${state}`}
    data-feedback={state}
    data-motion={reducedMotion ? 'still' : 'quiet'}
    role={state === 'error' ? 'alert' : 'status'}
    aria-live={state === 'error' ? 'assertive' : 'polite'}
    aria-atomic="true"
    initial={{ opacity: 0 }} animate={{ opacity: 1 }}
    exit={{ opacity: 0, transition: { duration: .12 } }}
    transition={{ duration: reducedMotion ? 0 : .16 }}
  >
    {props.state === 'loading' && <>
      <div className="feedback-decoration" aria-hidden="true">
        <div className="feedback-ticket"><ReceiptText size={21} /><span>ORDER / IN KITCHEN</span><i /><i /><i /></div>
        <div className="feedback-pot"><div className="feedback-steam"><i /><i /><i /></div><CookingPot size={58} strokeWidth={1.65} /></div>
        <span className="feedback-utensil"><Utensils size={25} /></span>
      </div>
      <span className="feedback-eyebrow">配餐中</span>
      <h4>正在搭配…</h4>
      <p>正在查看菜单和价格，请稍等。</p>
      <div className="feedback-wait-note"><span className="feedback-wait-dots" aria-hidden="true"><i /><i /><i /></span>按人数、预算和口味选餐</div>
      <small className="feedback-help">你可以继续改需求，改完后再配餐。</small>
    </>}
    {props.state === 'success' && <>
      <span className="feedback-ready-stamp" aria-hidden="true"><Check size={24} strokeWidth={2.6} /></span>
      <div className="feedback-ready-copy"><span className="feedback-eyebrow">配餐完成</span><h4>已生成 {props.count} 份方案</h4><p>看看总价、人均和不辣主食，选一份合适的。</p></div>
    </>}
    {props.state === 'error' && <>
      <div className="feedback-error-ticket feedback-decoration" aria-hidden="true"><ReceiptText size={38} /><span>再试一次</span></div>
      <span className="feedback-eyebrow">配餐未完成</span>
      <h4>这次配餐没完成</h4>
      <p className="feedback-error-detail">{props.message}</p>
      <p>需求还在，不用重新填写。</p>
      <motion.button type="button" className="secondary-button feedback-retry" aria-label="重新配餐" onClick={props.onRetry} disabled={props.retryDisabled} {...buttonMotion(reducedMotion, props.retryDisabled)}><RotateCcw size={15} />重新配餐</motion.button>
      <small className="feedback-help">也可以先改一下左侧需求，再重新配餐。</small>
    </>}
  </motion.div>
}
