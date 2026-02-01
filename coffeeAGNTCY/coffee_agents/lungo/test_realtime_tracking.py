#!/usr/bin/env python3
# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Test script to verify real-time performance tracking is working.
Shows how new responses are captured and averages are recalculated.
"""

import sys
import time
import requests
from services.realtime_performance_tracker import get_realtime_tracker

def test_realtime_tracking():
    """Test that real-time tracking captures requests and recalculates averages"""
    
    print("=" * 70)
    print("Real-time Performance Tracker Test")
    print("=" * 70)
    print()
    
    tracker = get_realtime_tracker()
    
    # Simulate some requests
    print("📝 Simulating requests to farms...")
    print()
    
    # Simulate Brazil farm requests
    print("🇧🇷 Brazil Farm:")
    for i in range(3):
        response_time = 1.0 + (i * 0.2)  # 1.0s, 1.2s, 1.4s
        tracker.record_request("brazil", response_time, success=True)
        time.sleep(0.1)  # Small delay to see the logs
        metrics = tracker.get_metrics("brazil")
        if metrics:
            print(f"  Request {i+1}: {response_time:.2f}s → Avg: {metrics.avg_response_time:.2f}s (from {metrics.total_requests} records)")
    
    print()
    
    # Simulate Colombia farm requests
    print("🇨🇴 Colombia Farm:")
    for i in range(3):
        response_time = 1.5 + (i * 0.3)  # 1.5s, 1.8s, 2.1s
        tracker.record_request("colombia", response_time, success=True)
        time.sleep(0.1)
        metrics = tracker.get_metrics("colombia")
        if metrics:
            print(f"  Request {i+1}: {response_time:.2f}s → Avg: {metrics.avg_response_time:.2f}s (from {metrics.total_requests} records)")
    
    print()
    
    # Simulate Vietnam farm requests (some failures)
    print("🇻🇳 Vietnam Farm:")
    for i in range(4):
        response_time = 2.0 + (i * 0.5)  # 2.0s, 2.5s, 3.0s, 3.5s
        success = i < 3  # First 3 succeed, last one fails
        tracker.record_request("vietnam", response_time, success=success)
        time.sleep(0.1)
        metrics = tracker.get_metrics("vietnam")
        if metrics:
            print(f"  Request {i+1}: {response_time:.2f}s, Success: {success} → Avg: {metrics.avg_response_time:.2f}s, Success Rate: {metrics.success_rate:.1%} (from {metrics.total_requests} records)")
    
    print()
    print("=" * 70)
    print("📊 Final Summary")
    print("=" * 70)
    
    all_metrics = tracker.get_all_metrics()
    for farm, metrics in all_metrics.items():
        print(f"\n{farm.title()} Farm:")
        print(f"  Total Requests: {metrics.total_requests}")
        print(f"  Successful: {metrics.successful_requests}")
        print(f"  Failed: {metrics.failed_requests}")
        print(f"  Average Response Time: {metrics.avg_response_time:.2f}s")
        print(f"  P95 Response Time: {metrics.p95_response_time:.2f}s")
        print(f"  Success Rate: {metrics.success_rate:.1%}")
        print(f"  Recommended Timeout: {metrics.recommended_timeout:.2f}s")
    
    print()
    print("=" * 70)
    print("✅ Test Complete!")
    print("=" * 70)
    print()
    print("💡 Key Points:")
    print("  • Each request is automatically captured")
    print("  • Average is recalculated after each new request")
    print("  • Metrics update in real-time (no database needed)")
    print("  • Check the logs above to see '📊 Real-time Tracker' messages")
    print()


def test_api_endpoint():
    """Test the API endpoint to see real-time data"""
    print()
    print("=" * 70)
    print("Testing API Endpoint")
    print("=" * 70)
    print()
    
    try:
        response = requests.get("http://127.0.0.1:8000/agent/performance-metrics", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print("✅ API Endpoint is working!")
            print()
            print("Real-time Tracker Stats:")
            stats = data.get("realtime_tracker_stats", {})
            print(f"  Total Records: {stats.get('total_records', 0)}")
            print(f"  Farms with Data: {stats.get('farms_with_data', 0)}")
            print(f"  Records per Farm: {stats.get('records_per_farm', {})}")
            print()
            print("Farm Metrics:")
            farms = data.get("farms", {})
            for farm, metrics in farms.items():
                print(f"  {farm}: avg={metrics.get('avg_response_time', 0):.2f}s, "
                      f"records={metrics.get('total_requests', 0)}")
        else:
            print(f"❌ API returned status {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to API. Is the Auction Supervisor running?")
        print("   Start it with: docker compose up auction-supervisor")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    # Set UTF-8 encoding for Windows
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    
    test_realtime_tracking()
    test_api_endpoint()
