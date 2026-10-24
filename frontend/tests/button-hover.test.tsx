import { act, render, screen, waitFor } from '@testing-library/react'
import { motion } from 'motion/react'
import { describe, expect, it } from 'vitest'
import { buttonMotion } from '../src/lib/motion-presets'

function Action({ disabled }: { disabled: boolean }) {
  // This regression concerns gesture state, not spring duration. Keep Motion real,
  // but settle every target immediately so the event sequence stays deterministic.
  return <motion.button disabled={disabled} {...buttonMotion(false, disabled)} transition={{ duration: 0 }}>Generate</motion.button>
}

function pointer(button: HTMLElement, type: 'pointerenter' | 'pointerleave') {
  // jsdom need not implement PointerEvent: Motion only consumes these fields.
  const event = new window.Event(type)
  Object.defineProperties(event, {
    pointerType: { value: 'mouse' },
    button: { value: 0 },
    isPrimary: { value: true },
  })
  act(() => { button.dispatchEvent(event) })
}

async function nextMotionFrames() {
  // Use jsdom's real RAF without changing globals or leaking fake timers. Two
  // frames let the updated gesture target render before checking its transform.
  await act(async () => {
    await new Promise<void>((resolve) => {
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()))
    })
  })
}

describe('button hover during an asynchronous action', () => {
  it('stays neutral after the pointer leaves while disabled and the action is re-enabled', async () => {
    const { rerender } = render(<Action disabled={false} />)
    const button = screen.getByRole('button', { name: 'Generate' })

    pointer(button, 'pointerenter')
    await waitFor(() => expect(button.style.transform).toBe('translateY(-3px) scale(1.025)'))

    rerender(<Action disabled />)
    await waitFor(() => expect(button.style.transform).toBe('none'))
    expect(button).toBeDisabled()

    pointer(button, 'pointerleave')
    rerender(<Action disabled={false} />)
    await nextMotionFrames()

    expect(button).toBeEnabled()
    expect(button.style.transform).toBe('none')
  })
})
