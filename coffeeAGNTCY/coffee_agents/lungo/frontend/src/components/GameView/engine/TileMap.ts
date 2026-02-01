import { COLLISION_MAP, COLLISION_TILE_SIZE, MAP_COLS, MAP_ROWS, MAP_WIDTH, MAP_HEIGHT } from "./CollisionMap"

export const TILE_SIZE = COLLISION_TILE_SIZE

export class TileMap {
    width: number = MAP_COLS
    height: number = MAP_ROWS
    worldWidth: number = MAP_WIDTH
    worldHeight: number = MAP_HEIGHT
    tiles: number[]

    constructor() {
        // Use the collision map from CollisionMap.ts
        this.tiles = [...COLLISION_MAP]
    }

    public isSolid(x: number, y: number): boolean {
        const tileX = Math.floor(x / TILE_SIZE)
        const tileY = Math.floor(y / TILE_SIZE)

        // Out of bounds = solid
        if (tileX < 0 || tileX >= this.width || tileY < 0 || tileY >= this.height) {
            return true
        }

        return this.tiles[tileX + tileY * this.width] === 1
    }

    // Get tile at world coordinates
    public getTile(x: number, y: number): number {
        const tileX = Math.floor(x / TILE_SIZE)
        const tileY = Math.floor(y / TILE_SIZE)

        if (tileX < 0 || tileX >= this.width || tileY < 0 || tileY >= this.height) {
            return 1 // Solid
        }

        return this.tiles[tileX + tileY * this.width]
    }
}
