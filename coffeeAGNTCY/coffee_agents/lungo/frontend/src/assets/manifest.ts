// Sprite sheet specifications
// Players sheet: 2000 × 1412 px, 4 cols × 4 rows = 500 × 353 per frame
// NPC sheet: Specific pixel coordinates (not grid-based)

export const FRAME_SIZE = 32 // Default for tilesets

// Sheet-specific frame dimensions (for grid-based sprites)
export const SHEET_FRAME_SIZES: Record<string, { w: number; h: number }> = {
    players: { w: 500, h: 353 },  // 2000/4 × 1412/4
    style_anchor: { w: 32, h: 32 },
}

export interface SpriteDefinition {
    sheet: string
    // Grid-based positioning (for regular sprite sheets)
    row?: number
    col?: number
    // Direct pixel positioning (for irregular layouts)
    x?: number
    y?: number
    // Frame dimensions
    w?: number
    h?: number
}

// Helper to compute pixel coordinates from definition
export function getSpriteRect(def: SpriteDefinition): { x: number; y: number; w: number; h: number } {
    const sheetFrameSize = SHEET_FRAME_SIZES[def.sheet] || { w: FRAME_SIZE, h: FRAME_SIZE }
    const w = def.w ?? sheetFrameSize.w
    const h = def.h ?? sheetFrameSize.h

    // If direct x/y provided, use those
    if (def.x !== undefined && def.y !== undefined) {
        return { x: def.x, y: def.y, w, h }
    }

    // Otherwise calculate from row/col grid
    return {
        x: (def.col ?? 0) * w,
        y: (def.row ?? 0) * h,
        w,
        h,
    }
}

export const SPRITE_SHEETS = {
    players: "players.png",
    npc: "npc.png",
    style_anchor: "style_anchor.png",
    chat_bubble: "chat_bubble.png",
}

export const MANIFEST: Record<string, SpriteDefinition> = {
    // === PLAYER SPRITES ===
    // Grid-based: (row, col)

    // Idle poses
    "player_idle_down": { sheet: "players", row: 0, col: 0 },
    "player_idle_left": { sheet: "players", row: 2, col: 0 },
    "player_idle_right": { sheet: "players", row: 2, col: 3 },
    "player_idle_up": { sheet: "players", row: 0, col: 3 },

    // Walk animation - Down
    "player_walk_down_1": { sheet: "players", row: 0, col: 1 },
    "player_walk_down_2": { sheet: "players", row: 0, col: 2 },

    // Walk animation - Left
    "player_walk_left_1": { sheet: "players", row: 1, col: 0 },
    "player_walk_left_2": { sheet: "players", row: 1, col: 2 },

    // Walk animation - Right
    "player_walk_right_1": { sheet: "players", row: 1, col: 1 },
    "player_walk_right_2": { sheet: "players", row: 2, col: 2 },

    // Walk animation - Up
    "player_walk_up_1": { sheet: "players", row: 3, col: 1 },
    "player_walk_up_2": { sheet: "players", row: 3, col: 3 },

    // Default player idle
    "player_idle": { sheet: "players", row: 0, col: 0 },

    // === NPC SPRITES ===
    // Direct pixel coordinates (x, y) with size 125×300
    "npc_supervisor": { sheet: "npc", x: 105, y: 1287, w: 125, h: 300 },
    "npc_worker": { sheet: "npc", x: 800, y: 1287, w: 125, h: 300 },
    "npc_barista": { sheet: "npc", x: 1490, y: 1287, w: 125, h: 300 },

    // === UI ===
    "chat_bubble": { sheet: "chat_bubble", x: 0, y: 0, w: 246, h: 236 },
}
