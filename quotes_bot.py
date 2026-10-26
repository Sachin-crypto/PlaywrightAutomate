"""
quotes_bot.py
Logs into a members-only page (quotes.toscrape.com - a practice site built for
scraping), collects quotes from the first 3 pages and saves them to CSV.

Run it two ways to compare:
    python quotes_bot.py --mode local   # normal Playwright, fresh browser every run
    python quotes_bot.py --mode cloud   # GoLogin Cloud Browser, saved profile

Setup:
    pip install playwright aiohttp
    playwright install chromium          # only needed for --mode local

    Put credentials in a .env file next to this script:
        GL_API_TOKEN=your_gologin_dev_token
        GL_PROFILE_ID=your_profile_id
    Or set them in the environment (PowerShell: $env:GL_API_TOKEN="..."; $env:GL_PROFILE_ID="...")
"""

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

import aiohttp
from playwright.async_api import async_playwright

# Load environment variables from the .env file
load_dotenv()


BASE_URL = "https://quotes.toscrape.com"
# The practice site accepts any username/password.
USERNAME = "sachin"
PASSWORD = "demo123"
MAX_PAGES = 3

TOKEN = os.getenv("GL_API_TOKEN")
PROFILE_ID = os.getenv("GL_PROFILE_ID")
CLOUD_URL = f"wss://cloudbrowser.gologin.com/connect?token={TOKEN}&profile={PROFILE_ID}"
STOP_URL = f"https://api.gologin.com/browser/{PROFILE_ID}/web"


# ---------- shared bot logic (identical for local and cloud) ----------

async def ensure_logged_in(page) -> bool:
    """Returns True if the session was already logged in, False if we had to log in."""
    await page.goto(BASE_URL, wait_until="domcontentloaded")

    if await page.locator('a[href="/logout"]').count() > 0:
        print("✅ Already logged in - session restored from the profile")
        return True

    print("🔐 Not logged in - logging in now...")
    await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    await page.fill("#username", USERNAME)
    await page.fill("#password", PASSWORD)
    await page.click('input[type="submit"]')
    await page.wait_for_selector('a[href="/logout"]', timeout=15000)
    print("✅ Login successful")
    return False


async def scrape_quotes(page) -> list[dict]:
    rows = []
    await page.goto(BASE_URL, wait_until="domcontentloaded")

    for page_no in range(1, MAX_PAGES + 1):
        for q in await page.locator(".quote").all():
            rows.append(
                {
                    "page": page_no,
                    "quote": (await q.locator(".text").inner_text()).strip(),
                    "author": (await q.locator(".author").inner_text()).strip(),
                    "tags": ", ".join(await q.locator(".tag").all_inner_texts()),
                }
            )
        print(f"📄 Page {page_no}: {len(rows)} quotes collected so far")

        next_link = page.locator("li.next > a")
        if page_no == MAX_PAGES or await next_link.count() == 0:
            break
        await next_link.click()
        await page.wait_for_load_state("domcontentloaded")

    return rows


def save_csv(rows: list[dict], mode: str) -> str:
    filename = f"quotes_{mode}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["page", "quote", "author", "tags"])
        writer.writeheader()
        writer.writerows(rows)
    return filename


# ---------- the only part that differs: how we get a browser ----------

async def run_local():
    async with async_playwright() as p:
        # Fresh browser, fresh session, every single run.
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await ensure_logged_in(page)
        rows = await scrape_quotes(page)

        await browser.close()
        return rows


async def check_cloud_session():
    """Same pre-flight check the GoLogin docs use: surfaces auth/profile errors early."""
    async with aiohttp.ClientSession() as session:
        resp = await session.get(CLOUD_URL.replace("wss://", "https://"))
        if resp.status >= 400:
            reason = resp.headers.get("X-Error-Reason", resp.reason)
            raise RuntimeError(f"Failed to start cloud browser: {reason}")


async def stop_cloud_profile():
    async with aiohttp.ClientSession() as session:
        await session.delete(STOP_URL, headers={"Authorization": f"Bearer {TOKEN}"})


async def run_cloud():
    if not TOKEN or not PROFILE_ID:
        sys.exit("Set GL_API_TOKEN and GL_PROFILE_ID first (see top of the file).")

    await check_cloud_session()
    try:
        async with async_playwright() as p:
            # THE one-line change: connect to the cloud browser instead of launching one.
            browser = await p.chromium.connect_over_cdp(CLOUD_URL)

            # Use the profile's own context so its saved cookies/storage are used.
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            await ensure_logged_in(page)
            rows = await scrape_quotes(page)

            await browser.close()
            return rows
    finally:
        # Stop the cloud session so the profile state is saved and minutes aren't wasted.
        await stop_cloud_profile()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local", "cloud"], required=True)
    args = parser.parse_args()

    print(f"\n=== Running in {args.mode.upper()} mode ===")
    rows = await (run_local() if args.mode == "local" else run_cloud())

    filename = save_csv(rows, args.mode)
    print(f"💾 Saved {len(rows)} quotes to {filename}\n")


if __name__ == "__main__":
    asyncio.run(main())