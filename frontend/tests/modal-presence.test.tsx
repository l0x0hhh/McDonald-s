import { installOnlineApi } from './helpers/online-api'
installOnlineApi()
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
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(await screen.findByRole('button', { name: '看看怎么点' }))
    const choose = await screen.findByRole('button', { name: '就选这份搭配多样' }, {timeout: 5000})
    choose.focus()
    fireEvent.click(choose)
    await screen.findByRole('dialog', { name: '选择取餐方式' })
    fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(cleanupOrder.dialogAttached).toEqual([false]))
    expect(choose).toHaveFocus()
    expect(document.querySelector<HTMLElement>('.app-shell')?.inert).toBe(false)
    fireEvent.click(choose)
    const reopened = await screen.findByRole('dialog', { name: '选择取餐方式' })
    fireEvent.change(screen.getByLabelText('取餐方式'),{target:{value:'eat-in'}})
    expect(screen.getByRole('button', { name: '前往支付' })).toBeEnabled()
    expect(document.querySelector<HTMLElement>('.app-shell')?.inert).toBe(true)
    expect(reopened).toBeInTheDocument()
  })
})
