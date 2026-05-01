from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError

from app.schemas.instagram import InstagramCrawlResponse
from app.services.playwright_manager import PlaywrightManager


class InstagramCrawler:
    def __init__(self, manager: PlaywrightManager):
        # Playwright 브라우저가 준비된 매니저를 주입받아 크롤링에 사용합니다.
        if manager.browser is None:
            raise ValueError("Playwright 브라우저가 준비되지 않았습니다.")
        self._manager = manager

    def crawl_post(self, url: str) -> InstagramCrawlResponse:
        # 현재는 게시물 URL의 OG 메타(og:title/og:description/og:image) 기반으로 최소 정보만 추출합니다.
        if "instagram.com" not in url:
            raise ValueError("instagram.com URL만 지원합니다.")

        browser = self._manager.browser
        assert browser is not None

        context = browser.new_context(
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            # DOM이 구성될 때까지 우선 대기합니다.
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # 인스타는 지연 로딩이 많아서 네트워크 idle까지 기다리되, 오래 걸리면 빠르게 탈출합니다.
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightError:
                pass

            # 최종 HTML을 가져와서 BeautifulSoup로 파싱합니다.
            html = page.content()
        except PlaywrightError as e:
            raise TimeoutError("페이지 로딩에 실패했습니다. (차단/네트워크/타임아웃)") from e
        finally:
            # 컨텍스트/페이지를 정리해 쿠키/세션이 누적되지 않게 합니다.
            page.close()
            context.close()

        soup = BeautifulSoup(html, "html.parser")

        def meta(property_name: str) -> str | None:
            # OpenGraph 메타 태그에서 content 값을 꺼냅니다.
            tag = soup.find("meta", attrs={"property": property_name})
            if not tag:
                return None
            return tag.get("content") or None

        og_title = meta("og:title")
        og_description = meta("og:description")
        og_image = meta("og:image")

        images: list[str] = []
        if og_image:
            # og:image는 대표 이미지 1장을 제공하는 경우가 많습니다.
            images.append(og_image)

        # og:description에서 캡션/장소 힌트를 가볍게 추정합니다.
        caption = self._extract_caption_from_og(og_description)
        location_name = self._extract_location_hint(og_description)

        return InstagramCrawlResponse(
            url=url,
            caption=caption,
            images=images,
            location_name=location_name,
            og_title=og_title,
            og_description=og_description,
        )

    def _extract_caption_from_og(self, og_description: str | None) -> str | None:
        if not og_description:
            return None
        # 보통: '...: "캡션"'
        m = re.search(r'\"(.+?)\"', og_description)
        if m:
            return m.group(1).strip() or None
        return og_description.strip() or None

    def _extract_location_hint(self, og_description: str | None) -> str | None:
        if not og_description:
            return None
        # 예: "장소명 • ..." 형태가 섞여 나오는 경우가 있어 가벼운 힌트만 제공합니다.
        # 실제 장소는 로그인/GraphQL/추가 JSON 파싱이 필요할 수 있습니다.
        if " • " in og_description:
            head = og_description.split(" • ", 1)[0].strip()
            return head or None
        return None
