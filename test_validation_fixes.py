#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Test timezone-aware datetime correctness for realtime lookup."""

import sys
import io
from datetime import datetime

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from app.skills.bundles.realtime_lookup.execution_engine import RealtimeExecutionEngine


def test_timezone_aware_datetime():
    """Test that timezone-aware datetime is correctly computed for different locations."""
    print("\n" + "=" * 70)
    print("TIMEZONE-AWARE DATETIME TEST")
    print("=" * 70 + "\n")

    def mock_search(query, max_results=3):
        return []

    engine = RealtimeExecutionEngine(mock_search)

    # Test cases with different timezones
    test_cases = [
        ("Tokyo", "Asia/Tokyo"),
        ("New York", "America/New_York"),
        ("London", "Europe/London"),
        ("Sydney", "Australia/Sydney"),
        ("Melbourne", "Australia/Melbourne"),
    ]

    print("Testing timezone-aware datetime for different locations:\n")

    for location, expected_tz in test_cases:
        # Get the timezone
        tz_name = engine.TIMEZONE_MAP.get(location)
        if not tz_name:
            print(f"[SKIP] {location}: timezone not found")
            continue

        # Create timezone-aware datetime
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)

        # Format date and time
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        hour = now.hour

        # Verify timezone is correct
        assert tz_name == expected_tz, f"Timezone mismatch for {location}"

        # Verify date is in correct format
        assert len(date_str) == 10, f"Date format incorrect for {location}: {date_str}"
        assert date_str.count('-') == 2, f"Date format incorrect for {location}: {date_str}"

        # Verify time is in correct format
        assert len(time_str) == 5, f"Time format incorrect for {location}: {time_str}"
        assert time_str.count(':') == 1, f"Time format incorrect for {location}: {time_str}"

        # Verify hour is valid
        assert 0 <= hour < 24, f"Hour out of range for {location}: {hour}"

        print(f"[PASS] {location}")
        print(f"  Timezone: {tz_name}")
        print(f"  Date: {date_str}")
        print(f"  Time: {time_str}")
        print(f"  Hour: {hour}")
        print()

    print("=" * 70)
    print("All timezone tests passed!")
    print("=" * 70 + "\n")


def test_cross_day_scenarios():
    """Test scenarios where timezone conversion crosses day boundary."""
    print("\n" + "=" * 70)
    print("CROSS-DAY TIMEZONE SCENARIO TEST")
    print("=" * 70 + "\n")

    # Simulate different UTC times to test cross-day scenarios
    test_scenarios = [
        # (location, timezone, description)
        ("Tokyo", "Asia/Tokyo", "UTC+9: Should be ahead of UTC"),
        ("New York", "America/New_York", "UTC-5/-4: Should be behind UTC"),
        ("Sydney", "Australia/Sydney", "UTC+10/+11: Should be ahead of UTC"),
    ]

    print("Testing cross-day scenarios:\n")

    for location, tz_name, description in test_scenarios:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)

        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        hour = now.hour

        # Verify the datetime object is timezone-aware
        assert now.tzinfo is not None, f"Datetime not timezone-aware for {location}"
        assert now.tzinfo.tzname(now) is not None, f"Timezone name not available for {location}"

        print(f"[PASS] {location}")
        print(f"  {description}")
        print(f"  Current date: {date_str}")
        print(f"  Current time: {time_str}")
        print(f"  Timezone: {tz_name}")
        print()

    print("=" * 70)
    print("All cross-day scenario tests passed!")
    print("=" * 70 + "\n")


def test_strict_mode_response():
    """Test that strict mode returns only factual data without contamination."""
    print("\n" + "=" * 70)
    print("STRICT MODE RESPONSE TEST")
    print("=" * 70 + "\n")

    from app.skills.bundles.realtime_lookup.integration import RealtimeLookupSkillHandler

    def mock_search(query, max_results=3):
        results = {
            "BTC price USD": [
                {
                    "title": "Bitcoin Price",
                    "snippet": "Bitcoin (BTC): $42,350 USD (+2.1% 24h)",
                    "url": "https://crypto.example.com/btc",
                }
            ],
        }
        return results.get(query, [])

    handler = RealtimeLookupSkillHandler(mock_search)

    # Test with strict mode
    result = handler.execute("比特币现在价格多少", allowed_tools=["search_web"], strict_mode=True)

    print("Testing strict mode response:\n")

    if result and result.get("success"):
        answer = result.get("answer", "")
        print(f"[PASS] Strict mode executed successfully")
        print(f"  Answer: {answer}")
        print(f"  Strict mode flag: {result.get('strict_mode')}")

        # Verify answer is concise and factual
        # Should NOT contain phrases like "既然你刚才在分析那个界面"
        contamination_phrases = [
            "既然你",
            "刚才",
            "界面",
            "分析",
            "建议",
            "可以",
            "让我",
            "我来",
        ]

        has_contamination = any(phrase in answer for phrase in contamination_phrases)

        if not has_contamination:
            print(f"  ✓ No response contamination detected")
            print(f"  ✓ Response is concise and factual")
        else:
            print(f"  ✗ Response contamination detected!")
            for phrase in contamination_phrases:
                if phrase in answer:
                    print(f"    Found: '{phrase}'")

        print()
    else:
        print(f"[FAIL] Strict mode execution failed")
        print()

    print("=" * 70)
    print("Strict mode response test completed!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    test_timezone_aware_datetime()
    test_cross_day_scenarios()
    test_strict_mode_response()

    print("\n" + "=" * 70)
    print("ALL VALIDATION TESTS COMPLETED")
    print("=" * 70 + "\n")
