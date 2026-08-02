"""Offline full-flow proof for the AWS demo composition."""

from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from crypto_trust_agent.application.dto.live_market import (
    LiveMarketBarDTO,
    LiveMarketDataResultDTO,
)
from crypto_trust_agent.infrastructure.aws.bedrock_reasoning import OperationBoundRecordingClient
from crypto_trust_agent.infrastructure.collectors.google_news_demo import DemoNewsItem
from crypto_trust_agent.presentation.api.aws_demo_composition import build_aws_demo_composition
from crypto_trust_agent.presentation.api.demo_ui_api import DemoUiAsgiApp, DemoUiHttpHandler, create_demo_fastapi_app
from crypto_trust_agent.presentation.api.demo_ui_composition import DEMO_COOKIE_NAME, DEMO_USER_TOKEN
from crypto_trust_agent.presentation.demo_ui.app import DemoApp


class _News:
    def collect(self, asset, category, *, timeout_seconds=8.0):
        del timeout_seconds
        return (DemoNewsItem(
            f"{asset} {category} update",
            "A bounded public-source market update.",
            f"https://news.google.com/rss/articles/{asset.lower()}-{category}",
            "Demo Wire",
            "2026-08-01T00:00:00Z",
        ),)


class _Market:
    def fetch_daily_ohlcv(self, request):
        fetched = str(request.as_of)
        source = "https://api.binance.com/api/v3/klines?symbol=" + request.pair
        days = (request.start_date, request.end_date)
        bars = tuple(
            LiveMarketBarDTO(
                request.asset,
                request.pair,
                day,
                "100",
                "115",
                "95",
                "110" if index else "100",
                "1000",
                source,
                fetched,
                int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp() * 1000),
                int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp() * 1000) + 86_399_999,
                "sha256:" + hashlib.sha256(day.isoformat().encode()).hexdigest(),
            )
            for index, day in enumerate(days)
        )
        return LiveMarketDataResultDTO(
            request.operation_id,
            request.asset,
            request.pair,
            request.start_date,
            request.end_date,
            bars,
            True,
            (),
            (),
            fetched,
        )


class AwsDemoCompositionTests(unittest.TestCase):
    def test_submit_uses_reasoning_boundary_and_publishes_artifacts(self) -> None:
        client_boundary = OperationBoundRecordingClient()
        composition = build_aws_demo_composition(
            reasoning_client=client_boundary,
            market_provider=_Market(),
            news_collector=_News(),
        )
        handler = DemoUiHttpHandler(
            DemoApp(composition.use_case),
            composition.use_case,
            composition.authenticator,
            browser_token=DEMO_USER_TOKEN,
            cookie_name=DEMO_COOKIE_NAME,
        )
        app = create_demo_fastapi_app(DemoUiAsgiApp(handler))
        client = TestClient(app)
        response = client.post(
            "/demo/submit",
            headers={"Authorization": f"Bearer {DEMO_USER_TOKEN}"},
            json={
                "question": "Analyze BTC market conditions using evidence.",
                "assets": ["BTC"],
                "timeframe_start": "2026-07-18T00:00:00Z",
                "timeframe_end": "2026-08-01T00:00:00Z",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        result = response.json()
        self.assertEqual("completed", result["state"])
        self.assertEqual("complete", result["publication_outcome"])
        self.assertEqual(1, len(client_boundary.invoker.calls))
        self.assertTrue(composition.artifact_repository._items)


if __name__ == "__main__":
    unittest.main()
