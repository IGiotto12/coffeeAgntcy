export class Camera {
    x: number = 0
    y: number = 0
    width: number = 0
    height: number = 0
    worldWidth: number = 0
    worldHeight: number = 0

    constructor(width: number, height: number, worldWidth: number, worldHeight: number) {
        this.width = width
        this.height = height
        this.worldWidth = worldWidth
        this.worldHeight = worldHeight
    }

    public resize(width: number, height: number) {
        this.width = width
        this.height = height
    }

    public follow(targetX: number, targetY: number) {
        // Center camera on target
        this.x = targetX - this.width / 2
        this.y = targetY - this.height / 2

        // Clamp to map bounds
        this.x = Math.max(0, Math.min(this.x, this.worldWidth - this.width))
        this.y = Math.max(0, Math.min(this.y, this.worldHeight - this.height))
    }
}
