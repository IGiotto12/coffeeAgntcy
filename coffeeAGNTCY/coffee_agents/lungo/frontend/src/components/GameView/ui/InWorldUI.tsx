import React, { useState, useEffect, useRef } from "react"
import { NPC } from "../engine/NPC"
import { useAgentAPI } from "@/hooks/useAgentAPI"
import {
    useStartGroupStreaming,
    useGroupStreamingActions,
    useGroupIsStreaming,
    useGroupFinalResponse,
    useGroupError
} from "@/stores/groupStreamingStore"
import { PATTERNS } from "@/utils/patternUtils"

interface InWorldUIProps {
    npc: NPC
    onClose: () => void
}

export const InWorldUI: React.FC<InWorldUIProps> = ({ npc, onClose }) => {
    const [input, setInput] = useState("")
    const [messages, setMessages] = useState<{ role: "user" | "agent", content: string }[]>([])

    // Hooks
    const { sendMessage, loading: agentLoading } = useAgentAPI()

    // Streaming Hooks (Group Comm)
    const startStreaming = useStartGroupStreaming()
    const { reset: resetGroup } = useGroupStreamingActions()
    const isStreaming = useGroupIsStreaming()
    const groupFinal = useGroupFinalResponse()
    const groupError = useGroupError()

    const scrollRef = useRef<HTMLDivElement>(null)

    useEffect(() => {
        // Reset group store on open
        if (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION) {
            resetGroup()
        }
    }, [npc, resetGroup])

    // Watch for Group Streaming updates
    useEffect(() => {
        if (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION) {
            if (groupFinal) {
                setMessages(prev => [...prev, { role: "agent", content: groupFinal }])
            } else if (groupError) {
                setMessages(prev => [...prev, { role: "agent", content: `Error: ${groupError}` }])
            }
        }
    }, [groupFinal, groupError, npc.def.pattern])

    const handleSend = async () => {
        if (!input.trim()) return

        const userMsg = input
        setInput("")
        setMessages(prev => [...prev, { role: "user", content: userMsg }])

        try {
            if (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION) {
                // Streaming
                await startStreaming(userMsg)
            } else {
                // Standard Request/Response
                const res = await sendMessage(userMsg, npc.def.pattern)
                setMessages(prev => [...prev, { role: "agent", content: res.response }])
            }
        } catch (e: any) {
            setMessages(prev => [...prev, { role: "agent", content: `Error: ${e.message}` }])
        }
    }

    // Auto-scroll
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight
        }
    }, [messages])

    const isLoading = agentLoading || (npc.def.pattern === PATTERNS.GROUP_COMMUNICATION && isStreaming)

    return (
        <div className="absolute inset-0 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 z-50">
            <div className="bg-gray-900 w-full max-w-lg rounded-lg border border-gray-700 shadow-2xl flex flex-col max-h-[80vh]">
                {/* Header */}
                <div className="flex justify-between items-center p-4 border-b border-gray-700">
                    <div>
                        <h2 className="text-xl font-bold font-mono text-green-400">{npc.def.name}</h2>
                        <span className="text-xs text-gray-500">{npc.def.pattern}</span>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-gray-400 hover:text-white px-2"
                    >
                        ✕
                    </button>
                </div>

                {/* Chat Area */}
                <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-[200px]" ref={scrollRef}>
                    {messages.length === 0 && (
                        <div className="text-gray-500 text-sm font-mono italic text-center mt-10">
                            Start a conversation with {npc.def.name}...
                        </div>
                    )}
                    {messages.map((msg, i) => (
                        <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                            <div className={`max-w-[80%] rounded px-3 py-2 text-sm font-mono ${msg.role === "user"
                                    ? "bg-green-900/50 text-green-100 border border-green-700"
                                    : "bg-gray-800 text-gray-200 border border-gray-700"
                                }`}>
                                {msg.content}
                            </div>
                        </div>
                    ))}
                    {isLoading && (
                        <div className="text-green-500 text-xs font-mono animate-pulse">
                            Agent is processing...
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
                            onClick={handleSend}
                            disabled={isLoading || !input.trim()}
                        >
                            Send
                        </button>
                    </div>
                    <div className="text-right text-[10px] text-gray-600 mt-1">
                        Press Esc to close
                    </div>
                </div>
            </div>
        </div>
    )
}
