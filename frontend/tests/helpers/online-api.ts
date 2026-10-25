import { beforeEach, vi } from 'vitest'
import * as api from '../../src/lib/api'
import sample from '../../../docs/api-examples/plans_6人150元2人不吃辣.json'
import noSolution from '../../../docs/api-examples/plans_无解.json'
import stores from '../../../docs/api-examples/stores.json'
import type { PlanResponse, Requirements } from '../../src/lib/types'
export const canonical = structuredClone(sample.data.requirements) as Requirements
export function planResponse(): PlanResponse {
  const value=structuredClone(sample.data) as unknown as PlanResponse
  value.data_source='live_mcp'; value.algorithm_source='llm_skill'; value.suggestions=[]
  value.plans.forEach((plan,i)=>{plan.quote_id=`quote-${i}`; plan.price_basis='live_quote'; plan.take_way_options=[{code:'eat-in',title:'堂食'},{code:'take-in-store',title:'外带'}]})
  return value
}
export function installOnlineApi() {
  beforeEach(()=>{
    sessionStorage.clear()
    vi.spyOn(api,'getStores').mockResolvedValue({...structuredClone(stores.data),data_source:'live_mcp'})
    vi.spyOn(api,'getPlans').mockImplementation(async(_store,requirements)=>requirements.budget_fen===3000 ? {...structuredClone(noSolution.data),data_source:'live_mcp'} as PlanResponse : {...planResponse(), requirements})
    vi.spyOn(api,'parseChat').mockImplementation(async(_message,current)=>({requirements:current as Requirements,draft:current,missing_fields:[],ready:true,parse_source:'llm',notes:[]}))
    vi.spyOn(api,'createOrder').mockRejectedValue(new api.ApiError('测试未配置订单创建','E_TEST'))
    vi.spyOn(api,'getOrder').mockRejectedValue(new api.ApiError('订单状态待核实','E_TEST'))
  })
}
