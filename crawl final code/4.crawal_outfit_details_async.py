import json
import os
import asyncio
import re
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from tqdm import tqdm

INPUT_JSONL = "test.jsonl"
OUTPUT_JSONL = "wear_outfit_details_async.jsonl"
ERROR_JSONL = "error_outfits.jsonl"

BASE_DOMAIN = "https://wear.jp"
CONCURRENCY = 5

# ⭐ 全域 error buffer
error_users = {}


# =========================
# Utils
# =========================

def load_jsonl(path):
    data = []
    buffer = ""

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            buffer += line
            if line.endswith("}"):
                try:
                    data.append(json.loads(buffer))
                    buffer = ""
                except:
                    continue
    return data


def load_existing_ids(path):
    if not os.path.exists(path):
        return set()

    ids = set()
    buffer = ""

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            buffer += line.strip()
            if line.strip().endswith("}"):
                try:
                    obj = json.loads(buffer)
                    if obj.get("outfit_id"):
                        ids.add(obj["outfit_id"])
                    buffer = ""
                except:
                    continue
    return ids


def append_jsonl(path, data):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def save_error_users():
    if not error_users:
        return

    with open(ERROR_JSONL, "a", encoding="utf-8") as f:
        for u in error_users.values():
            f.write(json.dumps(u, ensure_ascii=False) + "\n")


# =========================
# 爬蟲
# =========================

async def crawl_outfit(page, outfit_url):
    outfit_id = outfit_url.rstrip("/").split("/")[-1]

    await page.goto(outfit_url, wait_until="domcontentloaded", timeout=10000)

    try:
        await page.wait_for_selector("img.w-full", timeout=3000)
    except:
        pass

    # ===== 主圖 =====
    outfit_image_url = None
    image_detail = None

    imgs = page.locator("img.w-full")
    count = await imgs.count()

    for i in range(count):
        img = imgs.nth(i)
        alt = await img.get_attribute("alt")
        src = await img.get_attribute("src")

        if not alt or "WEAR" in alt:
            continue

        if src and "/coordinate/" in src:
            outfit_image_url = src
            image_detail = alt
            break

    # ===== 描述 =====
    outfit_desc = ""
    user_desc = ""

    desc_node = page.locator("section >> p.hidden.xl\\:block")
    if await desc_node.count():
        outfit_desc = await desc_node.first.inner_text()

    user_node = page.locator("p.whitespace-pre-line")
    if await user_node.count():
        user_desc = await user_node.first.inner_text()

    # ===== items =====
    items = []
    seen = set()

    cards = page.locator("h2:has-text('着用アイテム') >> xpath=following::ul[1]/li")
    count = await cards.count()

    for i in range(count):
        card = cards.nth(i)

        link = card.locator("a[href*='/item/'], a[href*='/snapitem/']").first
        if not await link.count():
            continue

        href = await link.get_attribute("href")
        if not href:
            continue

        item_id = href.rstrip("/").split("/")[-1]
        if item_id in seen:
            continue
        seen.add(item_id)

        full_url = urljoin(BASE_DOMAIN, href)
        item_type = 0 if "/item/" in href else 1

        # brand
        brand = ""
        brand_node = card.locator("a[href*='/brand/']")
        if await brand_node.count():
            brand = (await brand_node.first.inner_text()).strip()

        # size
        size = ""
        size_node = card.locator("p:has-text('サイズ')")
        if await size_node.count():
            size = (await size_node.first.inner_text()).replace("サイズ：", "").strip()

        # category + color
        category = ""
        color = ""

        cat_node = card.locator("a[href*='/category/']")
        if await cat_node.count():
            text = await cat_node.first.inner_text()

            match = re.match(r"(.+?)\s*\((.+?)\)", text)
            if match:
                category = match.group(1).strip()
                color = f"({match.group(2).strip()})"
            else:
                category = text.strip()

        items.append({
            "item_id": item_id,
            "item_url": full_url,
            "item_type": item_type,
            "item_brand": brand,
            "outfit_item_size": size,
            "item_category": category,
            "outfit_item_color": color
        })

    # ===== tags =====
    tags = page.locator("a[href*='tag_ids']")
    tag_list = [await t.inner_text() for t in await tags.all()]

    # ===== brands（去重）=====
    brand_nodes = page.locator("section:has-text('着用ブランド') a[href*='/brand/']")
    brand_list = [
        (await b.inner_text()).strip()
        for b in await brand_nodes.all()
        if (await b.inner_text()).strip() and "ZOZOTOWN" not in (await b.inner_text())
    ]

    fav_brands = list(dict.fromkeys(brand_list))

    return {
        "outfit_id": outfit_id,
        "outfit_url": outfit_url,
        "outfit_image_url": outfit_image_url,
        "image_detail": image_detail,
        "outfit_desc": outfit_desc,
        "user_desc": user_desc,
        "items": items,
        "tags": tag_list,
        "fav_brands": fav_brands
    }


# =========================
# Worker（⭐ error分流核心）
# =========================

async def worker(browser, semaphore, outfit, user, existing_ids):
    async with semaphore:
        page = await browser.new_page()

        outfit_url = outfit.get("outfit_url")
        outfit_id = outfit_url.rstrip("/").split("/")[-1]

        if outfit_id in existing_ids:
            await page.close()
            return

        try:
            data = await crawl_outfit(page, outfit_url)
            append_jsonl(OUTPUT_JSONL, data)
            existing_ids.add(outfit_id)

            tqdm.write(f"✔ {outfit_id}")

        except Exception:
            tqdm.write(f"❌ {outfit_url}")

            user_id = user["user_id"]

            if user_id not in error_users:
                error_users[user_id] = {
                    "user_id": user["user_id"],
                    "user_name": user.get("user_name"),
                    "user_url": user.get("user_url"),
                    "outfit_count": 0,
                    "outfits": []
                }

            error_users[user_id]["outfits"].append(outfit)
            error_users[user_id]["outfit_count"] += 1

        await page.close()


# =========================
# Main
# =========================

async def main():
    print("🚀 爬蟲開始執行...")

    users = load_jsonl(INPUT_JSONL)
    existing_ids = load_existing_ids(OUTPUT_JSONL)

    print(f"📁 已存在資料: {len(existing_ids)}")

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        tasks = []

        for user in users:
            for outfit in user.get("outfits", []):
                if "outfit_url" in outfit:
                    tasks.append(
                        asyncio.create_task(
                            worker(browser, semaphore, outfit, user, existing_ids)
                        )
                    )

        print(f"📊 總任務數: {len(tasks)}")

        try:
            with tqdm(total=len(tasks), desc="Crawling") as pbar:
                for task in asyncio.as_completed(tasks):
                    await task
                    pbar.update(1)

        except KeyboardInterrupt:
            print("\n🛑 偵測到中斷，正在安全停止...")

            for task in tasks:
                task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

        finally:
            await browser.close()
            save_error_users()

    print("\n✅ 爬蟲完成！")


# =========================

if __name__ == "__main__":
    asyncio.run(main())