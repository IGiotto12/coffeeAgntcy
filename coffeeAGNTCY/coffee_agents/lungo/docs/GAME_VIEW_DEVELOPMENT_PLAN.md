# Game View Development Plan




Semi–open-world 2D web experience where the user plays a character in a 2D world and prompts agents (Exchange / Logistics) the same way as in the existing chat UI. **Tech stack for the game layer: React + div/canvas + CSS/JS only** (no game engine for now). This document is the single source of truth during development—refer back to it for scope and order of work.




---




## Goals




- Add a **Game View** as an alternative to the current Agent UI (graph + chat).
- In the game view: **2D world**, **player character**, **NPCs** that map to agents; **interact with NPC** → prompt agent → show response in-world (e.g. speech bubble, panel).
- Reuse existing **agent API** (`useAgentAPI`, `getApiUrlForPattern`, `startStreaming`) from the game view—no backend changes.
- Keep implementation simple: **React + div/canvas + CSS/JS**; add a game engine later only if needed.




---




## Phases (in order)




### Phase 1: Shell and navigation
- [x] **1.1** Add a **View** switcher in the left sidebar: two tabs — **Agent UI** (current) and **Game View**.
- [x] **1.2** When **Game View** is selected, main content area shows a **blank Game View** (placeholder div); when **Agent UI** is selected, show existing MainArea + ChatArea.
- [x] **1.3** (Optional) Persist last selected view in `localStorage` and restore on load.




**Definition of done for Phase 1:** User can switch between “Agent UI” and “Game View” from the sidebar; Game View is a blank area.




---




### Phase 2: Minimal 2D world and player
- [x] **2.1** **World container**: A fixed-size or full-viewport 2D "world" (e.g. a div with `position: relative`, `overflow: hidden`, explicit width/height).
- [x] **2.2** **Player character**: One controllable entity (div or canvas-drawn) with position `(x, y)` in world coordinates.
- [x] **2.3** **Movement**: Keyboard (WASD or arrows) updates player position; clamp or wrap so the player stays inside the world bounds.
- [x] **2.4** **Rendering**: Either all-div (positioned divs for player + later NPCs) or a single canvas for the world and entities—choose one approach and stick to it for the rest of the plan.




**Definition of done for Phase 2:** In Game View, a 2D world is visible and the player can move around inside it.




---




### Phase 3: NPCs and "which agent"
- [x] **3.1** **NPC entity**: Define an NPC as a position + label + **agent pattern** (e.g. `publish_subscribe` for Exchange, `group_communication` for Logistics).
- [x] **3.2** **Place 2–3 NPCs** in the world (e.g. "Exchange", "Logistics", optional "Helpdesk") with distinct positions and patterns.
- [x] **3.3** **Proximity or overlap**: Detect when the player is "near" an NPC (distance threshold or bounding box). Optionally show a hint ("Press E to talk") when in range.
- [x] **3.4** **Interaction trigger**: On key press (e.g. E) or click when in range, set "active NPC" (and thus the **pattern** to use for the next prompt). Do not open prompt UI yet if you prefer to do that in Phase 4.




**Definition of done for Phase 3:** Player can approach NPCs; interacting sets which agent/pattern will be used for prompting.




---




### Phase 4: Prompt and response in-world
- [x] **4.1** **Prompt input**: When the player interacts with an NPC, show an in-world prompt UI (e.g. input field + "Send" in a panel or speech bubble). Use the **pattern** from the active NPC to call `sendMessage(prompt, pattern)` or `startStreaming(prompt)` (existing hooks).
- [x] **4.2** **Loading state**: While the request is in flight, show a loading indicator in-world (e.g. "…" or spinner near the NPC or panel).
- [x] **4.3** **Show response**: Display the agent's reply in-world (e.g. speech bubble above NPC, or a panel that stays open). Reuse existing response/streaming state from the same hooks.
- [x] **4.4** **Close / dismiss**: Allow the user to close the panel or bubble and return to moving; optionally keep a "last response" visible until next interaction.




**Definition of done for Phase 4:** User can prompt the agent from the game view and see the response in-world without switching back to Agent UI.




---




### Phase 5: Polish and consistency
- [x] **5.1** **Styling**: Align colors, fonts, and panel style with the rest of the app (theme / Tailwind); ensure Game View respects light/dark mode if applicable.
- [x] **5.2** **Responsive behavior**: Decide how the 2D world scales or scrolls on small screens (e.g. scale-to-fit or letterbox).
- [x] **5.3** **Edge cases**: Handle API errors (show error message in-world); handle "no NPC in range" when pressing E.




---




## Out of scope (for this plan)




- Game engine (Phaser, PixiJS, etc.): not in current scope; revisit only if React+div/canvas hits hard limits.
- Multiplayer, persistence of world state, or complex game logic beyond “move, talk to NPC, prompt agent”.
- Changing backend or agent APIs; all agent communication stays as today.




---




## File and structure conventions




- **Game View component(s)**: Under `frontend/src/components/GameView/` (e.g. `GameView.tsx` as the root, then `World.tsx`, `Player.tsx`, `NPC.tsx`, `InWorldPrompt.tsx` as needed).
- **View mode**: Stored in App (e.g. `viewMode: 'agent_ui' | 'game_view'`). Sidebar receives `viewMode` and `onViewModeChange`.
- **Agent API**: Reuse `useAgentAPI()`, `useStartGroupStreaming()`, `getApiUrlForPattern(pattern)` from the Game View; pass **pattern** from the active NPC.




---




## Referencing this plan




- Before adding features, check which phase they belong to and complete previous phases first.
- When in doubt, prefer the smallest change that matches the phase “definition of done”.
- Update the checkboxes in this file as phases/items are completed.

frontend/src/components/GameView/
  GameView.tsx              // root: canvas + overlays + hooks
  engine/
    types.ts                // MapDefinition, NPCDefinition, state types
    useGameLoop.ts          // requestAnimationFrame loop
    input.ts                // key state tracking
    camera.ts               // camera follow + clamp + projection
    collision.ts            // tile collider resolution
    renderer.ts             // draw map layers + sprites + lighting
    timeOfDay.ts            // day/night model
  ui/
    InteractionHint.tsx
    DialogPanel.tsx
    SpeechBubble.tsx
    GlobalLogPanel.tsx
  data/
    npcs.ts                 // NPC definitions (pattern/personality/positions)
    maps/
      town.json
  assets/
    manifest.ts             // sprite sheet metadata
    images/...              // generated art assets