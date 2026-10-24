import { afterAll, describe, expect, it } from 'vitest'
// @ts-expect-error The test host provides Node; the browser app intentionally has no Node ambient types.
import { readFileSync } from 'node:fs'

// Check the responsive stylesheet cascade, not source-string snapshots. Browser
// verification covers equal bounding boxes and actual wheel / keyboard scrolling.
const style = document.createElement('style')
style.textContent = readFileSync('src/styles.css', 'utf8').replace(/@import\s+url\([^)]*\)\s*;/g, '')
document.head.append(style)

function declarations(selector: string, width: number) {
  const result: Record<string, string> = {}
  const visit = (rules: CSSRuleList) => {
    for (const rule of Array.from(rules)) {
      if ('media' in rule && 'cssRules' in rule) {
        const media = (rule as CSSMediaRule).conditionText
        const lower = media.match(/min-width:\s*(\d+)px/)
        const upper = media.match(/max-width:\s*(\d+)px/)
        if ((!lower || width >= Number(lower[1])) && (!upper || width <= Number(upper[1]))) visit((rule as CSSMediaRule).cssRules)
      } else if ('selectorText' in rule) {
        const entry = rule as CSSStyleRule
        if (entry.selectorText.split(',').some((item) => item.trim() === selector)) {
          for (let i = 0; i < entry.style.length; i++) {
            const property = entry.style.item(i)
            result[property] = entry.style.getPropertyValue(property).trim()
          }
          for (const property of ['margin-top', 'margin-bottom', 'padding-top', 'padding-bottom']) {
            const value = entry.style.getPropertyValue(property)
            if (value) result[property] = value.trim()
          }
        }
      }
    }
  }
  visit(style.sheet!.cssRules)
  return result
}

afterAll(() => style.remove())

describe('paired ordering boards', () => {
  it.each([1440, 1100, 900, 800, 721])('lets the brief determine the shared desktop row at %ipx', (width) => {
    expect(declarations('.workbench-grid', width)['align-items']).toBe('stretch')
    const results = declarations('.results-panel', width)
    expect(results.contain).toBe('size')
    expect(Number.parseFloat(results['min-height'])).toBe(0)
    expect(results.display).toBe('flex')
    expect(results['flex-direction']).toBe('column')
  })

  it.each([1440, 800, 721])('keeps only the desktop result body scrollable at %ipx', (width) => {
    const body = declarations('.results-content', width)
    expect(body['overflow-y']).toBe('auto')
    expect(Number.parseFloat(body['min-height'])).toBe(0)
    expect(body.flex).toBe('1 1 0%')
    expect(body['scrollbar-gutter']).toBe('stable')
    expect(body['scrollbar-width']).toBe('auto')
    expect(Number.parseFloat(body['padding-top'])).toBeGreaterThanOrEqual(16)
    expect(Number.parseFloat(body['padding-bottom'])).toBeGreaterThanOrEqual(24)
  })

  it.each([1440, 800, 390])('gives the two boards matching trim and header sizing at %ipx', (width) => {
    const brief = declarations('.brief-panel', width)
    const results = declarations('.results-panel', width)
    expect(brief.border).toBeTruthy()
    expect(results.border).toBe(brief.border)
    expect(results['box-shadow']).toBe(brief['box-shadow'])
    const leftHeading = declarations('.panel-heading', width)
    const rightHeading = declarations('.results-heading', width)
    expect(Number.parseFloat(leftHeading['min-height'])).toBeGreaterThanOrEqual(64)
    expect(rightHeading['min-height']).toBe(leftHeading['min-height'])
    expect(rightHeading['box-sizing']).toBe('border-box')
    expect(Number.parseFloat(rightHeading['margin-top'])).toBe(0)
    expect(Number.parseFloat(rightHeading['margin-bottom'])).toBe(0)
  })

  it.each([720, 390, 320])('uses natural document scrolling on mobile at %ipx', (width) => {
    const results = declarations('.results-panel', width)
    expect(results.contain).toBe('none')
    expect(results.display).toBe('block')
    const body = declarations('.results-content', width)
    expect(body['overflow-y']).toBe('visible')
    expect(body['scrollbar-gutter']).toBe('auto')
    expect(body['max-height']).toBe('none')
    expect(declarations('.results-scroll-hint', width).display).toBe('none')
  })

  it('keeps the focused scrolling region outline inside its clipping edge', () => {
    const focus = declarations('.results-content:focus-visible', 1440)
    expect(focus.outline).toBeTruthy()
    expect(Number.parseFloat(focus['outline-offset'])).toBeLessThan(0)
  })
})
