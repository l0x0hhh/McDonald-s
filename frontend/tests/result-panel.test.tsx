import { installOnlineApi } from './helpers/online-api'
installOnlineApi()
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../src/App'

describe('aligned workbench results board', () => {
  it('has a matching board header and a keyboard-focusable results viewport', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    await screen.findByRole('button', { name: /广州今日广场/ })
    const results = screen.getByRole('region', { name: '配餐结果' })
    const header = within(results).getByRole('heading', { name: '小厨房出餐台' }).parentElement
    expect(header).toHaveClass('panel-heading')
    const viewport = within(results).getByRole('region', { name: '方案列表' })
    expect(viewport).toHaveClass('results-content')
    expect(viewport).toHaveAttribute('tabindex', '0')
    expect(within(viewport).getByRole('heading', { name: /先填人数和预算/ })).toBeInTheDocument()
    expect(screen.queryByText('上下滚动查看方案')).not.toBeInTheDocument()
  })

  it('retains all three plans and can select the last one from the results viewport', async () => {
    render(<App />)
    await screen.findByRole('button', { name: /广州今日广场/ })
    fireEvent.click(await screen.findByRole('button', { name: '看看怎么点' }))
    await screen.findByRole('button', { name: '就选这份口味匹配' }, {timeout: 5000})
    const viewport = screen.getByRole('region', { name: '方案列表' })
    expect(within(viewport).getAllByRole('article')).toHaveLength(3)
    expect(screen.getByText('上下滚动查看方案')).toBeInTheDocument()
    fireEvent.click(within(viewport).getByRole('button', { name: '就选这份口味匹配' }))
    expect(await screen.findByRole('dialog', { name: '选择取餐方式' })).toBeInTheDocument()
  })
})
