import json
import time
import re
import os
import requests
import shutil
from urllib.parse import urljoin
from datetime import datetime
from playwright.sync_api import sync_playwright
from tqdm import tqdm

INPUT_JSON = "filtered_users.json"
OUTPUT_JSON = "wear_user_outfits2.jsonl"

BASE_DOMAIN = "https://wear.jp"
START_TIMEOUT_MS = 30_000

CUTOFF_DATE = datetime(2023, 1, 1)

HREF_RE = re.compile(r"^/[^/]+/\d+/$")


# =========================
# Utils
# =========================

def load_users(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_processed_users(path):
    """避免重複寫入 JSONL"""
    if not os.path.exists(path):
        return set()

    users = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                users.add(data.get("user_id"))
            except:
                pass
    return users

def is_coordinate_href(href: str) -> bool:
    return bool(href) and bool(HREF_RE.match(href))

def parse_datetime(dt_str):
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except:
        return None

def append_jsonl(path, data):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False))
        f.write("\n")


# =========================
# Disk 檢查
# =========================

def check_disk_usage(threshold=0.9):
    total, used, free = shutil.disk_usage("/")
    usage = used / total

    if usage >= threshold:
        print(f"\n🚨 Disk usage {usage*100:.2f}% 超過限制 {threshold*100}% → 中斷")
        return False

    return True


# =========================
# 🖼️ Image（統一 276）
# =========================

def to_276(url):
    if not url:
        return None

    if re.search(r"_\d+\.jpg", url):
        return re.sub(r"_\d+\.jpg", "_276.jpg", url)

    return url.replace(".jpg", "_276.jpg")


def download_image(url, save_path):
    try:
        if not url:
            return False

        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        # 已存在就跳過
        if os.path.exists(save_path):
            return True

        resp = requests.get(url, timeout=10)

        if resp.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True
        else:
            print(f"❌ status {resp.status_code}: {url}")

    except Exception as e:
        print(f"❌ download fail: {url} | {e}")

    return False


# =========================
# Core crawler
# =========================

def crawl_one_user(page, user):
    user_id = user.get("user_id")
    name = user.get("user_name")

    outfits = []
    seen_outfit = set()

    page_no = 1
    stop_all = False

    pbar_page = tqdm(desc=f"{user_id}", leave=False)

    while True:
        url = f"{BASE_DOMAIN}/{user_id}/?pageno={page_no}"

        page.goto(url, wait_until="domcontentloaded", timeout=START_TIMEOUT_MS)
        page.wait_for_timeout(1500)

        anchors = page.locator("a.relative.block")
        cnt = anchors.count()

        if cnt == 0:
            break

        pbar_page.update(1)

        for i in range(cnt):
            a = anchors.nth(i)
            href = a.get_attribute("href")

            if not is_coordinate_href(href):
                continue

            outfit_url = urljoin(BASE_DOMAIN, href)
            if outfit_url in seen_outfit:
                continue

            # 時間
            t = a.locator("time").first
            dt_obj = None

            if t.count() > 0:
                dt_obj = parse_datetime(t.get_attribute("datetime"))

            # cutoff
            if dt_obj and dt_obj < CUTOFF_DATE:
                stop_all = True
                break

            # 圖片
            img = a.locator("img").first
            raw_img_url = img.get_attribute("src") if img.count() > 0 else None
            outfit_image_url = to_276(raw_img_url)

            # outfit id
            outfit_id = outfit_url.rstrip("/").split("/")[-1]

            # 本地圖片
            local_path = None
            if outfit_image_url:
                local_path = f"images/{user_id}/{outfit_id}.jpg"

                success = download_image(outfit_image_url, local_path)
                if not success:
                    local_path = None

                time.sleep(0.25)

            # 完整網址
            full_outfit_url = f"{BASE_DOMAIN}/{user_id}/{outfit_id}/"

            outfits.append({
                "outfit_id": outfit_id,
                "outfit_url": full_outfit_url,
                "outfit_image_path": local_path,
                "outfit_image_url": outfit_image_url,
                "time_datetime": dt_obj.strftime("%Y-%m-%d") if dt_obj else None
            })

            seen_outfit.add(outfit_url)

        pbar_page.set_postfix(outfits=len(outfits))

        if stop_all:
            break

        page_no += 1
        if page_no > 50:
            break

    pbar_page.close()

    return {
        "user_id": user_id,
        "user_name": name,
        "user_url": f"{BASE_DOMAIN}/{user_id}/",
        "outfit_count": len(outfits),
        "outfits": outfits
    }


# =========================
# Main（分段 + resume）
# =========================

def main(start_idx=0, end_idx=None, disk_threshold=0.9):
    users = load_users(INPUT_JSON)

    if end_idx is None:
        end_idx = len(users)

    users = users[start_idx:end_idx]

    print(f"🚀 處理範圍: {start_idx} ~ {end_idx} (共 {len(users)} users)")

    processed_set = load_processed_users(OUTPUT_JSON)

    total_time = 0.0
    processed_users = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()

        for user in tqdm(users, desc="Users", dynamic_ncols=True):

            # 已處理 → skip
            if user["user_id"] in processed_set:
                continue

            # 磁碟檢查
            if not check_disk_usage(disk_threshold):
                print("🛑 停止執行（磁碟不足）")
                break

            start_time = time.time()

            try:
                data = crawl_one_user(page, user)
                append_jsonl(OUTPUT_JSON, data)

                user_time = time.time() - start_time
                total_time += user_time
                processed_users += 1

                avg_time = total_time / processed_users

                tqdm.write(
                    f"{user['user_id']} -> {data['outfit_count']} outfits | "
                    f"⏱ {user_time:.2f}s | avg {avg_time:.2f}s"
                )

            except Exception as e:
                append_jsonl(OUTPUT_JSON, {
                    "user_id": user.get("user_id"),
                    "user_name": user.get("user_name"),
                    "user_url": user.get("user_url"),
                    "outfit_count": 0,
                    "outfits": [],
                    "error": str(e)
                })

                tqdm.write(f"❌ {user['user_id']} failed | {e}")

            time.sleep(1.0)

        browser.close()

    print(f"\n✅ Done: {start_idx} ~ {end_idx}")


# =========================
# Run
# =========================

if __name__ == "__main__":
    main(500, 1000)   # 👉 自己改這裡分段