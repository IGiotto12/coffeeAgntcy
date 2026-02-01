// Static imports for Vite to handle properly
import playersSheet from "@/assets/game_fig/players.png"
import npcSheet from "@/assets/game_fig/npc.png"
import styleAnchor from "@/assets/game_fig/style_anchor.png"
import townBackground from "@/assets/game_fig/town_background.png"
import chatBubble from "@/assets/game_fig/chat_bubble.png"

import { MANIFEST, getSpriteRect } from "@/assets/manifest"

// Map sheet names to imported URLs
const SHEET_URLS: Record<string, string> = {
    players: playersSheet,
    npc: npcSheet,
    style_anchor: styleAnchor,
    town_background: townBackground,
    chat_bubble: chatBubble,
}

export class AssetManager {
    private images: Map<string, HTMLImageElement> = new Map()
    private loaded: boolean = false

    constructor() { }

    async loadAll(): Promise<void> {
        const promises = Object.entries(SHEET_URLS).map(([key, url]) => {
            return new Promise<void>((resolve, reject) => {
                const img = new Image()
                img.src = url
                img.onload = () => {
                    this.images.set(key, img)
                    console.log(`Loaded sheet: ${key} (${img.width}x${img.height})`)
                    resolve()
                }
                img.onerror = (e) => {
                    console.error(`Failed to load sprite sheet: ${key}`, e)
                    reject(e)
                }
            })
        })

        try {
            await Promise.all(promises)
            this.loaded = true
            console.log("All assets loaded successfully")
        } catch (e) {
            console.error("Asset loading failed", e)
        }
    }

    /**
     * Get a sprite by name, computing x/y from row/col
     */
    getSprite(name: string): { img: HTMLImageElement; x: number; y: number; w: number; h: number } | null {
        const def = MANIFEST[name]
        if (!def) {
            return null
        }
        const img = this.images.get(def.sheet)
        if (!img) {
            return null
        }
        const rect = getSpriteRect(def)
        return { img, ...rect }
    }

    getImage(sheetName: string): HTMLImageElement | null {
        return this.images.get(sheetName) || null
    }

    isLoaded(): boolean {
        return this.loaded
    }
}
