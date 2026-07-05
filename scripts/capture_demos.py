"""README용 데모 스크린샷 캡처 — 웹클라이언트를 헤드리스로 구동해 실제 응답 화면을 저장한다.

사전 조건: webclient/serve.py 가 localhost:8765에서 실행 중.
사용법: uv run --with playwright python scripts/capture_demos.py
"""

import asyncio
import json

from playwright.async_api import async_playwright

DEMOS = [
    ("why_moved", {"query": "삼성전자"}, "assets/demo-why-moved.png"),
    ("risk_check", {"query": "카카오"}, "assets/demo-risk-check.png"),
    ("stock_health", {"query": "SK하이닉스"}, "assets/demo-stock-health.png"),
]


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1360, "height": 980}, device_scale_factor=2)
        await page.goto("http://localhost:8765")
        await page.wait_for_function("typeof tools !== 'undefined' && tools.length === 7", timeout=30000)

        for tool, args, path in DEMOS:
            await page.evaluate(
                "([tool, args]) => { selectTool(tool, args); runTool(); }",
                [tool, args],
            )
            await page.wait_for_function(
                "document.getElementById('result').innerText.includes('응답')", timeout=60000
            )
            await page.wait_for_timeout(400)  # 렌더 안정화
            await page.screenshot(path=path)
            print(f"saved {path} ({tool} {json.dumps(args, ensure_ascii=False)})")

        await browser.close()


asyncio.run(main())
