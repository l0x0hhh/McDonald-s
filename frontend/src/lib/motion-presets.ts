// Underdamped, single-settle toy physics. Every spatial gesture has a reduced-motion branch.
export const gameSpring = { type: 'spring' as const, stiffness: 360, damping: 17, mass: .9 }
export const dialogSpring = { type: 'spring' as const, stiffness: 400, damping: 20, mass: .95 }

export function buttonMotion(reduced: boolean, disabled = false) {
  return {
    whileHover: reduced ? undefined : disabled ? { y: 0, scale: 1 } : { y: -3, scale: 1.025 },
    whileTap: reduced || disabled ? undefined : { y: 5, scale: .94 },
    transition: gameSpring,
  }
}

export function cardMotion(index: number, reduced: boolean) {
  return {
    initial: { opacity: 0, y: reduced ? 0 : 44, scale: reduced ? 1 : .88, rotate: reduced ? 0 : -2 },
    animate: { opacity: 1, y: 0, scale: 1, rotate: 0 },
    transition: { ...gameSpring, delay: reduced ? 0 : index * .09 },
    whileHover: reduced ? undefined : { y: -9, rotate: -.6, scale: 1.012 },
  }
}

export function dialogMotion(reduced: boolean) {
  return {
    initial: { opacity: 0, y: reduced ? 0 : 58, scale: reduced ? 1 : .78, rotate: reduced ? 0 : -3 },
    animate: { opacity: 1, y: 0, scale: 1, rotate: 0 },
    exit: { opacity: 0, y: reduced ? 0 : 24, scale: reduced ? 1 : .92, rotate: reduced ? 0 : 2, transition: { duration: .16, ease: 'easeIn' as const } },
    transition: dialogSpring,
  }
}
