import { useEffect, useState } from 'react'

const preferenceQuery = '(prefers-reduced-motion: reduce)'

// Motion's hook supplies a mount-time value in the installed release. Subscribe
// to the browser directly so an open receipt also obeys live OS changes.
export function useLiveReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState(() => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(preferenceQuery).matches : false
  ))

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const preference = window.matchMedia(preferenceQuery)
    const update = () => setReducedMotion(preference.matches)
    // Close the render-to-subscribe gap as well as listening for later changes.
    update()
    if (typeof preference.addEventListener === 'function') {
      preference.addEventListener('change', update)
      return () => preference.removeEventListener('change', update)
    }
    preference.addListener(update)
    return () => preference.removeListener(update)
  }, [])

  return reducedMotion
}
