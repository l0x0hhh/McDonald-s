import { afterAll, describe, expect, it } from 'vitest'
// @ts-expect-error The test host provides Node; the browser app intentionally has no Node ambient types.
import { readFileSync } from 'node:fs'

const css: string = readFileSync('src/styles.css', 'utf8')

// CSSOM verifies the responsive cascade; browser geometry is checked separately.
const style = document.createElement('style')
style.textContent = css.replace(/@import\s+url\([^)]*\)\s*;/g, '')
document.head.append(style)

function declarations(selector: string, width: number) {
  const result: Record<string, string> = {}
  const visit = (rules: CSSRuleList) => {
    for (const rule of Array.from(rules)) {
      if ('media' in rule && 'cssRules' in rule) {
        const media = (rule as CSSMediaRule).conditionText
        const lower = media.match(/min-width:\s*(\d+)px/)
        const upper = media.match(/max-width:\s*(\d+)px/)
        if ((lower || upper) && (!lower || width >= Number(lower[1])) && (!upper || width <= Number(upper[1]))) visit((rule as CSSMediaRule).cssRules)
      } else if ('selectorText' in rule) {
        const entry = rule as CSSStyleRule
        if (entry.selectorText.split(',').some((item) => item.trim() === selector)) {
          for (let i = 0; i < entry.style.length; i++) {
            const property = entry.style.item(i)
            result[property] = entry.style.getPropertyValue(property).trim()
          }
        }
      }
    }
  }
  visit(style.sheet!.cssRules)
  return result
}

function resolved(selector: string, width: number, property: string) {
  const root = declarations(':root', width)
  return declarations(selector, width)[property]?.replace(/var\((--[\w-]+)\)/g, (_, key: string) => root[key])
}

afterAll(() => style.remove())

describe('shared homepage alignment rails', () => {
  it.each([
    [1440, '430px minmax(0,1fr)', '26px'],
    [1100, '380px minmax(0,1fr)', '20px'],
    [800, '340px minmax(0,1fr)', '16px'],
  ])('keeps hero and task columns on one rail at %ipx', (width, tracks, gap) => {
    for (const selector of ['.hero-inner', '.workbench-grid']) {
      expect(resolved(selector, width as number, 'grid-template-columns')?.replace(/\s+/g, '')).toBe((tracks as string).replace(/\s+/g, ''))
      expect(resolved(selector, width as number, 'gap')).toBe(gap)
    }
  })

  it.each([720, 390, 320])('uses a safe single track at %ipx', (width) => {
    for (const selector of ['.hero-inner', '.workbench-grid']) expect(resolved(selector, width, 'grid-template-columns')).toBe('minmax(0,1fr)')
  })

  it.each([1440, 1100, 800, 390, 320])('contains the full chef artwork within its rail at %ipx', (width) => {
    expect(resolved('.hero-image', width, 'width')).toBe('100%')
    expect(Number.parseFloat(resolved('.hero-image', width, 'margin-left')!)).toBe(0)
    expect(resolved('.hero-image', width, 'object-fit')).toBe('contain')
  })

  it('removes local horizontal offsets from workbench headings and benefits', () => {
    expect(Number.parseFloat(resolved('.results-heading', 1440, 'margin-left')!)).toBe(0)
    expect(Number.parseFloat(resolved('.results-heading', 1440, 'margin-right')!)).toBe(0)
    expect(Number.parseFloat(resolved('.section-heading p', 1440, 'margin-left')!)).toBe(0)
    expect(Number.parseFloat(resolved('.bottom-feature-strip', 1440, 'padding-left')!)).toBe(0)
    expect(Number.parseFloat(resolved('.bottom-feature-strip', 1440, 'padding-right')!)).toBe(0)
  })
})
