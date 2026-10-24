import type { ChatResponse, Requirements } from './types';

const COUNT = '(?:\\d+|[零一二两三四五六七八九十]+)';
const AMOUNT = '(\\d+(?:\\.\\d{1,2})?)';
const BUDGET = new RegExp(`(?:总预算|预算|总计|人均|每人)?\\s*(?:改为|改成|为|是|[:：])?\\s*${AMOUNT}\\s*(?:元|块)`,'g');
const NON_SPICY = new RegExp(`(${COUNT})\\s*(?:个)?(?:人|位)\\s*(?:不吃辣|不能吃辣|不辣|忌辣)`, 'g');
const PEOPLE = new RegExp(`(${COUNT})\\s*(?:个)?人`, 'g');

function countFromText(value: string): number {
  if (/^\d+$/.test(value)) return Number(value);
  const digits: Record<string, number> = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  if (value === '十') return 10;
  if (value.includes('十')) {
    const [tens, ones] = value.split('十');
    return (tens ? digits[tens] : 1) * 10 + (ones ? digits[ones] : 0);
  }
  return digits[value] ?? Number.NaN;
}

/** Deliberately small, visible sentence templates. This function never calls an LLM. */
export function parseDemoChat(message: string, current: Requirements | null): ChatResponse {
  const notes = ['离线演示仅使用固定句式规则（demo_template），不代表真实 LLM 解析。'];
  const next: Partial<Requirements> = current ? structuredClone(current) : {
    budget_type: 'total', dietary_constraints: [], appetite: 'normal', preferred_tags: [],
  };
  const missing: string[] = [];
  let remainder = message.trim();

  // Remove dietary counts before total people so "2人不吃辣" never changes a party of six to two.
  const dietaryMatches = [...message.matchAll(NON_SPICY)];
  const dietaryMatch = dietaryMatches.at(-1);
  if (dietaryMatch) {
    next.dietary_constraints = [{ kind: 'non_spicy', people: countFromText(dietaryMatch[1]) }];
    remainder = remainder.replace(NON_SPICY, ' ');
  }
  const peopleMatches = [...remainder.matchAll(PEOPLE)];
  const peopleMatch = peopleMatches.at(-1);
  if (peopleMatch) next.people = countFromText(peopleMatch[1]);
  remainder = remainder.replace(PEOPLE, ' ');

  const budgetMatches = [...message.matchAll(BUDGET)];
  const budgetMatch = budgetMatches.at(-1);
  if (budgetMatch) {
    next.budget_fen = Math.round(Number(budgetMatch[1]) * 100);
    next.budget_type = /人均|每人/.test(budgetMatch[0]) ? 'per_person' : 'total';
    if (!/预算|总计|人均|每人/.test(budgetMatch[0])) notes.push('未明确预算口径，按总预算理解。');
    remainder = remainder.replace(BUDGET, ' ');
  }

  const allNonSpicy = /(?:全部|所有人|大家|都)(?:都)?(?:不吃辣|不能吃辣|不辣)/;
  const noDietary = /(?:都能吃辣|都吃辣|无忌口|没有忌口|不限辣)/;
  if (allNonSpicy.test(message) && next.people !== undefined) {
    next.dietary_constraints = [{ kind: 'non_spicy', people: next.people }];
    remainder = remainder.replace(allNonSpicy, ' ');
  } else if (noDietary.test(message)) {
    next.dietary_constraints = [];
    remainder = remainder.replace(noDietary, ' ');
  } else if (!dietaryMatch && /不吃辣|不能吃辣|不辣|忌辣/.test(message)) {
    missing.push('non_spicy_people');
    remainder = remainder.replace(/不吃辣|不能吃辣|不辣|忌辣/g, ' ');
  }

  const appetites = [
    ['heavy', /大胃口|饭量大|吃得多/],
    ['light', /小胃口|饭量小|吃得少/],
    ['normal', /正常饭量|普通饭量|饭量正常/],
  ] as const;
  for (const [appetite, pattern] of appetites) {
    if (pattern.test(message)) {
      next.appetite = appetite;
      remainder = remainder.replace(pattern, ' ');
    }
  }

  if (!Number.isInteger(next.people) || next.people! < 1 || next.people! > 20) missing.push('people');
  if (!Number.isInteger(next.budget_fen) || next.budget_fen! < 0) missing.push('budget');
  const dietaryPeople = next.dietary_constraints?.reduce((sum, constraint) => sum + constraint.people, 0) ?? 0;
  const invalidDietary = !Number.isInteger(dietaryPeople) || dietaryPeople < 0 || (next.people !== undefined && dietaryPeople > next.people);
  if (invalidDietary) {
    missing.push('non_spicy_people');
  }

  // Reject unrecognized residual wording rather than silently dropping a hard constraint.
  remainder = remainder.replace(/我们|今天|一共|总共|其中|共有|需要|请|帮我|帮我们|安排|点餐|改成|改为|人数|有|共|为|是|了|的|[,，。.!！?？:：;；、\s]/g, '');
  if (remainder) missing.push('unsupported_template');
  const missingFields = [...new Set(missing)];
  const complete = !missingFields.includes('people') && !missingFields.includes('budget') && !invalidDietary;
  const requirements = complete ? next as Requirements : null;
  let followUp: string | null = null;
  if (missingFields.includes('people')) followUp = '这次共几位用餐？请填写 1–20 人，并把预算和忌口一并告诉我。';
  else if (missingFields.includes('budget')) followUp = '预算是多少元，是总预算还是人均？请把人数和忌口一并补充，或使用需求表单。';
  else if (missingFields.includes('non_spicy_people')) followUp = '有几位不吃辣？请给出不超过总人数的明确人数。';
  else if (missingFields.includes('unsupported_template')) followUp = '离线固定句式未覆盖这条要求，请使用需求表单；表单未包含的要求需待真实接口支持。';

  return {
    requirements,
    missing_fields: missingFields,
    follow_up: followUp,
    ready: missingFields.length === 0,
    parse_source: 'demo_template',
    notes,
  };
}
