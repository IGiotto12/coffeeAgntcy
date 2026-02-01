import React, { useRef, useEffect, useState } from "react"
import { PatternType, PATTERNS } from "@/utils/patternUtils"
import { GameLoop } from "./engine/GameLoop"
import { InputManager } from "./engine/InputManager"
import { Renderer } from "./engine/Renderer"
import { AssetManager } from "./engine/AssetManager"
import { TileMap } from "./engine/TileMap"
import { Player } from "./engine/Player"
import { Camera } from "./engine/Camera"
import { NPC, NPCDefinition } from "./engine/NPC"
import { InWorldUI } from "./ui/InWorldUI"

interface GameViewProps {
    selectedPattern: PatternType
    onPatternChange: (pattern: PatternType) => void
}

const GameView: React.FC<GameViewProps> = ({
    selectedPattern,
    onPatternChange,
}) => {
    const containerRef = useRef<HTMLDivElement>(null)
    const canvasRef = useRef<HTMLCanvasElement>(null)

    // Game State Refs (mutable, not causing re-renders)
    const loopRef = useRef<GameLoop | null>(null)
    const inputRef = useRef<InputManager | null>(null)
    const rendererRef = useRef<Renderer | null>(null)
    const assetsRef = useRef<AssetManager | null>(null)
    const mapRef = useRef<TileMap | null>(null)
    const playerRef = useRef<Player | null>(null)
    const cameraRef = useRef<Camera | null>(null)
    const npcsRef = useRef<NPC[]>([])

    // UI State
    const [nearbyNPC, setNearbyNPC] = useState<NPC | null>(null)
    const [isInteracting, setIsInteractingState] = useState(false)

    // State Refs for Loop access
    const nearbyNPCRef = useRef<NPC | null>(null)
    const isInteractingRef = useRef(false)

    const setInteracting = (interacting: boolean) => {
        isInteractingRef.current = interacting
        setIsInteractingState(interacting)
    }

    useEffect(() => {
        if (!canvasRef.current || !containerRef.current) return

        // Initialize Engine
        inputRef.current = new InputManager()

        // World setup - uses CollisionMap data
        mapRef.current = new TileMap()

        // Player setup at spawn point (center of map on walkable path)
        playerRef.current = new Player(512, 480)

        // Camera setup with world dimensions from map
        cameraRef.current = new Camera(
            containerRef.current.clientWidth,
            containerRef.current.clientHeight,
            mapRef.current.worldWidth,
            mapRef.current.worldHeight
        )

        // NPC setup on walkable paths (away from fences)
        npcsRef.current = [
            new NPC({
                id: "supervisor",
                name: "Supervisor",
                pattern: PATTERNS.GROUP_COMMUNICATION,
                x: 512,
                y: 288,  // Top center on main road
                spriteKey: "npc_supervisor",
            }),
            new NPC({
                id: "worker",
                name: "Worker",
                pattern: PATTERNS.PUBLISH_SUBSCRIBE,
                x: 160,
                y: 512,  // Left side path
                spriteKey: "npc_worker",
            }),
            new NPC({
                id: "barista",
                name: "Barista",
                pattern: PATTERNS.SLIM_A2A,
                x: 864,
                y: 512,  // Right side path
                spriteKey: "npc_barista",
            }),
        ]

        // Renderer setup
        const ctx = canvasRef.current.getContext("2d")
        if (ctx) {
            // Disable smoothing for pixel art look
            ctx.imageSmoothingEnabled = false
            rendererRef.current = new Renderer(
                ctx,
                containerRef.current.clientWidth,
                containerRef.current.clientHeight
            )
        }

        // Loop
        loopRef.current = new GameLoop((dt) => {
            if (!inputRef.current || !playerRef.current || !mapRef.current || !rendererRef.current || !cameraRef.current) return

            // Update Physics only if not interacting
            if (!isInteractingRef.current) {
                playerRef.current.update(dt, inputRef.current, mapRef.current)
            }
            cameraRef.current.follow(playerRef.current.x, playerRef.current.y)

            // Proximity Check
            let closest: NPC | null = null
            let minDist = 50 // Interaction radius

            for (const npc of npcsRef.current) {
                const dist = Math.sqrt(
                    Math.pow(playerRef.current.x - npc.def.x, 2) +
                    Math.pow(playerRef.current.y - npc.def.y, 2)
                )
                if (dist < minDist) {
                    closest = npc
                    break // Only one at a time
                }
            }

            // Update React state safely (check if changed)
            if (closest !== nearbyNPCRef.current) {
                nearbyNPCRef.current = closest
                setNearbyNPC(closest)
            }

            // Interaction Trigger
            if (closest && inputRef.current.isKeyDown("KeyE") && !isInteractingRef.current) {
                setInteracting(true)
                // inputRef.current.destroy() // Stop listening to reset keys? No, just clear keys
                // actually we might want to keep inputManager alive but ignore updates
            }

            // Escape to cancel
            if (isInteractingRef.current && inputRef.current.isKeyDown("Escape")) {
                setInteracting(false)
            }

            // Render
            rendererRef.current.clear()
            rendererRef.current.render(
                mapRef.current,
                playerRef.current,
                npcsRef.current,
                cameraRef.current,
                assetsRef.current || undefined
            )
        })

        // Load assets
        assetsRef.current = new AssetManager()
        assetsRef.current.loadAll().catch(err => console.error("Failed to load assets", err))

        loopRef.current.start()

        const handleResize = () => {
            if (containerRef.current && canvasRef.current && rendererRef.current && cameraRef.current) {
                const w = containerRef.current.clientWidth
                const h = containerRef.current.clientHeight
                canvasRef.current.width = w
                canvasRef.current.height = h
                rendererRef.current.setSize(w, h)
                cameraRef.current.resize(w, h)

                // Re-disable smoothing after resize might reset context
                const ctx = canvasRef.current.getContext("2d")
                if (ctx) ctx.imageSmoothingEnabled = false
            }
        }

        window.addEventListener("resize", handleResize)
        // Initial resize to fit
        handleResize()

        return () => {
            loopRef.current?.stop()
            inputRef.current?.destroy()
            window.removeEventListener("resize", handleResize)
        }
    }, []) // Empty dependency array ensures this effect runs only once

    return (
        <div ref={containerRef} className="h-full w-full overflow-hidden bg-black relative">
            <canvas
                ref={canvasRef}
                className="block"
                style={{ imageRendering: "pixelated" }}
            />
            {/* UI Overlay Layer */}
            <div className="absolute top-4 left-4 text-white pointer-events-none opacity-50 font-mono text-sm">
                <p>WASD: Move</p>
                <p>E: Interact</p>
            </div>

            {/* Interaction Hint */}
            {nearbyNPC && !isInteracting && (
                <div
                    className="absolute text-white bg-black/80 px-2 py-1 rounded border border-white/20 transform -translate-x-1/2 -translate-y-full pointer-events-none text-sm font-mono"
                    style={{
                        // We would ideally project world coords to screen, but for now just show at bottom or fixed
                        // Implementing world-to-screen projection needed for floating labels?
                        // Let's just put it at the bottom center or fixed position for now
                        left: "50%",
                        bottom: "20%"
                    }}
                >
                    Press E to talk to {nearbyNPC.def.name}
                </div>
            )}

            {/* Interaction Panel */}
            {isInteracting && nearbyNPC && (
                <InWorldUI
                    npc={nearbyNPC}
                    onClose={() => setInteracting(false)}
                />
            )}
        </div>
    )
}

export default GameView
