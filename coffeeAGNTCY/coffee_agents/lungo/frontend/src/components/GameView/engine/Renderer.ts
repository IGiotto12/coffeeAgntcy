import { Camera } from "./Camera"
import { TileMap } from "./TileMap"
import { Player } from "./Player"
import { NPC } from "./NPC"
import { AssetManager } from "./AssetManager"

export class Renderer {
    ctx: CanvasRenderingContext2D
    width: number
    height: number

    constructor(ctx: CanvasRenderingContext2D, width: number, height: number) {
        this.ctx = ctx
        this.width = width
        this.height = height
    }

    setSize(width: number, height: number) {
        this.width = width
        this.height = height
    }

    clear() {
        this.ctx.fillStyle = "#111"
        this.ctx.fillRect(0, 0, this.width, this.height)
    }

    render(map: TileMap, player: Player, npcs: NPC[], camera: Camera, assets?: AssetManager) {
        this.ctx.save()
        this.ctx.translate(-camera.x, -camera.y)

        // Draw Background Image
        const bgImage = assets?.getImage("town_background")
        if (bgImage) {
            // Draw the entire background image at world origin
            this.ctx.drawImage(bgImage, 0, 0, map.worldWidth, map.worldHeight)
        } else {
            // Fallback: draw a simple colored background
            this.ctx.fillStyle = "#2d5a27" // Grass green
            this.ctx.fillRect(0, 0, map.worldWidth, map.worldHeight)
        }

        // Optional: Debug collision grid overlay (uncomment to debug)
        // this.drawCollisionDebug(map)

        // Draw NPCs
        npcs.forEach(npc => {
            const spriteKey = npc.def.spriteKey || "npc_supervisor"
            const sprite = assets?.getSprite(spriteKey)

            if (sprite) {
                this.ctx.drawImage(
                    sprite.img,
                    sprite.x, sprite.y, sprite.w, sprite.h,
                    npc.def.x - npc.width / 2, npc.def.y - npc.height / 2,
                    npc.width, npc.height
                )
            } else {
                // Fallback colored box
                this.ctx.fillStyle = "cyan"
                this.ctx.fillRect(npc.def.x - npc.width / 2, npc.def.y - npc.height / 2, npc.width, npc.height)
            }

            // Chat bubble when chatting
            if (npc.isChatting) {
                const bubbleImg = assets?.getImage("chat_bubble")
                const bubbleWidth = 60
                const bubbleHeight = 58 // 246:236 ratio
                const bubbleX = npc.def.x - bubbleWidth / 2
                const bubbleY = npc.def.y - npc.height / 2 - bubbleHeight - 10

                if (bubbleImg) {
                    this.ctx.drawImage(bubbleImg, bubbleX, bubbleY, bubbleWidth, bubbleHeight)
                    // Draw "..." text inside bubble
                    this.ctx.fillStyle = "#4a3520"
                    this.ctx.font = "bold 18px serif"
                    this.ctx.textAlign = "center"
                    this.ctx.fillText("...", npc.def.x, bubbleY + bubbleHeight / 2 + 4)
                } else {
                    // Fallback bubble
                    this.ctx.fillStyle = "#f5e6c8"
                    this.ctx.beginPath()
                    this.ctx.ellipse(npc.def.x, bubbleY + bubbleHeight / 2, bubbleWidth / 2, bubbleHeight / 2, 0, 0, Math.PI * 2)
                    this.ctx.fill()
                    this.ctx.strokeStyle = "#8b7355"
                    this.ctx.lineWidth = 2
                    this.ctx.stroke()
                    this.ctx.fillStyle = "#4a3520"
                    this.ctx.font = "bold 18px serif"
                    this.ctx.textAlign = "center"
                    this.ctx.fillText("...", npc.def.x, bubbleY + bubbleHeight / 2 + 6)
                }
            }

            // Name tag
            this.ctx.fillStyle = "white"
            this.ctx.font = "bold 12px sans-serif"
            this.ctx.textAlign = "center"
            this.ctx.strokeStyle = "black"
            this.ctx.lineWidth = 3
            this.ctx.strokeText(npc.def.name, npc.def.x, npc.def.y - npc.height / 2 - 8)
            this.ctx.fillText(npc.def.name, npc.def.x, npc.def.y - npc.height / 2 - 8)
        })

        // Draw Player
        const playerSprite = assets?.getSprite(player.spriteKey)
        if (playerSprite) {
            this.ctx.drawImage(
                playerSprite.img,
                playerSprite.x, playerSprite.y, playerSprite.w, playerSprite.h,
                player.x - player.displayWidth / 2, player.y - player.displayHeight / 2,
                player.displayWidth, player.displayHeight
            )
        } else {
            this.ctx.fillStyle = "orange"
            this.ctx.fillRect(player.x - player.displayWidth / 2, player.y - player.displayHeight / 2, player.displayWidth, player.displayHeight)
        }

        this.ctx.restore()
    }

    // Debug helper: draw collision grid overlay
    private drawCollisionDebug(map: TileMap) {
        const TILE_SIZE = 32
        for (let y = 0; y < map.height; y++) {
            for (let x = 0; x < map.width; x++) {
                const tile = map.tiles[x + y * map.width]
                if (tile === 1) {
                    this.ctx.fillStyle = "rgba(255, 0, 0, 0.3)"
                    this.ctx.fillRect(x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE)
                }
            }
        }
    }
}
