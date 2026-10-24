import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import HeroScene from '../src/components/HeroScene'

describe('two-chef kitchen hero', () => {
  it('shows the versioned illustration with both chefs', () => {
    render(<HeroScene paused={false} />)

    expect(screen.getByRole('img')).toHaveClass('hero-image')
    expect(screen.getByRole('img')).toHaveAttribute('src', '/images/kitchen-hero-duo.png')
  })

  it('describes the boy and girl cooking together to non-visual users', () => {
    render(<HeroScene paused={false} />)

    expect(screen.getByRole('img')).toHaveAttribute('alt', '红黄玩具厨房里，男厨师和女厨师一起准备汉堡与薯条')
  })

  it('reserves the original landscape dimensions to prevent layout shifts', () => {
    render(<HeroScene paused={false} />)

    expect(screen.getByRole('img')).toHaveAttribute('width', '1536')
    expect(screen.getByRole('img')).toHaveAttribute('height', '1024')
  })

  it('keeps the working pause control outside the animated illustration', () => {
    const { container } = render(<HeroScene paused={false} />)
    const visual = container.querySelector('.hero-visual')!
    const pause = screen.getByRole('button', { name: '暂停氛围动效' })

    expect(visual).toContainElement(screen.getByRole('img'))
    expect(visual.querySelector('button, input, textarea, a, select')).toBeNull()
    expect(visual).not.toContainElement(pause)
    fireEvent.click(pause)
    expect(screen.getByRole('button', { name: '开启动效' })).toHaveAttribute('aria-pressed', 'true')
  })
})
