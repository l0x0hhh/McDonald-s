import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Keep real Motion frames and strict assertions; shared Windows workers need a bounded DOM lookup budget.
configure({ asyncUtilTimeout: 5000 })

afterEach(() => { cleanup(); vi.restoreAllMocks() })
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }),
})
HTMLElement.prototype.scrollIntoView = function () {}
