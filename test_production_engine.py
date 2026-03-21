#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Comprehensive tests for production realtime lookup engine."""

import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.skills.bundles.realtime_lookup.execution_engine import RealtimeExecutionEngine
from app.skills.bundles.realtime_lookup.subtype_classifier import SubtypeClassifier
from app.skills.bundles.realtime_lookup.numeric_extractor import NumericExtractor
from app.skills.bundles.realtime_lookup.query_generator import QueryGenerator
from app.skills.bundles.realtime_lookup.card_payload import CardPayloadGenerator


def mock_search(query: str, max_results: int = 3) -> list[dict]:
    """Mock search_web tool with realistic results."""
    results = {
        "墨尔本 weather today": [
            {
                "title": "Melbourne Weather",
                "snippet": "Melbourne today: Partly cloudy, 18°C - 24°C, wind 15 km/h",
                "url": "https://weather.example.com/melbourne",
            }
        ],
        "Forest Hill petrol price today": [
            {
                "title": "Fuel Prices",
                "snippet": "Forest Hill 98 RON: $1.78–$1.83 AUD/L as of today",
                "url": "https://fuel.example.com/forest-hill",
            }
        ],
        "BTC price USD": [
            {
                "title": "Bitcoin Price",
                "snippet": "Bitcoin (BTC): $42,350 USD (+2.1% 24h) - Updated 2026-03-20",
                "url": "https://crypto.example.com/btc",
            }
        ],
        "NVDA stock price today": [
            {
                "title": "NVIDIA Stock",
                "snippet": "NVIDIA (NVDA): $875.42 USD (+2.1% today) - Market cap: $2.15T",
                "url": "https://stock.example.com/nvda",
            }
        ],
        "USD to AUD exchange rate": [
            {
                "title": "Exchange Rate",
                "snippet": "USD to AUD: 1.5234 (as of 2026-03-20 14:35 UTC)",
                "url": "https://exchange.example.com",
            }
        ],
    }
    return results.get(query, [])


def test_subtype_classification():
    """Test subtype classification."""
    print("\n" + "=" * 70)
    print("TEST 1: SUBTYPE CLASSIFICATION")
    print("=" * 70)

    test_cases = [
        ("帮我联网查墨尔本今天的天气", "weather"),
        ("Forest Hill 附近 98 号油价多少", "fuel_price"),
        ("现在东京几点", "current_time"),
        ("纽约现在是白天还是晚上", "day_or_night"),
        ("比特币现在价格多少", "crypto_price"),
        ("英伟达现在股价多少", "stock_price"),
        ("美元兑澳元多少", "exchange_rate"),
        ("湖人队现在比分多少", "sports_score"),
    ]

    passed = 0
    for query, expected_subtype in test_cases:
        result = SubtypeClassifier.classify(query)
        status = "[PASS]" if result.subtype == expected_subtype else "[FAIL]"
        print(f"{status} Query: {query}")
        print(f"  Subtype: {result.subtype} (expected: {expected_subtype})")
        print(f"  Confidence: {result.confidence:.2f}")
        print(f"  Entities: {result.entities}")
        if result.subtype == expected_subtype:
            passed += 1
        print()

    print(f"Subtype Classification: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


def test_numeric_extraction():
    """Test numeric extraction."""
    print("=" * 70)
    print("TEST 2: NUMERIC EXTRACTION")
    print("=" * 70)

    test_cases = [
        ("Bitcoin (BTC): $42,350 USD", 42350.0, "price"),
        ("USD to AUD: 1.5234", 1.5234, "rate"),
        ("Change: +2.1%", 2.1, "percent"),
        ("Temperature: 22°C", (22.0, "C"), "temperature"),
        ("Score: 3-2", (3, 2), "score"),
        ("Price range: $1.78–$1.83", (1.78, 1.83), "price_range"),
    ]

    passed = 0
    for text, expected, test_type in test_cases:
        if test_type == "price":
            result = NumericExtractor.extract_price(text)
        elif test_type == "rate":
            result = NumericExtractor.extract_rate(text)
        elif test_type == "percent":
            result = NumericExtractor.extract_percentage(text)
        elif test_type == "temperature":
            result = NumericExtractor.extract_temperature(text)
        elif test_type == "score":
            result = NumericExtractor.extract_score(text)
        elif test_type == "price_range":
            result = NumericExtractor.extract_price_range(text)

        status = "[PASS]" if result == expected else "[FAIL]"
        print(f"{status} {test_type}: {text}")
        print(f"  Result: {result} (expected: {expected})")
        if result == expected:
            passed += 1
        print()

    print(f"Numeric Extraction: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


def test_query_generation():
    """Test query generation."""
    print("=" * 70)
    print("TEST 3: QUERY GENERATION")
    print("=" * 70)

    test_cases = [
        ("weather", {"location": "Melbourne"}, "weather"),
        ("fuel", {"location": "Forest Hill", "fuel_type": "98"}, "fuel"),
        ("crypto", {"symbol": "BTC"}, "crypto"),
        ("stock", {"symbol": "NVDA"}, "stock"),
        ("exchange", {"from_currency": "USD", "to_currency": "AUD"}, "exchange"),
        ("time", {"location": "Tokyo"}, "time"),
    ]

    passed = 0
    for intent_type, kwargs, description in test_cases:
        queries = QueryGenerator.generate(intent_type, **kwargs)
        status = "[PASS]" if queries and len(queries) > 0 else "[FAIL]"
        print(f"{status} {description}")
        print(f"  Queries: {queries[:2]}")  # Show first 2
        if queries and len(queries) > 0:
            passed += 1
        print()

    print(f"Query Generation: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


def test_card_payload():
    """Test card payload generation."""
    print("=" * 70)
    print("TEST 4: CARD PAYLOAD GENERATION")
    print("=" * 70)

    test_cases = [
        ("weather", CardPayloadGenerator.weather("Melbourne", 22, "Cloudy")),
        ("fuel", CardPayloadGenerator.fuel("Forest Hill", "98", 1.78, 1.83)),
        ("crypto", CardPayloadGenerator.crypto("BTC", 42350)),
        ("stock", CardPayloadGenerator.stock("NVDA", "NVIDIA", 875.42)),
        ("exchange", CardPayloadGenerator.exchange("USD", "AUD", 1.5234)),
        ("time", CardPayloadGenerator.time("Tokyo", "22:58", "Asia/Tokyo", False, "夜间")),
    ]

    passed = 0
    for card_type, card in test_cases:
        status = "[PASS]" if card and card.get("card_type") == card_type else "[FAIL]"
        print(f"{status} {card_type}")
        print(f"  Card: {str(card)[:80]}...")
        if card and card.get("card_type") == card_type:
            passed += 1
        print()

    print(f"Card Payload: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


def test_execution_engine():
    """Test full execution engine."""
    print("=" * 70)
    print("TEST 5: EXECUTION ENGINE")
    print("=" * 70)

    from app.skills.bundles.realtime_lookup.integration import RealtimeLookupSkillHandler

    handler = RealtimeLookupSkillHandler(mock_search)

    test_cases = [
        ("帮我联网查墨尔本今天的天气", "weather", "18"),
        ("比特币现在价格多少", "crypto", "42350"),
        ("美元兑澳元多少", "exchange", "1.5234"),
        ("现在东京几点", "time", ":"),  # Just check for time format
    ]

    passed = 0
    for query, expected_type, expected_value in test_cases:
        result = handler.execute(query, allowed_tools=["search_web"])
        status = "[PASS]" if result and result.get("success") else "[FAIL]"
        print(f"{status} Query: {query}")

        if result:
            card = result.get("card", {})
            print(f"  Card type: {card.get('card_type')}")
            answer = result.get('answer', '')
            print(f"  Answer: {answer[:60] if answer else 'N/A'}")

            if (
                result.get("success")
                and card.get("card_type") == expected_type
                and expected_value in result.get("answer", "")
            ):
                passed += 1
        else:
            print(f"  Result: None")
        print()

    print(f"Execution Engine: {passed}/{len(test_cases)} passed\n")
    return passed == len(test_cases)


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("PRODUCTION REALTIME LOOKUP ENGINE TESTS")
    print("=" * 70)

    test1 = test_subtype_classification()
    test2 = test_numeric_extraction()
    test3 = test_query_generation()
    test4 = test_card_payload()
    test5 = test_execution_engine()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"1. Subtype Classification: {'PASS' if test1 else 'FAIL'}")
    print(f"2. Numeric Extraction: {'PASS' if test2 else 'FAIL'}")
    print(f"3. Query Generation: {'PASS' if test3 else 'FAIL'}")
    print(f"4. Card Payload: {'PASS' if test4 else 'FAIL'}")
    print(f"5. Execution Engine: {'PASS' if test5 else 'FAIL'}")
    print(f"\nOverall: {'[ALL TESTS PASSED]' if all([test1, test2, test3, test4, test5]) else '[SOME TESTS FAILED]'}")
    print("=" * 70 + "\n")
