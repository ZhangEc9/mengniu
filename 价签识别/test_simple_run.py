import json
import time
import requests
import hashlib
import re

from run_price_batch import SYSTEM_PROMPT, USER_PROMPT

with open("oss_image_map.json", "r", encoding="utf-8") as f:
    oss_map = json.load(f)

first_img = "45035165_6_first_normal_1781144340103_10029317.jpeg"
image_url = oss_map[first_img]

appid = "aism-8I4GX61"
secret = "3005609PIFGO2FAYJL0EASA5V829YK35W32EM5YR64JV35O7"
api_url = "https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/45c1d7fe3c?version=prod"

body = {
  "image": image_url,
  "system_text": SYSTEM_PROMPT,
  "text": USER_PROMPT,
  "userId": "",
  "envSystemName": "",
  "userName": "",
  "envSystemVersion": "",
  "unionId": "",
  "apiVersion": "0.0.2"
}

timeout = 180
timestamp = str(int(time.time() * 1000))
sign_str = json.dumps(body) + secret + timestamp
sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest()
headers = {
    "X-MN-APP-ID": appid,
    "X-MN-SIGN": sign,
    "X-MN-TIMESTAMP": timestamp,
    "Content-Type": "application/json",
}
print("Calling API for {} (timeout={}s)...".format(first_img, timeout))
t0 = time.time()
resp = requests.post(api_url, headers=headers, json=body, timeout=timeout)
t1 = time.time()
print("Done in {:.2f}s, HTTP status: {}".format(t1 - t0, resp.status_code))

if resp.status_code == 200:
    res_data = resp.json()
    status = res_data.get("status")
    msg = res_data.get("message")
    print("Business status: {}, message: {}".format(status, msg))
    payload = res_data.get("payload", {})
    print("Token consume: {}".format(payload.get("tokenConsume")))
    result = payload.get("result", {})
    page_content = result.get("page_content", "")
    
    # 存下原始返回以便查看
    with open("raw_api_response_test.txt", "w", encoding="utf-8") as rf:
        rf.write(page_content)
    
    # 提取 JSON
    m = re.search(r"```(?:json)?\s*([\[\{].*?[\]\}])\s*```", page_content, re.DOTALL)
    raw_json = m.group(1) if m else page_content
    data = None
    try:
        data = json.loads(raw_json)
    except Exception:
        m2 = re.search(r"[\[\{].*[\]\}]", page_content, re.DOTALL)
        if m2:
            data = json.loads(m2.group(0))
            
    if isinstance(data, list):
        tags = data
    elif isinstance(data, dict):
        tags = data.get("price_tags", data.get("tags", []))
    else:
        tags = []
        
    print("\n=== 识别结果 ===")
    print("共识别到价签数量: {} 个".format(len(tags)))
    layer_counts = {}
    for item in tags:
        l = item.get("shelf_layer")
        layer_counts[l] = layer_counts.get(l, 0) + 1
    print("各层分布 (shelf_layer): {}".format(layer_counts))
    print("\n--- 价签列表详情 (前 10 个) ---")
    for item in tags[:10]:
        print("  ID {:2d}: 价格={:<6} 层={} bbox={}".format(
            item.get("id", 0), str(item.get("price")), item.get("shelf_layer"), item.get("bbox")
        ))
    if len(tags) > 10:
        print("  ...")
        print("--- 价签列表详情 (后 5 个) ---")
        for item in tags[-5:]:
            print("  ID {:2d}: 价格={:<6} 层={} bbox={}".format(
                item.get("id", 0), str(item.get("price")), item.get("shelf_layer"), item.get("bbox")
            ))
else:
    print("HTTP error {}: {}".format(resp.status_code, resp.text))
