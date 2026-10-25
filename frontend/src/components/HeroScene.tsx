import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { motion, useMotionValue, useSpring } from 'motion/react'
import { ChefHat, Pause, Play, RotateCcw, Sparkles } from 'lucide-react'
import { kitchenDepth } from '../lib/stage-motion'
import { useLiveReducedMotion } from '../lib/use-live-reduced-motion'
import './hero-scene.css'

// The original image remains intact. Two softly masked eye frames blink independently.
export default function HeroScene({ paused }: { paused: boolean }) {
  const reducedMotion = useLiveReducedMotion()
  const scene = useRef<HTMLDivElement>(null)
  const [inView, setInView] = useState(false)
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== 'hidden')
  const [userPaused, setUserPaused] = useState(false)
  const [entered, setEntered] = useState(false)
  const [loaded, setLoaded] = useState({ boy: false, girl: false })
  const [blinkFailed, setBlinkFailed] = useState(false)
  const [replay, setReplay] = useState(0)
  const targetX = useMotionValue(0)
  const targetY = useMotionValue(0)
  const targetRotateX = useMotionValue(0)
  const targetRotateY = useMotionValue(0)
  const spring = { stiffness: 115, damping: 23, mass: .6 }
  const x = useSpring(targetX, spring)
  const y = useSpring(targetY, spring)
  const rotateX = useSpring(targetRotateX, spring)
  const rotateY = useSpring(targetRotateY, spring)

  useEffect(() => {
    // A browser without observation support simply keeps the ambience still.
    if (!scene.current || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(([entry]) => setInView(Boolean(entry?.isIntersecting)), { threshold: .15 })
    observer.observe(scene.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const update = () => setPageVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])

  const active = inView && pageVisible && !paused && !userPaused && !reducedMotion
  const blinkReady = loaded.boy && loaded.girl && !blinkFailed

  useEffect(() => { if (inView) setEntered(true) }, [inView])
  useEffect(() => {
    if (!active) {
      targetX.set(0); targetY.set(0); targetRotateX.set(0); targetRotateY.set(0)
    }
  }, [active, targetX, targetY, targetRotateX, targetRotateY])

  function resetDepth() {
    targetX.set(0); targetY.set(0); targetRotateX.set(0); targetRotateY.set(0)
  }

  function moveDepth(event: PointerEvent<HTMLDivElement>) {
    if (!active || event.pointerType !== 'mouse') return
    const depth = kitchenDepth(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect())
    targetX.set(depth.x); targetY.set(depth.y)
    targetRotateX.set(depth.rotateX); targetRotateY.set(depth.rotateY)
  }

  return <div className="hero-art" ref={scene} data-idle={active ? 'running' : 'paused'} data-reduced-motion={reducedMotion}
    data-stage-entered={entered} data-blink-ready={blinkReady} data-character-animation={blinkFailed ? 'static' : blinkReady ? 'blink' : 'loading'} data-replay={replay}
    onPointerMove={moveDepth} onPointerLeave={resetDepth}>
    <div className="hero-visual">
      <div className="hero-warm-glow" aria-hidden="true" />
      <motion.div className="hero-stage" style={{ x: active ? x : 0, y: active ? y : 0, rotateX: active ? rotateX : 0, rotateY: active ? rotateY : 0, transformPerspective: 900 }}>
      <div className="hero-entry">
      <div className="hero-canvas">
        <img className="hero-image" src="/images/kitchen-hero-duo.png" alt="红黄玩具厨房里，男厨师和女厨师一起准备汉堡与薯条" width="1536" height="1024" fetchPriority="high" draggable="false" />
        {(['boy', 'girl'] as const).map((chef) => <img key={`${chef}-${replay}`} className={`chef-blink chef-blink--${chef}`} data-chef={chef}
          src="/images/kitchen-chefs-blink.png" alt="" aria-hidden="true" width="1536" height="1024" draggable="false"
          onLoad={() => setLoaded((value) => ({ ...value, [chef]: true }))} onError={() => setBlinkFailed(true)} />)}
      </div>
      <div className="hero-lamp-wash" aria-hidden="true" />
      <svg className="kitchen-steam kitchen-steam--burger" viewBox="0 0 60 80" fill="none" aria-hidden="true">
        <path d="M14 72C2 52 27 43 15 22" /><path d="M31 69C19 49 44 40 32 12" /><path d="M48 72C36 52 61 43 49 22" />
      </svg>
      <svg className="kitchen-steam kitchen-steam--grill" viewBox="0 0 60 80" fill="none" aria-hidden="true">
        <path d="M14 72C2 52 27 43 15 22" /><path d="M31 69C19 49 44 40 32 12" /><path d="M48 72C36 52 61 43 49 22" />
      </svg>
      <Sparkles className="kitchen-glint" size={24} strokeWidth={1.4} aria-hidden="true" />
      </div>
      </motion.div>
    </div>
    <div className="open-sign"><span className="sign-dot" /><span><small>TODAY'S KITCHEN</small>小厨房 · 开饭准备中</span></div>
    <div className="hero-status">
      <div className="art-caption"><ChefHat size={13} />选好搭配，再去付款</div>
      {!reducedMotion && <button type="button" className="idle-toggle" aria-label={userPaused ? '开启动效' : '暂停动效'} aria-pressed={userPaused} onClick={() => setUserPaused((value) => !value)}>
        {userPaused ? <Play size={11} /> : <Pause size={11} />}<span>{userPaused ? '开启动效' : '暂停动效'}</span>
      </button>}
      {!reducedMotion && <button type="button" className="idle-toggle idle-replay" aria-label="重播人物动效"
        disabled={!blinkReady || paused || !inView || !pageVisible}
        onClick={() => { setUserPaused(false); setReplay((value) => value + 1) }}><RotateCcw size={11} /><span>看厨师眨眼</span></button>}
    </div>
  </div>
}
