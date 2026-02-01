#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple test script to demonstrate Performance Analyzer
Run this to see the performance metrics without starting the full server
"""

import json
import sys
import os

# Fix Windows console encoding
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from services.performance_analyzer import get_performance_analyzer

def main():
    print("=" * 60)
    print("Performance Analyzer Demo")
    print("=" * 60)
    print()
    
    analyzer = get_performance_analyzer()
    
    print("Fetching performance metrics...")
    summary = analyzer.get_metrics_summary()
    
    print(f"\n📊 Performance Metrics Summary")
    print(f"Timestamp: {summary['timestamp']}")
    print(f"Cache Age: {summary['cache_age_seconds']:.1f} seconds")
    print()
    
    print("🏭 Farm Performance:")
    print("-" * 60)
    
    for farm_name, metrics in summary['farms'].items():
        print(f"\n{farm_name.upper()}:")
        print(f"  ⏱️  Average Response Time: {metrics['avg_response_time']}s")
        print(f"  📈 P50 Response Time: {metrics['p50_response_time']}s")
        print(f"  📈 P95 Response Time: {metrics['p95_response_time']}s")
        print(f"  📈 P99 Response Time: {metrics['p99_response_time']}s")
        print(f"  ✅ Success Rate: {metrics['success_rate']*100:.1f}%")
        print(f"  📊 Total Requests: {metrics['total_requests']}")
        print(f"  ✅ Successful: {metrics['successful_requests']}")
        print(f"  ❌ Failed: {metrics['failed_requests']}")
        print(f"  🎯 Stability Score: {metrics['stability_score']:.2f}")
        print(f"  ⏰ Recommended Timeout: {metrics['recommended_timeout']}s")
    
    print("\n" + "=" * 60)
    print("💡 Key Insights:")
    print("-" * 60)
    
    # Find fastest and slowest farms
    farms = summary['farms']
    fastest = min(farms.items(), key=lambda x: x[1]['avg_response_time'])
    slowest = max(farms.items(), key=lambda x: x[1]['avg_response_time'])
    most_stable = max(farms.items(), key=lambda x: x[1]['stability_score'])
    
    print(f"🚀 Fastest Farm: {fastest[0].title()} ({fastest[1]['avg_response_time']}s avg)")
    print(f"🐌 Slowest Farm: {slowest[0].title()} ({slowest[1]['avg_response_time']}s avg)")
    print(f"⭐ Most Stable: {most_stable[0].title()} (score: {most_stable[1]['stability_score']:.2f})")
    
    print("\n📋 Recommended Timeouts for Scout Agent:")
    for farm_name, metrics in farms.items():
        print(f"  - {farm_name.title()}: {metrics['recommended_timeout']}s (based on P95: {metrics['p95_response_time']}s)")
    
    print("\n" + "=" * 60)
    print("✅ Demo Complete!")
    print("=" * 60)
    
    # Also print as JSON for API testing
    print("\n📄 JSON Output (for API testing):")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
