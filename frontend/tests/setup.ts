import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }),
})
HTMLElement.prototype.scrollIntoView = function () {}
