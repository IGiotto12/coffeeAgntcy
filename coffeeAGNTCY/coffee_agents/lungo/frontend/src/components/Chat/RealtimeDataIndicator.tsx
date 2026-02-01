/**
 * Copyright AGNTCY Contributors (https://github.com/agntcy)
 * SPDX-License-Identifier: Apache-2.0
 **/

import React, { useState, useEffect } from "react"

const DEFAULT_EXCHANGE_APP_API_URL = "http://127.0.0.1:8000"
const EXCHANGE_APP_API_URL =
    import.meta.env.VITE_EXCHANGE_APP_API_URL || DEFAULT_EXCHANGE_APP_API_URL

interface RealtimeTrackerStats {
    total_records: number
    farms_with_data: number
    records_per_farm: Record<string, number>
}

interface PerformanceMetricsResponse {
    realtime_tracker_stats: RealtimeTrackerStats
    timestamp: string
}

const RealtimeDataIndicator: React.FC = () => {
    const [stats, setStats] = useState<RealtimeTrackerStats | null>(null)
    const [lastUpdate, setLastUpdate] = useState<number>(0)
    const [isVisible, setIsVisible] = useState<boolean>(false)
    const [hasNewData, setHasNewData] = useState<boolean>(false)

    useEffect(() => {
        let intervalId: NodeJS.Timeout
        let previousTotalRecords = 0

        const fetchStats = async () => {
            try {
                const response = await fetch(
                    `${EXCHANGE_APP_API_URL}/agent/performance-metrics?use_realtime=true`
                )
                if (!response.ok) return

                const data: PerformanceMetricsResponse = await response.json()
                const currentStats = data.realtime_tracker_stats

                if (currentStats) {
                    // Check if we have new data
                    if (currentStats.total_records > previousTotalRecords) {
                        setHasNewData(true)
                        setIsVisible(true)
                        // Hide after 5 seconds
                        setTimeout(() => {
                            setHasNewData(false)
                        }, 5000)
                    }

                    setStats(currentStats)
                    setLastUpdate(Date.now())
                    previousTotalRecords = currentStats.total_records

                    // Show indicator if we have data
                    if (currentStats.total_records > 0) {
                        setIsVisible(true)
                    }
                }
            } catch (error) {
                // Silently fail - API might not be available
            }
        }

        // Initial fetch
        fetchStats()

        // Poll every 3 seconds
        intervalId = setInterval(fetchStats, 3000)

        return () => {
            if (intervalId) clearInterval(intervalId)
        }
    }, [])

    // Always show the indicator if we have data, or show a minimal version if API is available
    // Only hide if we explicitly have no data and haven't received any updates
    if (!stats && lastUpdate === 0) {
        // Don't show anything until we've tried to fetch at least once
        return null
    }

    // Show indicator even if no data yet (to indicate system is active)
    const displayStats = stats || { total_records: 0, farms_with_data: 0, records_per_farm: {} }
    
    return (
        <div
            className={`fixed bottom-20 right-4 z-50 rounded-lg border bg-background p-3 shadow-lg transition-all duration-300 ${
                hasNewData
                    ? "border-green-500 bg-green-50 dark:bg-green-900/20"
                    : displayStats.total_records > 0
                    ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                    : "border-border bg-background/80"
            }`}
            style={{ maxWidth: "280px" }}
        >
            <div className="flex items-center gap-2">
                <div
                    className={`h-2 w-2 rounded-full ${
                        hasNewData 
                            ? "animate-pulse bg-green-500" 
                            : displayStats.total_records > 0
                            ? "bg-blue-500"
                            : "bg-gray-400"
                    }`}
                />
                <div className="flex-1">
                    <div className="text-xs font-semibold text-foreground">
                        {hasNewData ? "📊 New Data Collected!" : "📊 Real-time Tracking"}
                    </div>
                    <div className="text-xs text-muted-foreground">
                        {displayStats.total_records > 0 ? (
                            <>
                                {displayStats.total_records} request{displayStats.total_records !== 1 ? "s" : ""}{" "}
                                tracked
                                {displayStats.farms_with_data > 0 && (
                                    <span> • {displayStats.farms_with_data} farm{displayStats.farms_with_data !== 1 ? "s" : ""}</span>
                                )}
                            </>
                        ) : (
                            "Waiting for data..."
                        )}
                    </div>
                </div>
            </div>
            {hasNewData && (
                <div className="mt-2 text-xs text-green-600 dark:text-green-400">
                    Performance metrics updated
                </div>
            )}
        </div>
    )
}

export default RealtimeDataIndicator
