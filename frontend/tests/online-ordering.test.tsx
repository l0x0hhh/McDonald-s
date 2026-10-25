import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import * as api from '../src/lib/api'
import sample from '../../docs/api-examples/plans_6人150元2人不吃辣.json'
import stores from '../../docs/api-examples/stores.json'
import type { PlanResponse, OrderResponse } from '../src/lib/types'

function plans() {
  const result = structuredClone(sample.data) as unknown as PlanResponse
  result.data_source = 'live_mcp'
  result.algorithm_source = 'llm_skill'
  result.plans = result.plans.slice(0, 2).map((p, i) => ({ ...p, type: i ? 'alternative' : 'recommended', title: i ? '换个口味' : '大家都爱吃', quote_id: `quote-${i}`, price_basis: 'live_quote', take_way_options: [{code:'TAKE', title:'到店自取'}], quote_breakdown: { price: p.total_fen, package_fee: null } }))
  return result
}
const order = (): OrderResponse => ({ order_id:'real-1', plan_id:plans().plans[0].plan_id, store_code:'1400011', items:[], total_fen:10140, status:'pending_payment', payment_url:'https://m.mcd.cn/pay?id=real-1', expires_at:null, created_at:'now' })
const navigation = vi.fn()
afterEach(() => vi.unstubAllGlobals())
beforeEach(() => {
  sessionStorage.clear()
  navigation.mockReset()
  vi.spyOn(api, 'getOrder').mockResolvedValue(order())
  vi.spyOn(api, 'getStores').mockResolvedValue({...structuredClone(stores.data),data_source:'live_mcp'})
  vi.spyOn(api, 'getPlans').mockResolvedValue(plans())
  vi.spyOn(api, 'createOrder').mockResolvedValue(order())
})
async function start() {
  render(<App navigate={navigation} />)
  await screen.findByRole('button', {name:/广州今日广场/})
  fireEvent.click(screen.getByRole('button', {name:'看看怎么点'}))
  return (await screen.findAllByRole('button', {name:/就选这份/}))[0]
}
describe('online kitchen checkout', () => {
  it('submits once from the take-way dialog on LAN HTTP without randomUUID', async () => {
    const source = globalThis.crypto
    vi.stubGlobal('crypto', { getRandomValues: source.getRandomValues.bind(source) })
    const result = plans()
    result.plans[0].take_way_options = [{code:'eat-in',title:'堂食'},{code:'take-in-store',title:'外带'}]
    vi.mocked(api.getPlans).mockResolvedValue(result)
    fireEvent.click(await start())
    fireEvent.change(await screen.findByLabelText('取餐方式'), {target:{value:'eat-in'}})
    const pay = screen.getByRole('button',{name:'前往支付'})
    act(() => { pay.click(); pay.click() })
    await waitFor(() => expect(navigation).toHaveBeenCalledWith(order().payment_url))
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.createOrder).mock.calls[0][0].idempotency_key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })
  it('shows preparation failure in the dialog without sending or locking, then allows retry', async () => {
    const source = globalThis.crypto
    const rng = vi.fn(() => { throw new Error('random source failure') })
    vi.stubGlobal('crypto', { getRandomValues: rng })
    const result = plans()
    result.plans[0].take_way_options = [{code:'eat-in',title:'堂食'},{code:'take-in-store',title:'外带'}]
    vi.mocked(api.getPlans).mockResolvedValue(result)
    fireEvent.click(await start())
    fireEvent.change(await screen.findByLabelText('取餐方式'), {target:{value:'eat-in'}})
    fireEvent.click(screen.getByRole('button',{name:'前往支付'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('下单准备失败，请再试一次。')
    expect(api.createOrder).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('kitchen-submission')).toBeNull()
    vi.stubGlobal('crypto', { getRandomValues: source.getRandomValues.bind(source) })
    fireEvent.click(screen.getByRole('button',{name:'前往支付'}))
    await waitFor(() => expect(navigation).toHaveBeenCalledTimes(1))
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('starts online without offline switches or static success', async () => {
    render(<App navigate={navigation} />)
    await screen.findByRole('button', {name:/广州今日广场/})
    expect(api.getStores).toHaveBeenCalledWith('广州','')
    expect(screen.queryByText('离线样例')).not.toBeInTheDocument()
    expect(screen.queryByText(/模拟结算/)).not.toBeInTheDocument()
    expect(api.createOrder).not.toHaveBeenCalled()
  })
  it('selects directly, locks globally synchronously, submits only quote identifiers and navigates same tab', async () => {
    const button = await start()
    act(() => { button.click(); button.click() })
    await waitFor(() => expect(navigation).toHaveBeenCalledWith(order().payment_url))
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(api.createOrder).toHaveBeenCalledWith({plan_id:plans().plans[0].plan_id,quote_id:'quote-0',take_way_code:'TAKE',idempotency_key:expect.any(String)})
    expect(screen.queryByText(/支付成功|已付款/)).not.toBeInTheDocument()
  })
  it('preserves model titles and labels line reference versus official total', async () => {
    await start()
    expect(screen.getByRole('article',{name:'大家都爱吃'})).toBeInTheDocument()
    expect(screen.queryByText('最省方案')).not.toBeInTheDocument()
    expect(screen.getAllByText('官方报价').length).toBe(2)
    expect(screen.getAllByText(/参考 ¥/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/包装费/)).not.toBeInTheDocument()
  })
  it('replaces price-changed quote, waits for a fresh click and changes the idempotency key', async () => {
    const updated = {...plans().plans[0],quote_id:'quote-new',total_fen:11140}
    vi.mocked(api.createOrder).mockRejectedValueOnce(new api.ApiError('价格已更新','E_PRICE_CHANGED',409,{updated_plan:updated}))
    fireEvent.click(await start())
    expect(await screen.findByText(/价格已更新.*再次/)).toBeInTheDocument()
    expect(navigation).not.toHaveBeenCalled()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    fireEvent.click((await screen.findAllByRole('button',{name:/就选这份/}))[0])
    await waitFor(() => expect(api.createOrder).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.createOrder).mock.calls[1][0]).toMatchObject({quote_id:'quote-new'})
    expect(vi.mocked(api.createOrder).mock.calls[1][0].idempotency_key).not.toBe(vi.mocked(api.createOrder).mock.calls[0][0].idempotency_key)
  })
  it.each(['http://www.mcdonalds.com.cn/pay','https://www.mcdonalds.com.cn.evil.test/pay','https://attacker.test/pay','javascript:alert(1)'])('does not navigate invalid payment URL %s', async (url) => {
    vi.mocked(api.createOrder).mockResolvedValue({...order(),payment_url:url})
    fireEvent.click(await start())
    expect(await screen.findByRole('alert')).toHaveTextContent('支付链接')
    expect(navigation).not.toHaveBeenCalled()
    expect(screen.queryByRole('link',{name:'继续前往官方支付'})).not.toBeInTheDocument()
  })
  it('ignores a late order after requirements changed while keeping the global request lock', async () => {
    let resolve!: (value:OrderResponse)=>void
    vi.mocked(api.createOrder).mockImplementationOnce(()=>new Promise(yes=>{resolve=yes}))
    fireEvent.click(await start())
    fireEvent.click(screen.getByRole('button',{name:'增加用餐人数'}))
    await act(async()=>resolve(order()))
    expect(navigation).not.toHaveBeenCalled()
  })
  it('does not blindly retry an unknown submission, even after another plan is selected', async () => {
    vi.mocked(api.createOrder).mockRejectedValueOnce(new api.ApiError('提交结果待核实','E_ORDER_SUBMISSION_UNKNOWN',504))
    fireEvent.click(await start())
    expect(await screen.findByRole('alert')).toHaveTextContent('待核实')
    const buttons=screen.getAllByRole('button',{name:/就选这份/})
    buttons.forEach(button=>{ expect(button).toBeDisabled(); fireEvent.click(button) })
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(navigation).not.toHaveBeenCalled()
  })
  it('keeps the same recovery URL after returning and queries status instead of claiming payment', async () => {
    fireEvent.click(await start())
    await waitFor(()=>expect(navigation).toHaveBeenCalledTimes(1))
    expect(await screen.findByRole('link',{name:'继续前往官方支付'})).toHaveAttribute('href',order().payment_url)
    fireEvent(window,new Event('focus'))
    expect(screen.queryByText('支付成功')).not.toBeInTheDocument()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('preselects the matching official take-way but waits for the payment button', async () => {
    const result=plans();result.plans[0].take_way_options=[{code:'eat-in',title:'堂食'},{code:'take-in-store',title:'外带'}]
    vi.mocked(api.getPlans).mockResolvedValue(result)
    fireEvent.click(await start())
    expect(await screen.findByRole('dialog',{name:'选择取餐方式'})).toBeInTheDocument()
    expect(screen.getByLabelText('取餐方式')).toHaveValue('eat-in')
    expect(screen.getByRole('button',{name:'前往支付'})).toBeEnabled()
    expect(api.createOrder).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('取餐方式'),{target:{value:'take-in-store'}})
    fireEvent.click(screen.getByRole('button',{name:'前往支付'}))
    await waitFor(()=>expect(api.createOrder).toHaveBeenCalledWith(expect.objectContaining({take_way_code:'take-in-store'})))
  })
  it('retains a single pending order when automatic navigation throws',async()=>{
    navigation.mockImplementationOnce(()=>{throw new Error('navigation blocked')})
    fireEvent.click(await start())
    expect(await screen.findByRole('alert')).toHaveTextContent('跳转未完成')
    expect(screen.getByRole('link',{name:'继续前往官方支付'})).toHaveAttribute('href',order().payment_url)
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('keeps unknown submission blocked after remount',async()=>{
    sessionStorage.setItem('kitchen-submission','unknown')
    render(<App navigate={navigation}/>)
    await screen.findByRole('button',{name:/广州今日广场/})
    expect(screen.getByRole('alert')).toHaveTextContent('待核实')
    fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}))
    const choices=await screen.findAllByRole('button',{name:/就选这份/})
    choices.forEach(button=>expect(button).toBeDisabled())
    expect(api.createOrder).not.toHaveBeenCalled()
  })
  it('only reports paid after the official status query confirms it',async()=>{
    vi.mocked(api.getOrder).mockResolvedValue({...order(),status:'paid'})
    fireEvent.click(await start());await waitFor(()=>expect(navigation).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/· 已支付 ·/)).not.toBeInTheDocument()
    await screen.findByRole('link',{name:'继续前往官方支付'})
    fireEvent(window,new Event('focus'))
    await waitFor(()=>expect(api.getOrder).toHaveBeenCalledWith('real-1'))
    expect(await screen.findByText(/· 已支付 ·/)).toBeInTheDocument()
    expect(screen.queryByRole('link',{name:'继续前往官方支付'})).not.toBeInTheDocument()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('keeps an officially known order with unknown status blocked after remount',async()=>{
    sessionStorage.setItem('kitchen-order',JSON.stringify({...order(),status:'unknown'}))
    vi.mocked(api.getOrder).mockResolvedValue({...order(),status:'unknown'})
    render(<App navigate={navigation}/>);await screen.findByRole('button',{name:/广州今日广场/})
    fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}))
    const buttons=await screen.findAllByRole('button',{name:/就选这份/})
    buttons.forEach(button=>expect(button).toBeDisabled())
    expect(api.createOrder).not.toHaveBeenCalled()
  })
  it('never lets a late paid GET for order A overwrite a newly created pending order B',async()=>{
    const old={...order(),order_id:'old-A',status:'paid' as const,payment_url:null}
    sessionStorage.setItem('kitchen-order',JSON.stringify(old))
    let resolve!:(value:OrderResponse)=>void
    vi.mocked(api.getOrder).mockImplementationOnce(()=>new Promise(yes=>resolve=yes))
    fireEvent.click(await start())
    await screen.findByRole('link',{name:'继续前往官方支付'})
    await act(async()=>resolve(old))
    expect(JSON.parse(sessionStorage.getItem('kitchen-order')!).order_id).toBe('real-1')
    expect(screen.getByRole('link',{name:'继续前往官方支付'})).toHaveAttribute('href',order().payment_url)
    screen.getAllByRole('button',{name:/就选这份/}).forEach(button=>expect(button).toBeDisabled())
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('never lets a late GET clear a new uncertain submission marker, including after remount',async()=>{
    const old={...order(),order_id:'old-A',status:'paid' as const,payment_url:null}
    sessionStorage.setItem('kitchen-order',JSON.stringify(old))
    let resolveGet!:(value:OrderResponse)=>void
    let rejectCreate!:(error:Error)=>void
    vi.mocked(api.getOrder).mockImplementationOnce(()=>new Promise(yes=>resolveGet=yes))
    vi.mocked(api.createOrder).mockImplementationOnce(()=>new Promise((_yes,no)=>rejectCreate=no))
    const view=render(<App navigate={navigation}/>);await screen.findByRole('button',{name:/广州今日广场/})
    fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));fireEvent.click((await screen.findAllByRole('button',{name:/就选这份/}))[0])
    expect(sessionStorage.getItem('kitchen-submission')).toBe('unknown')
    await act(async()=>resolveGet(old))
    expect(sessionStorage.getItem('kitchen-submission')).toBe('unknown')
    await act(async()=>rejectCreate(new api.ApiError('待核实','E_ORDER_SUBMISSION_UNKNOWN')))
    expect(sessionStorage.getItem('kitchen-submission')).toBe('unknown')
    view.unmount();vi.mocked(api.getOrder).mockResolvedValue(old)
    render(<App navigate={navigation}/>);await screen.findByRole('button',{name:/广州今日广场/})
    expect(await screen.findByRole('alert')).toHaveTextContent('待核实')
    fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));(await screen.findAllByRole('button',{name:/就选这份/})).forEach(button=>expect(button).toBeDisabled())
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it.each([
    ['amount',{total_fen:11140}],['store',{store_code:'wrong-store'}],['plan',{plan_id:'wrong-plan'}],
  ])('blocks both navigation and persisted recovery for mismatched %s',async(_name,patch)=>{
    const mismatched={...order(),...patch}
    vi.mocked(api.createOrder).mockResolvedValue(mismatched)
    const view=render(<App navigate={navigation}/>);await screen.findByRole('button',{name:/广州今日广场/})
    fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));fireEvent.click((await screen.findAllByRole('button',{name:/就选这份/}))[0])
    await screen.findByRole('alert')
    expect(navigation).not.toHaveBeenCalled()
    expect(screen.queryByRole('link',{name:'继续前往官方支付'})).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-order')).toContain('real-1')
    expect(sessionStorage.getItem('kitchen-payment-intent')).toBeNull()
    view.unmount();vi.mocked(api.getOrder).mockResolvedValue(mismatched)
    render(<App navigate={navigation}/>);await screen.findByRole('button',{name:/广州今日广场/})
    expect(screen.queryByRole('link',{name:'继续前往官方支付'})).not.toBeInTheDocument()
    expect(navigation).not.toHaveBeenCalled()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('revokes payment approval if a current status query changes quote facts',async()=>{
    fireEvent.click(await start());await screen.findByRole('link',{name:'继续前往官方支付'})
    vi.mocked(api.getOrder).mockResolvedValue({...order(),total_fen:11140})
    fireEvent.click(screen.getByRole('button',{name:'查询订单状态'}))
    await waitFor(()=>expect(api.getOrder).toHaveBeenCalled())
    await screen.findByText(/响应与当前订单不一致/)
    expect(screen.queryByRole('link',{name:'继续前往官方支付'})).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('preserves only a validated same-order payment recovery across remount',async()=>{
    const view=render(<App navigate={navigation}/>);await screen.findByRole('button',{name:/广州今日广场/})
    fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));fireEvent.click((await screen.findAllByRole('button',{name:/就选这份/}))[0])
    await screen.findByRole('link',{name:'继续前往官方支付'})
    view.unmount();render(<App navigate={navigation}/>);await screen.findByRole('button',{name:/广州今日广场/})
    expect(await screen.findByRole('link',{name:'继续前往官方支付'})).toHaveAttribute('href',order().payment_url)
    expect(api.createOrder).toHaveBeenCalledTimes(1)
  })
  it('shows store network error and offers an explicit retry', async () => {
    vi.mocked(api.getStores).mockRejectedValueOnce(new Error('门店连接失败'))
    render(<App navigate={navigation}/>)
    expect(await screen.findByRole('alert')).toHaveTextContent('门店连接失败')
    fireEvent.click(screen.getByRole('button',{name:'重试'}))
    await screen.findByRole('button',{name:/广州今日广场/})
    expect(api.getStores).toHaveBeenCalledTimes(2)
  })
})

describe('payment recovery intent', () => {
  const pending = (): OrderResponse => ({ ...order(), payment_url: 'https://m.mcd.cn/scanToPay?id=real-1', expires_at: '2999-10-25T13:30:00+08:00' })
  const unknown = (): OrderResponse => ({ ...order(), status: 'unknown', payment_url: null })

  it('recovers a matched create unknown through explicit review without creating again', async () => {
    vi.mocked(api.createOrder).mockResolvedValue(unknown())
    fireEvent.click(await start())
    expect(await screen.findByRole('alert')).toHaveTextContent(/状态待核实.*查询订单状态/)
    expect(JSON.parse(sessionStorage.getItem('kitchen-payment-intent')!)).toEqual({ order_id: 'real-1', plan_id: plans().plans[0].plan_id, store_code: '1400011', total_fen: 10140 })
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    vi.mocked(api.getOrder).mockResolvedValue(pending())
    fireEvent.click(screen.getByRole('button', { name: '查询订单状态' }))
    fireEvent.click(await screen.findByRole('button', { name: '核对订单后支付' }))
    expect(navigation).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认订单，前往支付' }))
    expect(await screen.findByRole('link', { name: '继续前往官方支付' })).toHaveAttribute('href', pending().payment_url)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(navigation).toHaveBeenCalledExactlyOnceWith(pending().payment_url)
  })

  it('restores only the create-confirmed intent across remount and requires an official pending response before showing its link', async () => {
    vi.mocked(api.createOrder).mockResolvedValue(unknown())
    const view = render(<App navigate={navigation} />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(screen.getByRole('button', { name: '看看怎么点' }))
    fireEvent.click((await screen.findAllByRole('button', { name: /就选这份/ }))[0])
    await screen.findByRole('alert')
    expect(sessionStorage.getItem('kitchen-payment-intent')).not.toBeNull()
    view.unmount()
    vi.mocked(api.getOrder).mockResolvedValue(pending())
    render(<App navigate={navigation} />)
    expect(await screen.findByRole('button', { name: '核对订单后支付' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(api.getOrder).toHaveBeenCalledWith('real-1')
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(navigation).not.toHaveBeenCalled()
  })

  it('does not infer payment intent from an arbitrary cached kitchen-order even when a GET reports pending', async () => {
    sessionStorage.setItem('kitchen-order', JSON.stringify(unknown()))
    vi.mocked(api.getOrder).mockResolvedValue(pending())
    render(<App navigate={navigation} />)
    expect(await screen.findByText(/缺少此前确认的付款记录/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-intent')).toBeNull()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(api.createOrder).not.toHaveBeenCalled()
    expect(navigation).not.toHaveBeenCalled()
  })

  it.each([
    { payment_url: 'https://attacker.test/pay' },
    { expires_at: '2000-01-01T00:00:00Z' },
    { expires_at: 'not-a-date' },
    { expires_at: null },
  ])('never promotes a recovery intent with invalid link or expiry %j', async patch => {
    vi.mocked(api.createOrder).mockResolvedValue(unknown())
    fireEvent.click(await start())
    await screen.findByRole('alert')
    vi.mocked(api.getOrder).mockResolvedValue({ ...pending(), ...patch })
    fireEvent.click(screen.getByRole('button', { name: '查询订单状态' }))
    expect(await screen.findByText(/支付链接缺失、未通过校验或已过期/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-approval')).toBeNull()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(navigation).not.toHaveBeenCalled()
  })

  it('revokes recovery intent after a GET changes quote facts and does not revive it with a later corrected GET', async () => {
    vi.mocked(api.createOrder).mockResolvedValue(unknown())
    fireEvent.click(await start())
    await screen.findByRole('alert')
    expect(sessionStorage.getItem('kitchen-payment-intent')).not.toBeNull()
    vi.mocked(api.getOrder).mockResolvedValue({ ...pending(), total_fen: 11140 })
    fireEvent.click(screen.getByRole('button', { name: '查询订单状态' }))
    await screen.findByText(/响应与当前订单不一致/)
    expect(sessionStorage.getItem('kitchen-payment-intent')).toBeNull()
    vi.mocked(api.getOrder).mockResolvedValue(pending())
    fireEvent.click(screen.getByRole('button', { name: '查询订单状态' }))
    await screen.findByText(/缺少此前确认的付款记录/)
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(navigation).not.toHaveBeenCalled()
  })

  it('reports a confirmed non-pending create status instead of silently returning', async () => {
    vi.mocked(api.createOrder).mockResolvedValue({ ...order(), status: 'paid', payment_url: null })
    fireEvent.click(await start())
    expect(await screen.findByText(/订单已创建，当前状态：已支付/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '继续前往官方支付' })).not.toBeInTheDocument()
    expect(sessionStorage.getItem('kitchen-payment-intent')).toBeNull()
    expect(api.createOrder).toHaveBeenCalledTimes(1)
    expect(navigation).not.toHaveBeenCalled()
  })
})
