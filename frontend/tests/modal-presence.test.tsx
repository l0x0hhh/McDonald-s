import { useCallback } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from '../src/App'

const cleanupOrder = vi.hoisted(() => ({ dialogAttached: [] as boolean[] }))
vi.mock('motion/react', async (original) => {
  const motion = await original<typeof import('motion/react')>()
  return {
    ...motion,
    usePresence: () => {
      const [isPresent, safeToRemove] = motion.usePresence()
      return [isPresent, useCallback(() => {
        cleanupOrder.dialogAttached.push(Boolean(document.querySelector('[role="dialog"]')))
        safeToRemove?.()
      }, [safeToRemove])]
    },
  }
})

describe('receipt presence cleanup ordering', () => {
  it('unregisters projection children before completing presence, then reopens on the first click', async () => {
    cleanupOrder.dialogAttached = []
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '生成省钱方案' }))
    const choose = await screen.findByRole('button', { name: '就选这份搭配多样' })
    choose.focus()
    fireEvent.click(choose)
    await screen.findByRole('dialog', { name: '确认这份开饭计划' })
    fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(cleanupOrder.dialogAttached).toEqual([false]))
    expect(choose).toHaveFocus()
    expect(document.querySelector<HTMLElement>('.app-shell')?.inert).toBe(false)
    fireEvent.click(choose)
    const reopened = await screen.findByRole('dialog', { name: '确认这份开饭计划' })
    expect(screen.getByRole('button', { name: '确认模拟结算' })).toBeEnabled()
    expect(document.querySelector<HTMLElement>('.app-shell')?.inert).toBe(true)
    expect(reopened).toBeInTheDocument()
  })
})
