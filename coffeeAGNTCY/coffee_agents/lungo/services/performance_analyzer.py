# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Performance Analyzer Service
Analyzes historical trace data from ClickHouse to provide performance metrics for farms.
"""

import logging
import time
from typing import Dict, Optional, Any
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict

try:
    from clickhouse_connect import get_client
    CLICKHOUSE_AVAILABLE = True
except ImportError:
    CLICKHOUSE_AVAILABLE = False
    logging.warning("clickhouse-connect not available. Performance analyzer will use mock data.")

from config.config import (
    CLICKHOUSE_HOST,
    CLICKHOUSE_PORT,
    CLICKHOUSE_USER,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_DATABASE,
    PERFORMANCE_CACHE_TTL,
)

logger = logging.getLogger("lungo.performance_analyzer")


@dataclass
class FarmPerformanceMetrics:
    """Performance metrics for a single farm"""
    farm_name: str
    avg_response_time: float  # seconds
    p50_response_time: float
    p95_response_time: float
    p99_response_time: float
    success_rate: float  # 0.0 to 1.0
    total_requests: int
    successful_requests: int
    failed_requests: int
    stability_score: float  # 0.0 to 1.0 (based on variance)
    last_updated: str
    recommended_timeout: float  # seconds (calculated based on p95)


class PerformanceAnalyzer:
    """Analyzes performance data from ClickHouse traces"""
    
    def __init__(self):
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[float] = None
        self._client = None
        
        if CLICKHOUSE_AVAILABLE:
            try:
                self._client = get_client(
                    host=CLICKHOUSE_HOST,
                    port=CLICKHOUSE_PORT,
                    username=CLICKHOUSE_USER,
                    password=CLICKHOUSE_PASSWORD,
                    database=CLICKHOUSE_DATABASE
                )
                logger.info("ClickHouse client initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize ClickHouse client: {e}. Using mock data.")
                self._client = None
        else:
            logger.warning("ClickHouse client not available. Using mock data.")
    
    def get_farm_performance(self, force_refresh: bool = False, use_realtime: bool = True) -> Dict[str, FarmPerformanceMetrics]:
        """
        Get performance metrics for all farms.
        Combines historical data (ClickHouse) with real-time data (in-memory).
        
        Args:
            force_refresh: If True, bypass cache and fetch fresh data
            use_realtime: If True, combine with real-time tracker data
            
        Returns:
            Dictionary mapping farm names to performance metrics
        """
        # Try to get real-time data first (for hackathon demo - immediate feedback)
        if use_realtime:
            try:
                from services.realtime_performance_tracker import get_realtime_tracker
                realtime_tracker = get_realtime_tracker()
                realtime_metrics = realtime_tracker.get_all_metrics()
                
                # If we have real-time data, use it (for immediate demo effect)
                if realtime_metrics:
                    logger.info(f"Using real-time performance data for {len(realtime_metrics)} farms")
                    # Merge with historical data if available
                    historical_metrics = self._get_historical_metrics(force_refresh)
                    return self._merge_metrics(realtime_metrics, historical_metrics)
            except Exception as e:
                logger.debug(f"Real-time tracker not available: {e}")
        
        # Fall back to historical data (ClickHouse)
        # Check cache
        if not force_refresh and self._cache and self._cache_timestamp:
            age = time.time() - self._cache_timestamp
            if age < PERFORMANCE_CACHE_TTL:
                logger.debug(f"Returning cached performance data (age: {age:.1f}s)")
                return self._cache
        
        # Fetch fresh data
        logger.info("Fetching fresh performance data from ClickHouse")
        metrics = self._fetch_performance_metrics()
        
        # Update cache
        self._cache = metrics
        self._cache_timestamp = time.time()
        
        return metrics
    
    def _get_historical_metrics(self, force_refresh: bool = False) -> Dict[str, FarmPerformanceMetrics]:
        """Get historical metrics from cache or ClickHouse"""
        if not force_refresh and self._cache and self._cache_timestamp:
            age = time.time() - self._cache_timestamp
            if age < PERFORMANCE_CACHE_TTL:
                return self._cache
        
        return self._fetch_performance_metrics()
    
    def _merge_metrics(self, realtime: Dict[str, FarmPerformanceMetrics], 
                      historical: Dict[str, FarmPerformanceMetrics]) -> Dict[str, FarmPerformanceMetrics]:
        """
        Merge real-time and historical metrics.
        Prefer real-time for recent data, but use historical for farms without real-time data.
        """
        merged = {}
        
        # Start with historical data
        merged.update(historical)
        
        # Override with real-time data (more recent)
        for farm, metric in realtime.items():
            # Weighted average: 70% real-time, 30% historical (if available)
            if farm in historical:
                hist = historical[farm]
                # Weighted average of response times
                merged[farm] = FarmPerformanceMetrics(
                    farm_name=metric.farm_name,
                    avg_response_time=round(metric.avg_response_time * 0.7 + hist.avg_response_time * 0.3, 2),
                    p50_response_time=round(metric.p50_response_time * 0.7 + hist.p50_response_time * 0.3, 2),
                    p95_response_time=round(metric.p95_response_time * 0.7 + hist.p95_response_time * 0.3, 2),
                    p99_response_time=round(metric.p99_response_time * 0.7 + hist.p99_response_time * 0.3, 2),
                    success_rate=round(metric.success_rate * 0.7 + hist.success_rate * 0.3, 3),
                    total_requests=metric.total_requests + hist.total_requests,
                    successful_requests=metric.successful_requests + hist.successful_requests,
                    failed_requests=metric.failed_requests + hist.failed_requests,
                    stability_score=round(metric.stability_score * 0.7 + hist.stability_score * 0.3, 3),
                    last_updated=metric.last_updated,  # Use real-time timestamp
                    recommended_timeout=round(metric.recommended_timeout * 0.7 + hist.recommended_timeout * 0.3, 2)
                )
            else:
                # No historical data, use real-time only
                merged[farm] = metric
        
        return merged
    
    def _fetch_performance_metrics(self) -> Dict[str, FarmPerformanceMetrics]:
        """Fetch performance metrics from ClickHouse or return mock data"""
        
        if not self._client:
            return self._get_mock_metrics()
        
        try:
            # Query ClickHouse for scout probe traces
            # Look for spans related to farm communication
            # We'll look for spans that have farm information in attributes or service names
            query = """
            SELECT 
                COALESCE(
                    JSONExtractString(SpanAttributes, 'farm_name'),
                    JSONExtractString(SpanAttributes, 'farm'),
                    CASE 
                        WHEN ServiceName LIKE '%brazil%' OR SpanAttributes LIKE '%brazil%' THEN 'brazil'
                        WHEN ServiceName LIKE '%colombia%' OR SpanAttributes LIKE '%colombia%' THEN 'colombia'
                        WHEN ServiceName LIKE '%vietnam%' OR SpanAttributes LIKE '%vietnam%' THEN 'vietnam'
                        ELSE ''
                    END
                ) as farm_name,
                avg(Duration / 1000000000.0) as avg_response_time_sec,
                quantile(0.5)(Duration / 1000000000.0) as p50_response_time_sec,
                quantile(0.95)(Duration / 1000000000.0) as p95_response_time_sec,
                quantile(0.99)(Duration / 1000000000.0) as p99_response_time_sec,
                count(*) as total_requests,
                countIf(StatusCode = 'OK' OR StatusCode = 1) as successful_requests,
                countIf(StatusCode != 'OK' AND StatusCode != 1) as failed_requests
            FROM default.otel_traces
            WHERE 
                (SpanName LIKE '%scout%' OR SpanName LIKE '%probe%' OR SpanName LIKE '%farm%')
                AND Timestamp > now() - INTERVAL 24 HOUR
                AND (
                    JSONExtractString(SpanAttributes, 'farm_name') != '' OR
                    JSONExtractString(SpanAttributes, 'farm') != '' OR
                    ServiceName LIKE '%brazil%' OR ServiceName LIKE '%colombia%' OR ServiceName LIKE '%vietnam%'
                )
            GROUP BY farm_name
            HAVING farm_name IN ('brazil', 'colombia', 'vietnam', 'Brazil', 'Colombia', 'Vietnam')
                AND farm_name != ''
            """
            
            result = self._client.query(query)
            
            metrics_dict = {}
            logger.info(f"Query returned {len(result.result_rows)} rows")
            
            for row in result.result_rows:
                farm_name = (row[0] or "").strip()
                if not farm_name:
                    continue
                    
                farm_name_lower = farm_name.lower()
                
                # Normalize farm names
                if farm_name_lower not in ['brazil', 'colombia', 'vietnam']:
                    logger.debug(f"Skipping unknown farm: {farm_name}")
                    continue
                
                avg_rt = float(row[1]) if row[1] else 2.0
                p50_rt = float(row[2]) if row[2] else avg_rt
                p95_rt = float(row[3]) if row[3] else avg_rt * 2
                p99_rt = float(row[4]) if row[4] else p95_rt * 1.2
                total = int(row[5]) if row[5] else 0
                success = int(row[6]) if row[6] else 0
                failed = int(row[7]) if row[7] else 0
                
                success_rate = success / total if total > 0 else 0.5
                
                # Calculate stability score (inverse of coefficient of variation)
                # Lower variance = higher stability
                if avg_rt > 0:
                    cv = (p95_rt - p50_rt) / avg_rt  # Coefficient of variation approximation
                    stability_score = max(0.0, min(1.0, 1.0 - cv))
                else:
                    stability_score = 0.5
                
                # Calculate recommended timeout (P95 + 30% buffer, min 1s, max 10s)
                recommended_timeout = max(1.0, min(10.0, p95_rt * 1.3))
                
                metrics_dict[farm_name_lower] = FarmPerformanceMetrics(
                    farm_name=farm_name_lower.title(),
                    avg_response_time=round(avg_rt, 2),
                    p50_response_time=round(p50_rt, 2),
                    p95_response_time=round(p95_rt, 2),
                    p99_response_time=round(p99_rt, 2),
                    success_rate=round(success_rate, 3),
                    total_requests=total,
                    successful_requests=success,
                    failed_requests=failed,
                    stability_score=round(stability_score, 3),
                    last_updated=datetime.now(timezone.utc).isoformat(),
                    recommended_timeout=round(recommended_timeout, 2)
                )
            
            # Ensure all farms are present (fill with defaults if missing)
            for farm in ['brazil', 'colombia', 'vietnam']:
                if farm not in metrics_dict:
                    metrics_dict[farm] = self._create_default_metrics(farm)
            
            logger.info(f"Fetched performance metrics for {len(metrics_dict)} farms")
            return metrics_dict
            
        except Exception as e:
            logger.error(f"Error fetching performance metrics from ClickHouse: {e}")
            logger.info("Falling back to mock data")
            return self._get_mock_metrics()
    
    def _get_mock_metrics(self) -> Dict[str, FarmPerformanceMetrics]:
        """Return mock performance metrics for demonstration"""
        logger.info("Using mock performance metrics")
        
        return {
            "brazil": FarmPerformanceMetrics(
                farm_name="Brazil",
                avg_response_time=1.2,
                p50_response_time=1.0,
                p95_response_time=2.0,
                p99_response_time=3.0,
                success_rate=0.95,
                total_requests=100,
                successful_requests=95,
                failed_requests=5,
                stability_score=0.85,
                last_updated=datetime.now(timezone.utc).isoformat(),
                recommended_timeout=2.6
            ),
            "colombia": FarmPerformanceMetrics(
                farm_name="Colombia",
                avg_response_time=1.8,
                p50_response_time=1.5,
                p95_response_time=3.0,
                p99_response_time=4.5,
                success_rate=0.92,
                total_requests=120,
                successful_requests=110,
                failed_requests=10,
                stability_score=0.78,
                last_updated=datetime.now(timezone.utc).isoformat(),
                recommended_timeout=3.9
            ),
            "vietnam": FarmPerformanceMetrics(
                farm_name="Vietnam",
                avg_response_time=3.5,
                p50_response_time=3.0,
                p95_response_time=6.0,
                p99_response_time=8.0,
                success_rate=0.88,
                total_requests=80,
                successful_requests=70,
                failed_requests=10,
                stability_score=0.65,
                last_updated=datetime.now(timezone.utc).isoformat(),
                recommended_timeout=7.8
            )
        }
    
    def _create_default_metrics(self, farm_name: str) -> FarmPerformanceMetrics:
        """Create default metrics for a farm with no data"""
        return FarmPerformanceMetrics(
            farm_name=farm_name.title(),
            avg_response_time=2.0,
            p50_response_time=1.8,
            p95_response_time=4.0,
            p99_response_time=6.0,
            success_rate=0.90,
            total_requests=0,
            successful_requests=0,
            failed_requests=0,
            stability_score=0.75,
            last_updated=datetime.now(timezone.utc).isoformat(),
            recommended_timeout=5.2
        )
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get a summary of all farm metrics for API response"""
        metrics = self.get_farm_performance()
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cache_age_seconds": time.time() - self._cache_timestamp if self._cache_timestamp else 0,
            "farms": {farm: asdict(metric) for farm, metric in metrics.items()}
        }


# Singleton instance
_performance_analyzer: Optional[PerformanceAnalyzer] = None


def get_performance_analyzer() -> PerformanceAnalyzer:
    """Get or create the singleton PerformanceAnalyzer instance"""
    global _performance_analyzer
    if _performance_analyzer is None:
        _performance_analyzer = PerformanceAnalyzer()
    return _performance_analyzer
