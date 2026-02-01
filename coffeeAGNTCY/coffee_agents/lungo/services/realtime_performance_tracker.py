# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Real-time Performance Tracker
Tracks performance metrics in memory as requests happen.
This provides immediate feedback without waiting for ClickHouse data.
"""

import logging
import time
from typing import Dict, List, Optional, Any
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from services.performance_analyzer import FarmPerformanceMetrics

logger = logging.getLogger("lungo.realtime_tracker")


@dataclass
class RequestRecord:
    """Record of a single request"""
    farm_name: str
    response_time: float  # seconds
    success: bool
    timestamp: float = field(default_factory=time.time)


class RealTimePerformanceTracker:
    """
    Tracks performance in real-time by collecting data as requests happen.
    Combines with historical data from ClickHouse for best results.
    """
    
    def __init__(self, max_records_per_farm: int = 100):
        """
        Args:
            max_records_per_farm: Maximum number of records to keep per farm (FIFO)
        """
        self.max_records_per_farm = max_records_per_farm
        self._records: Dict[str, List[RequestRecord]] = defaultdict(list)
        self._lock = False  # Simple flag for thread safety (async-safe in Python)
    
    def record_request(self, farm_name: str, response_time: float, success: bool):
        """
        Record a request result.
        
        Args:
            farm_name: Farm name (brazil, colombia, vietnam)
            response_time: Response time in seconds
            success: Whether the request was successful
        """
        farm_lower = farm_name.lower()
        record = RequestRecord(
            farm_name=farm_lower,
            response_time=response_time,
            success=success,
            timestamp=time.time()
        )
        
        self._records[farm_lower].append(record)
        
        # Keep only recent records (FIFO)
        if len(self._records[farm_lower]) > self.max_records_per_farm:
            self._records[farm_lower] = self._records[farm_lower][-self.max_records_per_farm:]
        
        # Calculate new average after adding this record
        total_records = len(self._records[farm_lower])
        avg_time = sum(r.response_time for r in self._records[farm_lower]) / total_records if total_records > 0 else 0
        
        # Log with INFO level so it's visible - shows new data is captured and average is recalculated
        logger.info(
            f"📊 Real-time Tracker: Captured new response for {farm_lower} | "
            f"Response time: {response_time:.2f}s | Success: {success} | "
            f"Total records: {total_records} | New average: {avg_time:.2f}s"
        )
    
    def get_metrics(self, farm_name: str) -> Optional[FarmPerformanceMetrics]:
        """
        Get performance metrics for a farm based on real-time data.
        Metrics are recalculated each time this is called with the latest data.
        
        Args:
            farm_name: Farm name (brazil, colombia, vietnam)
            
        Returns:
            FarmPerformanceMetrics or None if no data
        """
        farm_lower = farm_name.lower()
        records = self._records.get(farm_lower, [])
        
        if not records:
            return None
        
        # Calculate statistics (recalculated each time with latest data)
        response_times = [r.response_time for r in records]
        response_times.sort()
        
        total = len(records)
        successful = sum(1 for r in records if r.success)
        failed = total - successful
        
        avg_rt = sum(response_times) / total if total > 0 else 0
        p50_rt = response_times[int(total * 0.5)] if total > 0 else avg_rt
        p95_rt = response_times[int(total * 0.95)] if total > 1 else response_times[-1] if total > 0 else avg_rt
        p99_rt = response_times[int(total * 0.99)] if total > 1 else response_times[-1] if total > 0 else avg_rt
        
        success_rate = successful / total if total > 0 else 0.5
        
        # Calculate stability (inverse of coefficient of variation)
        if avg_rt > 0:
            cv = (p95_rt - p50_rt) / avg_rt if avg_rt > 0 else 0
            stability_score = max(0.0, min(1.0, 1.0 - cv))
        else:
            stability_score = 0.5
        
        # Recommended timeout (P95 + 30% buffer)
        recommended_timeout = max(1.0, min(10.0, p95_rt * 1.3))
        
        # Log when metrics are recalculated (for visibility)
        logger.debug(
            f"📈 Recalculated metrics for {farm_lower}: "
            f"avg={avg_rt:.2f}s, p95={p95_rt:.2f}s, timeout={recommended_timeout:.2f}s "
            f"(based on {total} records)"
        )
        
        return FarmPerformanceMetrics(
            farm_name=farm_lower.title(),
            avg_response_time=round(avg_rt, 2),
            p50_response_time=round(p50_rt, 2),
            p95_response_time=round(p95_rt, 2),
            p99_response_time=round(p99_rt, 2),
            success_rate=round(success_rate, 3),
            total_requests=total,
            successful_requests=successful,
            failed_requests=failed,
            stability_score=round(stability_score, 3),
            last_updated=datetime.now(timezone.utc).isoformat(),
            recommended_timeout=round(recommended_timeout, 2)
        )
    
    def get_all_metrics(self) -> Dict[str, FarmPerformanceMetrics]:
        """Get metrics for all farms"""
        metrics = {}
        for farm in ['brazil', 'colombia', 'vietnam']:
            metric = self.get_metrics(farm)
            if metric:
                metrics[farm] = metric
        return metrics
    
    def get_stats_summary(self) -> Dict[str, Any]:
        """Get summary statistics"""
        return {
            "total_records": sum(len(records) for records in self._records.values()),
            "farms_with_data": len([f for f in ['brazil', 'colombia', 'vietnam'] if f in self._records]),
            "records_per_farm": {farm: len(records) for farm, records in self._records.items()}
        }


# Singleton instance
_realtime_tracker: Optional[RealTimePerformanceTracker] = None


def get_realtime_tracker() -> RealTimePerformanceTracker:
    """Get or create the singleton RealTimePerformanceTracker instance"""
    global _realtime_tracker
    if _realtime_tracker is None:
        _realtime_tracker = RealTimePerformanceTracker()
    return _realtime_tracker
