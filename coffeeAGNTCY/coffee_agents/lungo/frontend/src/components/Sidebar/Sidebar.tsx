/**
 * Copyright AGNTCY Contributors (https://github.com/agntcy)
 * SPDX-License-Identifier: Apache-2.0
 **/

import React, { useState, useEffect } from "react"
import {
  PatternType,
  PATTERNS,
  getApiUrlForPattern,
} from "@/utils/patternUtils"
import SidebarItem from "./sidebarItem"
import SidebarDropdown from "./SidebarDropdown"

export type ViewMode = "agent_ui" | "game_view"

interface SidebarProps {
  selectedPattern: PatternType
  onPatternChange: (pattern: PatternType) => void
  viewMode: ViewMode
  onViewModeChange: (mode: ViewMode) => void
}

const Sidebar: React.FC<SidebarProps> = ({
  selectedPattern,
  onPatternChange,
  viewMode,
  onViewModeChange,
}) => {
  const [isPublishSubscribeExpanded, setIsPublishSubscribeExpanded] =
    useState(true)
  const [
    isPublishSubscribeStreamingExpanded,
    setIsPublishSubscribeStreamingExpanded,
  ] = useState(true)
  const [isGroupCommunicationExpanded, setIsGroupCommunicationExpanded] =
    useState(true)
  const [transport, setTransport] = useState<string>("")

  useEffect(() => {
    const fetchTransportConfig = async () => {
      try {
        const response = await fetch(
          `${getApiUrlForPattern(PATTERNS.PUBLISH_SUBSCRIBE)}/transport/config`,
        )
        const data = await response.json()
        if (data.transport) {
          setTransport(data.transport)
        }
      } catch (error) {
        console.error("Error fetching transport config:", error)
      }
    }

    fetchTransportConfig()
  }, [])

  const handlePublishSubscribeToggle = () => {
    setIsPublishSubscribeExpanded(!isPublishSubscribeExpanded)
  }

  const handlePublishSubscribeStreamingToggle = () => {
    setIsPublishSubscribeStreamingExpanded(!isPublishSubscribeStreamingExpanded)
  }

  const handleGroupCommunicationToggle = () => {
    setIsGroupCommunicationExpanded(!isGroupCommunicationExpanded)
  }

  return (
    <div className="flex h-full w-64 flex-none flex-col gap-5 border-r border-sidebar-border bg-sidebar-background font-inter lg:w-[320px]">
      <div className="flex flex-col border-b border-sidebar-border p-4 pb-2">
        <div className="flex w-full rounded-md bg-nav-background-secondary p-1">
          <button
            onClick={() => onViewModeChange("agent_ui")}
            className={`flex-1 rounded-sm py-1.5 text-center text-sm font-medium transition-colors ${viewMode === "agent_ui"
                ? "bg-action-background text-white shadow-sm"
                : "text-sidebar-text hover:bg-white/5"
              }`}
          >
            Agent UI
          </button>
          <button
            onClick={() => onViewModeChange("game_view")}
            className={`flex-1 rounded-sm py-1.5 text-center text-sm font-medium transition-colors ${viewMode === "game_view"
                ? "bg-action-background text-white shadow-sm"
                : "text-sidebar-text hover:bg-white/5"
              }`}
          >
            Game View
          </button>
        </div>
      </div>
      <div className="flex h-full flex-1 flex-col gap-5 p-4 pt-0">
        <div className="flex flex-col">
          <div className="flex min-h-[36px] w-full items-center gap-2 rounded py-2 pl-2 pr-5">
            <span className="flex-1 font-inter text-sm font-normal leading-5 tracking-[0.25px] text-sidebar-text">
              Conversation: Order Fulfilment
            </span>
          </div>

          <div className="flex flex-col">
            <div className="flex min-h-[36px] w-full items-center gap-2 rounded py-2 pl-5 pr-5">
              <span className="flex-1 font-inter text-sm font-normal leading-5 tracking-[0.25px] text-sidebar-text">
                Agentic Patterns
              </span>
            </div>

            <div>
              <SidebarDropdown
                title="Secure Group Communication"
                isExpanded={isGroupCommunicationExpanded}
                onToggle={handleGroupCommunicationToggle}
              >
                <SidebarItem
                  title="A2A SLIM"
                  isSelected={selectedPattern === PATTERNS.GROUP_COMMUNICATION}
                  onClick={() => onPatternChange(PATTERNS.GROUP_COMMUNICATION)}
                />
              </SidebarDropdown>
            </div>
          </div>
        </div>

        <div className="flex flex-col">
          <div className="flex min-h-[36px] w-full items-center gap-2 rounded py-2 pl-2 pr-5">
            <span className="flex-1 font-inter text-sm font-normal leading-5 tracking-[0.25px] text-sidebar-text">
              Conversation: Coffee Buying
            </span>
          </div>

          <div className="flex flex-col">
            <div className="flex min-h-[36px] w-full items-center gap-2 rounded py-2 pl-5 pr-5">
              <span className="flex-1 font-inter text-sm font-normal leading-5 tracking-[0.25px] text-sidebar-text">
                Agentic Patterns
              </span>
            </div>

            <div>
              <SidebarDropdown
                title="Publish Subscribe"
                isExpanded={isPublishSubscribeExpanded}
                onToggle={handlePublishSubscribeToggle}
              >
                <SidebarItem
                  title={`A2A ${transport}`}
                  isSelected={selectedPattern === PATTERNS.PUBLISH_SUBSCRIBE}
                  onClick={() => onPatternChange(PATTERNS.PUBLISH_SUBSCRIBE)}
                />
              </SidebarDropdown>
            </div>

            <div>
              <SidebarDropdown
                title="Publish Subscribe: Streaming"
                isExpanded={isPublishSubscribeStreamingExpanded}
                onToggle={handlePublishSubscribeStreamingToggle}
              >
                <SidebarItem
                  title={`A2A ${transport}`}
                  isSelected={
                    selectedPattern === PATTERNS.PUBLISH_SUBSCRIBE_STREAMING
                  }
                  onClick={() =>
                    onPatternChange(PATTERNS.PUBLISH_SUBSCRIBE_STREAMING)
                  }
                />
              </SidebarDropdown>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default Sidebar
