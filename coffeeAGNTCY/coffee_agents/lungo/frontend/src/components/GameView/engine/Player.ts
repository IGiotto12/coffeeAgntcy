import { InputManager } from "./InputManager"
import { TileMap } from "./TileMap"

type Direction = "down" | "left" | "right" | "up"

export class Player {
    x: number
    y: number
    speed: number = 200 // pixels per second

    // Collision box (smaller, for tighter movement)
    collisionWidth: number = 24
    collisionHeight: number = 24

    // Display size - wider to match source aspect ratio (500:353 ≈ 1.42:1)
    displayWidth: number = 100
    displayHeight: number = 70

    // Current direction and animation state
    direction: Direction = "down"
    isMoving: boolean = false
    animationFrame: number = 0
    animationTimer: number = 0
    animationSpeed: number = 0.15 // seconds per frame

    // For collision checking (use collision dimensions)
    get width() { return this.collisionWidth }
    get height() { return this.collisionHeight }

    // Dynamic sprite key based on direction and movement
    get spriteKey(): string {
        if (this.isMoving) {
            const frame = this.animationFrame + 1 // frames are 1-indexed in manifest
            return `player_walk_${this.direction}_${frame}`
        }
        return `player_idle_${this.direction}`
    }

    constructor(x: number, y: number) {
        this.x = x
        this.y = y
    }

    update(dt: number, input: InputManager, map: TileMap) {
        let dx = 0
        let dy = 0

        if (input.isKeyDown("KeyW") || input.isKeyDown("ArrowUp")) dy -= 1
        if (input.isKeyDown("KeyS") || input.isKeyDown("ArrowDown")) dy += 1
        if (input.isKeyDown("KeyA") || input.isKeyDown("ArrowLeft")) dx -= 1
        if (input.isKeyDown("KeyD") || input.isKeyDown("ArrowRight")) dx += 1

        // Update direction based on input
        if (dy < 0) this.direction = "up"
        else if (dy > 0) this.direction = "down"
        else if (dx < 0) this.direction = "left"
        else if (dx > 0) this.direction = "right"

        this.isMoving = dx !== 0 || dy !== 0

        if (this.isMoving) {
            // Normalize for diagonal movement
            const length = Math.sqrt(dx * dx + dy * dy)
            dx /= length
            dy /= length

            const nextX = this.x + dx * this.speed * dt
            const nextY = this.y + dy * this.speed * dt

            // Collision detection
            if (!this.checkCollision(nextX, this.y, map)) {
                this.x = nextX
            }
            if (!this.checkCollision(this.x, nextY, map)) {
                this.y = nextY
            }

            // Update animation
            this.animationTimer += dt
            if (this.animationTimer >= this.animationSpeed) {
                this.animationTimer = 0
                this.animationFrame = (this.animationFrame + 1) % 2 // 2 walk frames
            }
        } else {
            // Reset animation when stopped
            this.animationFrame = 0
            this.animationTimer = 0
        }
    }

    private checkCollision(x: number, y: number, map: TileMap): boolean {
        // Check all 4 corners
        const left = x - this.width / 2
        const right = x + this.width / 2
        const top = y - this.height / 2
        const bottom = y + this.height / 2

        return (
            map.isSolid(left, top) ||
            map.isSolid(right, top) ||
            map.isSolid(left, bottom) ||
            map.isSolid(right, bottom)
        )
    }
}
