import json
import re

def filter_users(input_path, output_path, threshold=30):
    """
    篩選 outfit_count_all > threshold 的使用者，並存成新 JSON
    """

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    filtered_users = []

    for user in data:
        outfit_count = user.get("outfit_count_all", 0)

        # 處理字串型態（例如 "743 コーディネート"）
        if isinstance(outfit_count, str):
            match = re.search(r"\d+", outfit_count)
            outfit_count = int(match.group()) if match else 0

        # 如果不是 int 或 str，直接當 0
        elif not isinstance(outfit_count, int):
            outfit_count = 0

        if outfit_count > threshold:
            filtered_users.append(user)

    # 寫入新 JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(filtered_users, f, ensure_ascii=False, indent=2)

    print(f"原始用戶數: {len(data)}")
    print(f"篩選後用戶數: {len(filtered_users)}")
    print(f"已存到: {output_path}")


# ===== 使用 =====
input_file = "wear_users.json"
output_file = "filtered_users.json"

filter_users(input_file, output_file, threshold=30)