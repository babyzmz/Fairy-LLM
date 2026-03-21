#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Quick integration test for realtime-lookup skill."""

import sys
import io

# Fix encoding for Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.skills.bundles.realtime_lookup.executor import RealtimeQueryAnalyzer, RealtimeExecutor
from app.skills.bundles.realtime_lookup.integration import RealtimeLookupSkillHandler


def mock_search(query: str, max_results: int = 3) -> list[dict]:
    """Mock search_web tool with predefined results."""
    results = {
        "墨尔本 weather today": [
            {
                "title": "Melbourne Weather",
                "snippet": "Melbourne today: Partly cloudy, 18°C - 24°C, wind 15 km/h",
            }
        ],
        "Forest Hill petrol price today": [
            {
                "title": "Fuel Prices",
                "snippet": "Forest Hill 98 RON: $1.89/L as of today",
            }
        ],
        "current time 东京": [
            {
                "title": "Tokyo Time",
                "snippet": "Tokyo: 2026-03-20 14:35 JST (Friday)",
            }
        ],
        "BTC price USD today": [
            {
                "title": "Bitcoin Price",
                "snippet": "Bitcoin (BTC): $42,350 USD (+2.1% 24h)",
            }
        ],
        "NVDA stock price today": [
            {
                "title": "NVIDIA Stock",
                "snippet": "NVIDIA (NVDA): $875.42 (+2.1% today)",
            }
        ],
        "USD to AUD exchange rate": [
            {
                "title": "Exchange Rate",
                "snippet": "USD to AUD: 1.52 (as of today)",
            }
        ],
    }
    return results.get(query, [])


def test_analyzer():
    """Test query analyzer for all 6 failing cases."""
    print("\n" + "=" * 70)
    print("TESTING QUERY ANALYZER")
    print("=" * 70)

    analyzer = RealtimeQueryAnalyzer()
    test_cases = [
        ("帮我联网查墨尔本今天的天气", "weather", "墨尔本"),
        ("Forest Hill 附近 98 号油价多少", "fuel", "Forest Hill"),
        ("现在东京几点", "time", "东京"),
        ("比特币现在价格多少", "crypto", "BTC"),
        ("英伟达现在股价多少", "stock", "NVDA"),
        ("美元兑澳元多少", "exchange", None),
    ]

    passed = 0
    for query, expected_intent, expected_entity in test_cases:
        intent, entity = analyzer.analyze(query)
        status = "[PASS]" if intent == expected_intent else "[FAIL]"
        print(f"{status} Query: {query}")
        print(f"  Intent: {intent} (expected: {expected_intent})")
        if entity:
            print(f"  Entity: {entity.primary}")
        if intent == expected_intent:
            passed += 1
        print()

    print(f"Analyzer: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


def test_executor():
    """Test executor for all 6 failing cases."""
    print("=" * 70)
    print("TESTING EXECUTOR")
    print("=" * 70)

    executor = RealtimeExecutor(mock_search)
    test_cases = [
        ("帮我联网查墨尔本今天的天气", "18°C"),
        ("Forest Hill 附近 98 号油价多少", "$1.89"),
        ("现在东京几点", "14:35"),
        ("比特币现在价格多少", "$42,350"),
        ("英伟达现在股价多少", "$875.42"),
        ("美元兑澳元多少", "1.52"),
    ]

    passed = 0
    for query, expected_snippet in test_cases:
        result = executor.execute(query)
        success = result and result.get("success") and expected_snippet in result.get("answer", "")
        status = "[PASS]" if success else "[FAIL]"
        print(f"{status} Query: {query}")
        if result:
            print(f"  Success: {result.get('success')}")
            print(f"  Answer: {result.get('answer', 'N/A')[:60]}")
        else:
            print(f"  Result: None")
        if success:
            passed += 1
        print()

    print(f"Executor: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


def test_handler():
    """Test integration handler."""
    print("=" * 70)
    print("TESTING INTEGRATION HANDLER")
    print("=" * 70)

    handler = RealtimeLookupSkillHandler(mock_search)
    test_cases = [
        ("帮我联网查墨尔本今天的天气", "18°C"),
        ("比特币现在价格多少", "$42,350"),
        ("现在东京几点", "14:35"),
    ]

    passed = 0
    for query, expected_snippet in test_cases:
        result = handler.execute(query, allowed_tools=["search_web"])
        success = result and result.get("success") and expected_snippet in result.get("answer", "")
        status = "[PASS]" if success else "[FAIL]"
        print(f"{status} Query: {query}")
        print(f"  Success: {result.get('success')}")
        print(f"  Answer: {result.get('answer', 'N/A')[:60]}")
        if success:
            passed += 1
        print()

    print(f"Handler: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("REALTIME LOOKUP INTEGRATION TEST")
    print("=" * 70)

    analyzer_ok = test_analyzer()
    executor_ok = test_executor()
    handler_ok = test_handler()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Analyzer: {'PASS' if analyzer_ok else 'FAIL'}")
    print(f"Executor: {'PASS' if executor_ok else 'FAIL'}")
    print(f"Handler:  {'PASS' if handler_ok else 'FAIL'}")
    print(f"\nOverall: {'[ALL TESTS PASSED]' if all([analyzer_ok, executor_ok, handler_ok]) else '[SOME TESTS FAILED]'}")
    print("=" * 70 + "\n")
