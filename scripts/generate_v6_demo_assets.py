"""Regenerate V6 demo screenshots and the local demonstration video.

Run after starting ``python 启动演示.py`` on port 8701.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SHOT_DIR = ROOT / "演示截图"
VIDEO_DIR = ROOT / "演示视频"
BASE_URL = "http://127.0.0.1:8701/"
VIDEO_FILE = VIDEO_DIR / "AlphaLens_演示录屏.webm"


def wait_for_app(page) -> None:
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector("#statusDot.on", state="attached", timeout=20000)


def desktop_screenshots(browser) -> None:
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        device_scale_factor=1,
    )
    page = context.new_page()
    wait_for_app(page)
    page.screenshot(path=SHOT_DIR / "1_v6_live_home_desktop.png", full_page=True)

    page.click('button[data-view="macroView"]')
    page.wait_for_selector("text=V6 优化候选", timeout=20000)
    page.wait_for_timeout(1500)
    page.screenshot(path=SHOT_DIR / "3_v6_validation_desktop.png", full_page=True)

    section = page.locator("section.section").filter(has_text="V6 优化候选")
    section.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    page.screenshot(path=SHOT_DIR / "5_v6_validation_v6_section_desktop.png")
    context.close()


def mobile_screenshots(browser) -> None:
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=1,
    )
    page = context.new_page()
    wait_for_app(page)
    page.screenshot(path=SHOT_DIR / "2_v6_live_home_mobile.png", full_page=True)

    page.click('button[data-view="macroView"]')
    page.wait_for_selector("text=V6 优化候选", timeout=20000)
    page.wait_for_timeout(1200)
    page.screenshot(path=SHOT_DIR / "4_v6_validation_mobile.png", full_page=True)
    context.close()


def record_video(browser) -> None:
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(VIDEO_DIR),
        record_video_size={"width": 1440, "height": 900},
    )
    page = context.new_page()
    wait_for_app(page)
    page.wait_for_timeout(700)

    page.click('button[data-view="macroView"]')
    page.wait_for_selector("text=V6 优化候选", timeout=20000)
    for _ in range(3):
        page.mouse.wheel(0, 650)
        page.wait_for_timeout(450)
    page.mouse.wheel(0, -1900)
    page.wait_for_timeout(350)

    page.click('a.brand')
    page.wait_for_selector("#liveView.active", timeout=20000)
    page.wait_for_timeout(800)
    page.click('button[data-example="0"]')
    page.wait_for_timeout(650)

    context.close()
    source = page.video.path()
    shutil.copyfile(source, VIDEO_FILE)


def main() -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            desktop_screenshots(browser)
            mobile_screenshots(browser)
            record_video(browser)
        finally:
            browser.close()
    print("generated screenshots and video")


if __name__ == "__main__":
    main()
