import { describe, expect, it } from 'vitest'
// @ts-expect-error Node test host
import { readFileSync } from 'node:fs'
const paths = ['src/styles.css','src/components/hero-scene.css','src/components/kitchen-feedback.css','src/components/opening-copy.css','src/components/plan-cards.css']
describe('readable kitchen typography',()=>{
  it('does not fetch fonts at runtime',()=>{expect(readFileSync(paths[0],'utf8')).not.toMatch(/fonts.googleapis|@import/)} )
  it('has no undersized component text',()=>{for(const path of paths){const css=readFileSync(path,'utf8');for(const m of css.matchAll(/font-size:\s*([\d.]+)px/g)) expect(Number(m[1]),`${path}: ${m[0]}`).toBeGreaterThanOrEqual(12)}})
  it('declares system Chinese font and accessible body and controls',()=>{const css=readFileSync(paths[0],'utf8');expect(css).toContain('"Microsoft YaHei"');expect(css).toContain('--text-secondary: #755b43');expect(css).toContain('font-size: 16px');expect(css).toContain('#b72f24')})
})
