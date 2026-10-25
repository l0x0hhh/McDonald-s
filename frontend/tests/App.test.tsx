import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import * as api from '../src/lib/api'
import { installOnlineApi, canonical, planResponse } from './helpers/online-api'
installOnlineApi()
async function start(){render(<App/>);await screen.findByRole('button',{name:/广州今日广场/})}
describe('kitchen ordering experience',()=>{
  it('shows the kitchen brief and online menu',async()=>{await start();expect(screen.getByRole('heading',{name:/今天.*开饭/})).toBeInTheDocument();expect(screen.queryByText('离线样例')).toBeNull()})
  it('clears old plans immediately when a requirement changes',async()=>{await start();fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));await screen.findAllByRole('button',{name:/就选这份/});fireEvent.click(screen.getByRole('button',{name:'增加用餐人数'}));await waitFor(()=>expect(screen.queryByRole('button',{name:/就选这份/})).toBeNull());expect(screen.getByLabelText('用餐人数')).toHaveValue(7)})
  it('shows no result without inventing cheap plans',async()=>{await start();fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'30'}});fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));expect(await screen.findByRole('heading',{name:'暂时没有合适的搭配'})).toBeInTheDocument();expect(screen.queryByRole('button',{name:/就选这份/})).toBeNull()})
  it('submits parsed appetite, beverage, preference and membership requirements without dropping them',async()=>{
    const full={...canonical,appetite:'heavy' as const,exclude_beverage:true,preferred_tags:['鸡肉'],membership_status:'active' as const,habitual_tags:['午餐'],meal_frequency_per_month:5}
    vi.mocked(api.parseChat).mockResolvedValue({requirements:full,draft:full,ready:true,missing_fields:[],parse_source:'llm',notes:[]})
    await start();fireEvent.change(screen.getByLabelText(/这顿想怎么吃/),{target:{value:'饭量大，不要饮料'}});fireEvent.click(screen.getByRole('button',{name:'发送点餐需求'}))
    await screen.findByText('已更新人数和预算。');fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'180'}});fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));await waitFor(()=>expect(api.getPlans).toHaveBeenCalledWith('1400011',{...full,budget_fen:18000}))
  })
  it('clears generated plans when the user switches store',async()=>{await start();fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));await screen.findAllByRole('button',{name:/就选这份/});fireEvent.click(screen.getByRole('button',{name:/广州今日广场/}));fireEvent.click(await screen.findByRole('button',{name:/广州维多利广场/}));await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());await waitFor(()=>expect(screen.queryAllByRole('button',{name:/就选这份/})).toHaveLength(0))})
  it('ignores a stale planning result after a form edit',async()=>{let resolve!:(v:ReturnType<typeof planResponse>)=>void;vi.mocked(api.getPlans).mockImplementationOnce(()=>new Promise(yes=>resolve=yes));await start();fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'160'}});await act(async()=>resolve(planResponse()));expect(screen.queryByRole('button',{name:/就选这份/})).toBeNull()})
})
