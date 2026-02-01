#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple script to generate trace data by running multiple prompts
This will create historical data in ClickHouse for Performance Analyzer
"""

import asyncio
import requests
import time
import json

# Prompts that will trigger scout agent (to generate farm probe traces)
TEST_PROMPTS = [
    "I need 50 lbs of coffee at $0.50 per lb. Which farm can fulfill this quickly?",
    "Can you find me 100 lbs of coffee at $0.55/lb? I need it fast.",
    "I want to order 75 lbs at $0.48/lb. Check all farms and pick the best one.",
    "Need 150 lbs of coffee at $0.52 per pound. Which farms are available right now?",
    "I prefer Colombia, but check all farms for 80 lbs at $0.50/lb availability",
    "Find me 200 lbs of coffee at best price from any farm",
    "I need 120 lbs at $0.45/lb, check all farms",
    "Which farm can supply 90 lbs at $0.50/lb fastest?",
    "Check availability for 110 lbs at $0.48/lb from all farms",
    "I need 130 lbs of coffee, find the best option from all farms"
]

API_URL = "http://localhost:8000/agent/prompt"

def run_prompt(prompt: str, index: int):
    """Run a single prompt and return the response"""
    print(f"\n[{index+1}/{len(TEST_PROMPTS)}] Running: {prompt[:50]}...")
    
    try:
        response = requests.post(
            API_URL,
            json={"prompt": prompt},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Success - Response: {result.get('response', '')[:100]}...")
            return True
        else:
            print(f"❌ Error - Status: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Exception: {e}")
        return False

def main():
    print("=" * 60)
    print("Generating Trace Data for Performance Analyzer")
    print("=" * 60)
    print(f"\nWill run {len(TEST_PROMPTS)} prompts to generate historical data")
    print("This will create scout probe traces in ClickHouse")
    print("\nMake sure Auction Supervisor is running on http://localhost:8000")
    print("\nStarting in 3 seconds...")
    time.sleep(3)
    
    success_count = 0
    failed_count = 0
    
    for i, prompt in enumerate(TEST_PROMPTS):
        success = run_prompt(prompt, i)
        if success:
            success_count += 1
        else:
            failed_count += 1
        
        # Wait a bit between requests to avoid overwhelming the system
        if i < len(TEST_PROMPTS) - 1:
            time.sleep(2)
    
    print("\n" + "=" * 60)
    print("Summary:")
    print(f"✅ Successful: {success_count}")
    print(f"❌ Failed: {failed_count}")
    print("=" * 60)
    
    if success_count > 0:
        print("\n✅ Trace data generated!")
        print("Wait 10-20 seconds for traces to be written to ClickHouse")
        print("Then run: python test_performance_analyzer.py")
        print("Or check: http://localhost:8000/agent/performance-metrics")
    else:
        print("\n❌ No data generated. Check if Auction Supervisor is running.")

if __name__ == "__main__":
    main()
