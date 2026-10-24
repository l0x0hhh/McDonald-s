type Bounds = { left: number; top: number; width: number; height: number }

/** The decorative stage moves by a few pixels; the layout and controls never do. */
export function kitchenDepth(clientX: number, clientY: number, rect: Bounds) {
  const still = { x: 0, y: 0, rotateX: 0, rotateY: 0 }
  if (![clientX, clientY, rect.left, rect.top, rect.width, rect.height].every(Number.isFinite) || rect.width <= 0 || rect.height <= 0) return still
  const clamp = (value: number) => Math.max(-1, Math.min(1, value))
  const x = clamp((clientX - rect.left) / rect.width * 2 - 1)
  const y = clamp((clientY - rect.top) / rect.height * 2 - 1)
  return { x: x * 4, y: y * 3, rotateX: y === 0 ? 0 : -y * .45, rotateY: x * .65 }
}
