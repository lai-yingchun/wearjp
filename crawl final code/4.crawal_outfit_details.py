import json
import time
import os
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from tqdm import tqdm

# INPUT_JSONL = "wear_user_outfits.jsonl"
INPUT_JSONL = "wear_user_outfits3.jsonl"
OUTPUT_JSONL = "wear_outfit_details3.jsonl"

BASE_DOMAIN = "https://wear.jp"


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


def load_existing_outfit_ids(path):
    """避免重複抓（斷點續跑用）"""
    if not os.path.exists(path):
        return set()

    ids = set()
    with open(path, "r", encoding="utf-8") as f:
        buffer = ""
        for line in f:
            buffer += line.strip()
            if line.strip().endswith("}"):
                try:
                    obj = json.loads(buffer)
                    if "outfit_id" in obj:
                        ids.add(obj["outfit_id"])
                    buffer = ""
                except:
                    continue
    return ids


def append_jsonl(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2)

    import re

    def compress_list(match):
        key = match.group(1)
        content = match.group(2)

        items = [line.strip().strip('",') for line in content.split("\n") if line.strip()]
        items = [f'"{i}"' for i in items]

        return f'"{key}": [{", ".join(items)}]'

    text = re.sub(
        r'"(tags|brands)": \[\n(.*?)\n\s*\]',
        compress_list,
        text,
        flags=re.DOTALL
    )

    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def debug_print(msg):
    print(f"⚠️ {msg}")


# =========================
# 核心爬蟲
# =========================

def crawl_outfit_detail(page, outfit_url):
    # ⭐ 解析 outfit_id
    outfit_id = outfit_url.rstrip("/").split("/")[-1]

    page.goto(outfit_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1200)

    # =========================
    # 主圖
    # =========================
    outfit_image_url = None
    image_detail = None

    imgs = page.locator("img.w-full")

    for i in range(imgs.count()):
        img = imgs.nth(i)
        alt = img.get_attribute("alt")
        src = img.get_attribute("src")

        if not alt or "WEAR" in alt:
            continue

        if "/coordinate/" in src:
            outfit_image_url = src
            image_detail = alt
            break

    # =========================
    # 描述
    # =========================
    desc_node = page.locator("section >> p.hidden.xl\\:block")
    outfit_desc = desc_node.first.inner_text() if desc_node.count() else ""

    user_node = page.locator("p.whitespace-pre-line")
    user_desc = user_node.first.inner_text() if user_node.count() else ""

    # =========================
    # items
    # =========================
    items = []
    seen_ids = set()

    item_cards = page.locator("h2:has-text('着用アイテム') >> xpath=following::ul[1]/li")

    for i in range(item_cards.count()):
        card = item_cards.nth(i)

        link = card.locator("a[href*='/item/'], a[href*='/snapitem/']").first
        if link.count() == 0:
            continue

        href = link.get_attribute("href")
        if not href or href in ["/item/", "/snapitem/"]:
            continue

        item_id = href.rstrip("/").split("/")[-1]

        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        full_url = urljoin(BASE_DOMAIN, href)
        item_type = 0 if "/item/" in href else 1

        # brand
        brand = ""
        brand_link = card.locator("a[href*='/brand/']")
        if brand_link.count():
            brand = brand_link.first.inner_text().strip()

        # category
        category, color = "", ""
        cat_node = card.locator("a[href*='/category/']")
        if cat_node.count():
            text = cat_node.first.inner_text()
            import re
            match = re.match(r"(.+?)\s*\((.+?)\)", text)
            if match:
                category = match.group(1).strip()
                color = f"({match.group(2).strip()})"
            else:
                category = text.strip()

        # size
        size = ""
        size_node = card.locator("p:has-text('サイズ')")
        if size_node.count():
            size = size_node.first.inner_text().replace("サイズ：", "").strip()

        items.append({
            "item_id": item_id,
            "item_url": full_url,
            "item_type": item_type,
            "brand": brand,
            "size": size,
            "category": category,
            "color": color
        })

    # =========================
    # tags / brands
    # =========================
    tags = page.locator("a[href*='tag_ids']")
    tag_list = [t.inner_text().replace("#", "") for t in tags.all()]

    brand_nodes = page.locator("section:has-text('着用ブランド') a[href*='/brand/']")
    brand_list = [
        b.inner_text().strip()
        for b in brand_nodes.all()
        if b.inner_text().strip() and "ZOZOTOWN" not in b.inner_text()
    ]

    return {
        "outfit_id": outfit_id,   # ⭐ 核心新增
        "outfit_url": outfit_url,
        "outfit_image_url": outfit_image_url,
        "image_detail": image_detail,
        "outfit_desc": outfit_desc,
        "user_desc": user_desc,
        "items": items,
        "tags": tag_list,
        "brands": brand_list
    }


# =========================
# Main
# =========================

def main():
    users = load_jsonl(INPUT_JSONL)

    # ⭐ 讀取已存在資料（避免覆蓋 + 斷點續跑）
    existing_ids = load_existing_outfit_ids(OUTPUT_JSONL)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()
        page.set_default_timeout(5000)

        for user in tqdm(users, desc="Users"):
            for outfit in user.get("outfits", []):

                outfit_url = outfit.get("outfit_url")
                if not outfit_url:
                    continue

                outfit_id = outfit_url.rstrip("/").split("/")[-1]

                # ⭐ 跳過已抓過的
                if outfit_id in existing_ids:
                    continue

                try:
                    data = crawl_outfit_detail(page, outfit_url)
                    append_jsonl(OUTPUT_JSONL, data)

                    existing_ids.add(outfit_id)

                except Exception as e:
                    debug_print(f"{outfit_url} ERROR: {e}")

                time.sleep(0.5)

        browser.close()

    print(f"\nSaved: {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()