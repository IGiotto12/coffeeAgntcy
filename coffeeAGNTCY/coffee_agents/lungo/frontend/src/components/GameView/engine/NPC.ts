import { PatternType } from "@/utils/patternUtils"

export interface NPCDefinition {
    id: string
    name: string
    pattern: PatternType // Agent pattern to trigger
    x: number // World coordinates
    y: number
    spriteKey?: string
}

export class NPC {
    def: NPCDefinition
    width: number = 42   // Display width (125 scaled down)
    height: number = 100 // Display height (300 scaled down)

    // Chat state
    isChatting: boolean = false

    constructor(def: NPCDefinition) {
        this.def = def
    }

    startChat() {
        this.isChatting = true
    }

    endChat() {
        this.isChatting = false
    }
}
