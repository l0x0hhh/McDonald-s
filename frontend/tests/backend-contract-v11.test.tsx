import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe,expect,it,vi } from 'vitest'
import App from '../src/App'
import * as api from '../src/lib/api'
import {installOnlineApi,canonical} from './helpers/online-api'
installOnlineApi()
const draft={people:2,budget_type:null,budget_fen:null,dietary_constraints:[],appetite:'normal' as const,preferred_tags:[],exclude_beverage:true,pending_fields:['budget']}
async function say(message:string){fireEvent.change(screen.getByLabelText(/这顿想怎么吃/),{target:{value:message}});fireEvent.click(screen.getByRole('button',{name:'发送点餐需求'}));await waitFor(()=>expect(screen.getByLabelText(/这顿想怎么吃/)).toHaveValue(''))}
describe('online backend draft contract without silent constraint loss',()=>{
  it('passes incomplete draft across followups instead of default form values',async()=>{
    const parse=vi.mocked(api.parseChat).mockResolvedValueOnce({requirements:null,draft,missing_fields:['budget'],follow_up:'请确认总预算',ready:false,parse_source:'llm',notes:[]}).mockResolvedValueOnce({requirements:{...canonical,people:2,exclude_beverage:true},ready:true,missing_fields:[],parse_source:'llm',notes:[]})
    render(<App/>);await screen.findByRole('button',{name:/广州今日广场/});await say('两个人，不要饮料');expect(screen.getByRole('button',{name:'看看怎么点'})).toBeDisabled();await say('总预算150元');expect(parse.mock.calls[1][1]).toEqual(draft);fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));await waitFor(()=>expect(api.getPlans).toHaveBeenCalledWith('1400011',expect.objectContaining({people:2,exclude_beverage:true})))
  })
  it('retains known extended constraints from an incomplete draft after an explicit form edit',async()=>{
    vi.mocked(api.parseChat).mockResolvedValueOnce({requirements:null,draft,missing_fields:['budget'],follow_up:'请确认总预算',ready:false,parse_source:'llm',notes:[]})
    render(<App/>);await screen.findByRole('button',{name:/广州今日广场/});await say('两个人，不要饮料')
    fireEvent.change(screen.getByLabelText('预算金额'),{target:{value:'180'}})
    fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}))
    await waitFor(()=>expect(api.getPlans).toHaveBeenCalledWith('1400011',expect.objectContaining({people:2,exclude_beverage:true,budget_fen:18000})))
  })
  it('provides editable beverage and appetite controls rather than dropping those requirements',async()=>{
    render(<App/>);await screen.findByRole('button',{name:/广州今日广场/});fireEvent.click(screen.getByRole('checkbox',{name:'不带饮料'}));fireEvent.change(screen.getByLabelText('饭量'),{target:{value:'heavy'}});fireEvent.click(screen.getByRole('button',{name:'看看怎么点'}));await waitFor(()=>expect(api.getPlans).toHaveBeenCalledWith('1400011',expect.objectContaining({appetite:'heavy',exclude_beverage:true})))
  })
})
