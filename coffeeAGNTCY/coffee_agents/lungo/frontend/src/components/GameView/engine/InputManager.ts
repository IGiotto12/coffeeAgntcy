export class InputManager {
    private keys: Set<string> = new Set()

    constructor() {
        window.addEventListener("keydown", this.handleKeyDown)
        window.addEventListener("keyup", this.handleKeyUp)
    }

    private handleKeyDown = (e: KeyboardEvent) => {
        this.keys.add(e.code)
    }

    private handleKeyUp = (e: KeyboardEvent) => {
        this.keys.delete(e.code)
    }

    public isKeyDown(code: string): boolean {
        return this.keys.has(code)
    }

    public destroy() {
        window.removeEventListener("keydown", this.handleKeyDown)
        window.removeEventListener("keyup", this.handleKeyUp)
    }
}
