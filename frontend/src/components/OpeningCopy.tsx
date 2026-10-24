import { useEffect, useRef, type ReactNode } from 'react'
import { useAnimate, type AnimationSequence } from 'motion/react'
import './opening-copy.css'

const copyTargets = ':scope > .eyebrow, :scope > h1, :scope > .hero-description, :scope > .hero-promises'

function settleCopy(element: HTMLElement | null) {
  element?.querySelectorAll<HTMLElement>(copyTargets).forEach((cue) => {
    cue.style.opacity = '1'
    cue.style.transform = 'none'
  })
}

/** One short opening cue for copy only; the action rail never changes position. */
export default function OpeningCopy({ children, reducedMotion }: { children: ReactNode; reducedMotion: boolean }) {
  const [scope, animate] = useAnimate<HTMLDivElement>()
  const animateAtMount = useRef(animate)
  const allowedAtMount = useRef(!reducedMotion)
  const playback = useRef<{ stop: () => void } | null>(null)

  useEffect(() => {
    if (!allowedAtMount.current) return
    const sequence: AnimationSequence = [
      [':scope > .eyebrow', { opacity: [.55, 1], y: [6, 0] }, { at: .02, duration: .34 }],
      [':scope > h1', { opacity: [.55, 1], y: [10, 0] }, { at: .09, duration: .55 }],
      [':scope > .hero-description', { opacity: [.6, 1], y: [6, 0] }, { at: .18, duration: .42 }],
      [':scope > .hero-promises', { opacity: [.65, 1], y: [4, 0] }, { at: .31, duration: .38 }],
    ]
    playback.current = animateAtMount.current(sequence, { defaultTransition: { ease: [.2, .8, .2, 1] } })
    return () => { playback.current?.stop(); playback.current = null }
  }, [scope])

  useEffect(() => {
    if (!reducedMotion) return
    playback.current?.stop()
    settleCopy(scope.current)
  }, [reducedMotion, scope])

  return <div className="hero-copy opening-copy" ref={scope} data-opening-motion={reducedMotion ? 'reduced' : 'enabled'}>{children}</div>
}
