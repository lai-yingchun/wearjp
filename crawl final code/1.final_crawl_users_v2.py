import re, json
from playwright.sync_api import sync_playwright
from tqdm import tqdm

BASE_URL = "https://wear.jp/user/?pageno={}"
START_PAGE = 1
NUM_PAGES = 180   # ⭐ 多抓 buffer（建議 >167）
TARGET = 11000

EXCLUDE_USER_IDS = {
    "coordinate","category","brand","tags","ranking","shop","news","column",
    "sp","men","women","user","login","sign_up","item","article","keyword"
}

# ⭐ 只抓 user card
SELECTOR = "li:has(h3) > a[href^='/']"


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def safe_text(locator):
    try:
        if locator.count() > 0:
            return locator.first.inner_text().strip()
    except:
        pass
    return ""


def extract_int(text):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else 0


def scroll_to_bottom(page):
    prev = 0
    while True:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)

        curr = page.locator(SELECTOR).count()
        if curr == prev:
            break
        prev = curr


def crawl_one_pass(page, page_no, users, seen):
    """單一輪抓取"""
    scroll_to_bottom(page)

    cards = page.locator(SELECTOR).all()

    # ⭐ 前166頁固定60
    if page_no <= 166:
        cards = cards[:60]

    print(f"Pass raw count: {len(cards)}")

    added = 0

    for card in cards:
        href = card.get_attribute("href")
        if not href:
            continue

        user_id = href.strip("/")
        if user_id in EXCLUDE_USER_IDS:
            continue
        if user_id in seen:
            continue

        user_name = norm_text(safe_text(card.locator("h3")))
        if not user_name:
            continue

        seen.add(user_id)
        added += 1

        user_info = norm_text(safe_text(card.locator("p.text-xs")))
        stats = card.locator("div.xl\\:flex p")

        outfit_text = safe_text(stats.nth(0)) if stats.count() > 0 else ""
        follower_text = safe_text(stats.nth(1)) if stats.count() > 1 else ""

        users.append({
            "user_id": user_id,
            "user_name": user_name,
            "user_info": user_info,
            "outfit_count_all": extract_int(outfit_text),
            "followers": extract_int(follower_text),
            "user_url": f"https://wear.jp/{user_id}/"
        })

        print(f"[{len(users):05d}] {user_name} ({user_id})")

    print(f"Added this pass: {added}")
    return added


def main():
    users, seen = [], set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="ja-JP")

        # =========================
        # 🔥 跑兩輪（關鍵）
        # =========================
        for run in range(2):
            print(f"\n================ RUN {run+1} ================\n")

            for page_no in tqdm(range(START_PAGE, START_PAGE + NUM_PAGES), desc=f"Run {run+1}"):

                url = BASE_URL.format(page_no)
                print(f"\n=== Page {page_no} ===")

                # =========================
                # 🔵 Pass 1
                # =========================
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector(SELECTOR, timeout=10000)

                crawl_one_pass(page, page_no, users, seen)

                # =========================
                # 🔴 Pass 2（refresh補資料）
                # =========================
                page.reload(wait_until="domcontentloaded")
                page.wait_for_selector(SELECTOR, timeout=10000)

                crawl_one_pass(page, page_no, users, seen)

                print(f"Total unique so far: {len(users)}")

                if len(users) >= TARGET:
                    break

            if len(users) >= TARGET:
                break

        browser.close()

    with open("wear_users.json", "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    print("\n✅ Saved: wear_users.json")
    print(f"Total unique users: {len(users)}")


if __name__ == "__main__":
    main()