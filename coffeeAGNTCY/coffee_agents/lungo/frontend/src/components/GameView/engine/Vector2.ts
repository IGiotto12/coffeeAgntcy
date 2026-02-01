export interface Vector2 {
    x: number
    y: number
}

export const distance = (a: Vector2, b: Vector2) => {
    return Math.sqrt(Math.pow(a.x - b.x, 2) + Math.pow(a.y - b.y, 2))
}
