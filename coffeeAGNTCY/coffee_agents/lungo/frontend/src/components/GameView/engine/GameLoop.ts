export class GameLoop {
    private lastTime: number = 0
    private running: boolean = false
    private updateFn: (dt: number) => void
    private requestFrameId: number | null = null

    constructor(updateFn: (dt: number) => void) {
        this.updateFn = updateFn
    }

    public start() {
        if (this.running) return
        this.running = true
        this.lastTime = performance.now()
        this.requestFrameId = requestAnimationFrame(this.loop)
    }

    public stop() {
        this.running = false
        if (this.requestFrameId !== null) {
            cancelAnimationFrame(this.requestFrameId)
            this.requestFrameId = null
        }
    }

    private loop = (timestamp: number) => {
        if (!this.running) return

        const dt = (timestamp - this.lastTime) / 1000 // delta time in seconds
        this.lastTime = timestamp

        this.updateFn(dt)

        this.requestFrameId = requestAnimationFrame(this.loop)
    }
}
