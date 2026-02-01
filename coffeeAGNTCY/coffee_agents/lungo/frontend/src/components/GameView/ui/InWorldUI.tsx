import React, { useState, useEffect, useRef } from "react"
import { NPC } from "../engine/NPC"
import { useAgentAPI } from "@/hooks/useAgentAPI"
import {
    useStreamingStatus,
    useStreamingEvents,
    useStreamingError,
    useStreamingActions,
} from "@/stores/auctionStreamingStore"
import {
    useStartGroupStreaming,
    useGroupStreamingActions,
    useGroupIsStreaming,
    useGroupFinalResponse,
    useGroupError
} from "@/stores/groupStreamingStore"
import { PATTERNS, getPatternDisplayName } from "@/utils/patternUtils"

interface InWorldUIProps {
    npc: NPC
    onClose: () => void
}

interface SuggestedPrompt {
    prompt: string
    description?: string
}

const DEFAULT_EXCHANGE_APP_API_URL = "http://127.0.0.1:8000"
const EXCHANGE_APP_API_URL =
    import.meta.env.VITE_EXCHANGE_APP_API_URL || DEFAULT_EXCHANGE_APP_API_URL

export const InWorldUI: React.FC<InWorldUIProps> = ({ npc, onClose }) => {
    const [input, setInput] = useState("")
    const [messages, setMessages] = useState<{ role: "user" | "agent", content: string }[]>([])
    const [suggestedPrompts, setSuggestedPrompts] = useState<SuggestedPrompt[]>([])
    const [isLoadingPrompts, setIsLoadingPrompts] = useState(true)
    const [showPrompts, setShowPrompts] = useState(true)
    const [pendingAgentResponse, setPendingAgentResponse] = useState(false)

    // Hooks
    const { sendMessage, loading: agentLoading } = useAgentAPI()

    // Streaming Hooks (Group Comm)
    const startStreaming = useStartGroupStreaming()
    const { reset: resetGroup } = useGroupStreamingActions()
    const isGroupStreaming = useGroupIsStreaming()
    const groupFinal = useGroupFinalResponse()
    const groupError = useGroupError()

    // Auction Streaming Hooks (Publish/Subscribe Streaming)
    const { connect, reset: resetAuction } = useStreamingActions()
    const auctionStatus = useStreamingStatus()
    const auctionEvents = useStreamingEvents()
    const auctionError = useStreamingError()

    const scrollRef = useRef<HTMLDivElement>(null)
    const processedEventsRef = useRef<Set<string>>(new Set())

    // Fetch suggested prompts
    useEffect(() => {
        const controller = new AbortController()

        const fetchPrompts = async () => {
            try {
                setIsLoadingPrompts(true)
                const isStreamingPattern = npc.def.pattern === PATTERNS.PUBLISH_SUBSCRIBE_STREAMING
                const url = isStreamingPattern
                    ? `${EXCHANGE_APP_API_URL}/suggested-prompts?pattern=streaming`
                    : `${EXCHANGE_APP_API_URL}/suggested-prompts`

                const res = await fetch(url, {
                    cache: "no-cache",
                    signal: controller.signal,
                })

                if (!res.ok) throw new Error(`HTTP ${res.status}`)

                const data = await res.json()

                // Flatten all prompts from categories
                const allPrompts: SuggestedPrompt[] = []
                Object.values(data).forEach((category: any) => {
                    if (Array.isArray(category)) {
                        allPrompts.push(...category)
                    }
                })

                setSuggestedPrompts(allPrompts.slice(0, 5)) // Take first 5
                setIsLoadingPrompts(false)
            } catch (err: any) {
                if (err.name !== "AbortError") {
                    console.warn("Failed to load prompts", err)
                    setIsLoadingPrompts(false)
                }
            }
        }

        fetchPrompts()

        return () => controller.abort()
    }, [npc.def.pattern])

    // Reset stores on open
    useEffect(() => {
        if (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION) {
            resetGroup()
        } else if (npc.def.pattern === PATTERNS.PUBLISH_SUBSCRIBE_STREAMING) {
            resetAuction()
        }
        // Clear processed events on new conversation
        processedEventsRef.current.clear()
    }, [npc, resetGroup, resetAuction])

    // Watch for Group Streaming updates
    useEffect(() => {
        if (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION) {
            if (groupFinal && pendingAgentResponse) {
                setMessages(prev => [...prev, { role: "agent", content: groupFinal }])
                setPendingAgentResponse(false)
            } else if (groupError && pendingAgentResponse) {
                setMessages(prev => [...prev, { role: "agent", content: `Error: ${groupError}` }])
                setPendingAgentResponse(false)
            }
        }
    }, [groupFinal, groupError, npc.def.pattern, pendingAgentResponse])

    // Watch for Auction Streaming events
    useEffect(() => {
        if (npc.def.pattern === PATTERNS.PUBLISH_SUBSCRIBE_STREAMING) {
            // Process new events
            for (const event of auctionEvents) {
                const eventKey = event.response
                if (eventKey && !processedEventsRef.current.has(eventKey)) {
                    processedEventsRef.current.add(eventKey)
                    setMessages(prev => [...prev, { role: "agent", content: event.response }])
                }
            }

            if (auctionError && pendingAgentResponse) {
                setMessages(prev => [...prev, { role: "agent", content: `Error: ${auctionError}` }])
                setPendingAgentResponse(false)
            }

            if (auctionStatus === "completed") {
                setPendingAgentResponse(false)
            }
        }
    }, [auctionEvents, auctionError, auctionStatus, npc.def.pattern, pendingAgentResponse])

    const handleSend = async (query?: string) => {
        const userMsg = query || input
        if (!userMsg.trim()) return

        setInput("")
        setShowPrompts(false) // Hide prompts after first message
        setMessages(prev => [...prev, { role: "user", content: userMsg }])
        setPendingAgentResponse(true)

        try {
            if (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION) {
                // Group Communication Streaming
                resetGroup()
                await startStreaming(userMsg)
            } else if (npc.def.pattern === PATTERNS.PUBLISH_SUBSCRIBE_STREAMING) {
                // Auction Streaming
                processedEventsRef.current.clear()
                resetAuction()
                await connect(userMsg)
            } else {
                // Standard Request/Response (PUBLISH_SUBSCRIBE, SLIM_A2A)
                // This is what the old UI does for coffee buying prompts
                const res = await sendMessage(userMsg, npc.def.pattern)
                setMessages(prev => [...prev, { role: "agent", content: res.response }])
                setPendingAgentResponse(false)
            }
        } catch (e: any) {
            console.error("Agent API error:", e)
            setMessages(prev => [...prev, { role: "agent", content: `Error: ${e.message}` }])
            setPendingAgentResponse(false)
        }
    }

    // Auto-scroll
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight
        }
    }, [messages])

    const isLoading = pendingAgentResponse || agentLoading ||
        (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION && isGroupStreaming) ||
        (npc.def.pattern === PATTERNS.PUBLISH_SUBSCRIBE_STREAMING && auctionStatus === "streaming")

    return (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 z-50">
            <div className="bg-gray-900 w-full max-w-lg rounded-lg border border-gray-700 shadow-2xl flex flex-col max-h-[85vh]">
                {/* Header */}
                <div className="flex justify-between items-center p-4 border-b border-gray-700">
                    <div>
                        <h2 className="text-xl font-bold font-mono text-green-400">{npc.def.name}</h2>
                        <span className="text-xs text-gray-500">{getPatternDisplayName(npc.def.pattern)}</span>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-gray-400 hover:text-white px-2 text-xl"
                    >
                        ✕
                    </button>
                </div>

                {/* Suggested Prompts */}
                {showPrompts && messages.length === 0 && (
                    <div className="p-3 border-b border-gray-700 bg-gray-800/50">
                        <p className="text-xs text-gray-400 mb-2 font-mono">Suggested Prompts:</p>
                        {isLoadingPrompts ? (
                            <div className="text-gray-500 text-xs animate-pulse">Loading prompts...</div>
                        ) : suggestedPrompts.length > 0 ? (
                            <div className="flex flex-wrap gap-2">
                                {suggestedPrompts.map((prompt, i) => (
                                    <button
                                        key={i}
                                        onClick={() => handleSend(prompt.prompt)}
                                        className="text-xs bg-green-900/40 hover:bg-green-800/60 text-green-300 px-2 py-1 rounded border border-green-700/50 transition-colors font-mono truncate max-w-[200px]"
                                        title={prompt.prompt}
                                    >
                                        {prompt.prompt.length > 30 ? prompt.prompt.slice(0, 30) + "..." : prompt.prompt}
                                    </button>
                                ))}
                            </div>
                        ) : (
                            <div className="text-gray-500 text-xs">No prompts available</div>
                        )}
                    </div>
                )}

                {/* Chat Area */}
                <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[200px]" ref={scrollRef}>
                    {messages.length === 0 && (
                        <div className="text-gray-500 text-sm font-mono italic text-center mt-10">
                            Start a conversation with {npc.def.name}...
                        </div>
                    )}
                    {messages.map((msg, i) => (
                        <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                            <div className={`max-w-[85%] rounded px-3 py-2 text-sm font-mono whitespace-pre-wrap ${msg.role === "user"
                                ? "bg-green-900/50 text-green-100 border border-green-700"
                                : "bg-gray-800 text-gray-200 border border-gray-700"
                                }`}>
                                {msg.content}
                            </div>
                        </div>
                    ))}
                    {isLoading && (
                        <div className="flex items-center gap-2 text-green-500 text-xs font-mono">
                            <span className="animate-pulse">●</span>
                            <span>Agent is processing...</span>
                        </div>
                    )}
                </div>

                {/* Input Area */}
                <div className="p-4 border-t border-gray-700 bg-gray-900/50 rounded-b-lg">
                    <div className="flex gap-2">
                        <input
                            type="text"
                            className="flex-1 bg-black/40 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-green-500 text-sm font-mono placeholder-gray-600"
                            placeholder="Type your message..."
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && !isLoading && handleSend()}
                            disabled={isLoading}
                            autoFocus
                        />
                        <button
                            className={`px-4 py-2 rounded text-sm font-bold transition-colors ${isLoading || !input.trim()
                                ? "bg-gray-700 text-gray-500 cursor-not-allowed"
                                : "bg-green-600 hover:bg-green-700 text-white"
                                }`}
                            onClick={() => handleSend()}
                            disabled={isLoading || !input.trim()}
                        >
                            Send
                        </button>
                    </div>
                    <div className="text-right text-[10px] text-gray-600 mt-1 font-mono">
                        Press Esc to close
                    </div>
                </div>
            </div>
        </div>
    )
}
