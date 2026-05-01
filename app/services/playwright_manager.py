from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Browser, Playwright, sync_playwright


@dataclass
class PlaywrightManager:
    # Playwright 인스턴스와 브라우저 핸들을 서버 전역에서 재사용합니다.
    playwright: Playwright | None = None
    browser: Browser | None = None

    def start(self) -> None:
        # 중복 초기화를 방지합니다.
        if self.playwright or self.browser:
            return
        # Playwright를 시작하고 headless 브라우저를 띄웁니다.
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)

    def stop(self) -> None:
        # 서버 종료 시 자원(브라우저/Playwright)을 정리합니다.
        if self.browser:
            self.browser.close()
            self.browser = None
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
