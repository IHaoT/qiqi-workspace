"""
批量OCR处理脚本 - 并发优化版
利用MinerU批量上传(每次最多50个) + 多并发处理
"""
import requests
import zipfile
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import sys
from pathlib import Path

# 添加当前目录和父目录到路径（避免重复添加）
_current = str(Path(__file__).parent)
_parent = str(Path(__file__).parent.parent)
if _current not in sys.path:
    sys.path.insert(0, _current)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import config
IMAGE_DIR = config.IMAGE_DIR
OUTPUT_DIR = config.TABLE_DIR
TOKEN = config.MINERU_TOKEN
BATCH_SIZE = 2  # 每批上传数量
MAX_WORKERS = 2  # 并发线程数
POLLING_INTERVAL = 5  # 轮询间隔(秒)
MAX_WAIT_TIME = 180  # 最大等待时间(秒)

def get_headers():
    return {'Content-Type': 'application/json', 'Authorization': f'Bearer {TOKEN}'}

# 全局计数器
counter_lock = Lock()
processed_count = [0]
success_count = [0]
fail_count = [0]

def apply_upload_urls(file_names):
    """申请批量上传链接"""
    url = f"{API_BASE}/file-urls/batch"
    data = {
        "files": [{"name": name, "data_id": name} for name in file_names],
        "model_version": "vlm"
    }
    resp = requests.post(url, headers=get_headers(), json=data, timeout=60)
    result = resp.json()

    if result["code"] == 0:
        batch_id = result["data"]["batch_id"]
        file_urls = result["data"]["file_urls"]
        # 兼容处理
        if file_urls and isinstance(file_urls[0], str):
            upload_urls = file_urls
        else:
            upload_urls = [item["upload_url"] for item in file_urls]
        return batch_id, upload_urls
    else:
        raise Exception(f"申请上传链接失败: {result['msg']}")

def upload_file(upload_url, file_path):
    """上传单个文件"""
    for attempt in range(3):
        try:
            with open(file_path, 'rb') as f:
                resp = requests.put(upload_url, data=f, timeout=120)
            if resp.status_code == 200:
                return True, file_path
            return False, file_path
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                return False, file_path
    return False, file_path

def get_results(batch_id):
    """查询解析结果"""
    url = f"{API_BASE}/extract-results/batch/{batch_id}"
    resp = requests.get(url, headers=get_headers(), timeout=30)
    return resp.json()

def wait_for_results(batch_id, timeout=MAX_WAIT_TIME):
    """轮询等待解析结果"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            result = get_results(batch_id)
            if result["code"] != 0:
                print(f"  查询失败: {result.get('msg')}")
                return None

            data = result["data"]
            extract_results = data.get("extract_result", [])

            # 防御性检查
            if not isinstance(extract_results, list):
                print(f"  extract_results格式错误: {type(extract_results)}")
                print(f"  data内容: {str(data)[:500]}")
                return None

            states = []
            for item in extract_results:
                if isinstance(item, dict):
                    states.append(item.get("state"))
                else:
                    print(f"  item不是字典: {type(item)}, 值: {item}")
                    states.append(None)

            # 如果所有文件都完成了
            if all(s == "done" for s in states if s is not None):
                return extract_results
            # 如果有文件失败了
            elif any(s == "failed" for s in states if s is not None):
                return extract_results
            else:
                # 打印当前状态
                running = sum(1 for s in states if s == "running")
                pending = sum(1 for s in states if s == "pending")
                print(f"  等待中: pending={pending}, running={running}")
                time.sleep(POLLING_INTERVAL)

        except Exception as e:
            print(f"  轮询异常: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(POLLING_INTERVAL)

    print(f"  等待超时")
    return None

def download_and_extract(zip_url, output_path):
    """下载zip并解压"""
    try:
        resp = requests.get(zip_url, timeout=120)
        if resp.status_code != 200:
            return False

        zip_path = output_path + ".zip"
        with open(zip_path, 'wb') as f:
            f.write(resp.content)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(output_path)

        os.remove(zip_path)
        return True
    except Exception as e:
        print(f"下载解压失败: {e}")
        return False

def process_batch(file_names, batch_num, total_batches):
    """处理一批文件"""
    global processed_count, success_count, fail_count

    try:
        print(f"[批次 {batch_num}/{total_batches}] 上传 {len(file_names)} 个文件...")

        # 申请上传链接
        batch_id, upload_urls = apply_upload_urls(file_names)

        # 并发上传
        file_paths = [os.path.join(IMAGE_DIR, name) for name in file_names]
        success_uploads = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(upload_file, url, path): name
                for url, path, name in zip(upload_urls, file_paths, file_names)
            }
            for future in as_completed(futures):
                name = futures[future]
                success, path = future.result()
                if success:
                    success_uploads[name] = batch_id

        if len(success_uploads) != len(file_names):
            print(f"[批次 {batch_num}] 上传失败")
            with counter_lock:
                fail_count[0] += len(file_names) - len(success_uploads)
            return

        # 等待解析结果
        print(f"[批次 {batch_num}] 等待解析...")
        extract_results = wait_for_results(batch_id)

        if extract_results is None:
            print(f"[批次 {batch_num}] 等待超时")
            with counter_lock:
                fail_count[0] += len(file_names)
            return

        # 处理结果
        for item in extract_results:
            name = item.get("file_name")
            state = item.get("state")

            with counter_lock:
                processed_count[0] += 1
                current = processed_count[0]

            if state == "done":
                zip_url = item.get("full_zip_url")
                extract_path = os.path.join(OUTPUT_DIR, name + "_extract")

                if download_and_extract(zip_url, extract_path):
                    # 检查full.md是否存在
                    full_md_path = os.path.join(extract_path, "full.md")
                    if os.path.exists(full_md_path):
                        with counter_lock:
                            success_count[0] += 1
                        print(f"[{current}] {name}: 成功")
                    else:
                        with counter_lock:
                            fail_count[0] += 1
                        print(f"[{current}] {name}: HTML不存在")
                else:
                    with counter_lock:
                        fail_count[0] += 1
                    print(f"[{current}] {name}: 下载失败")
            else:
                with counter_lock:
                    fail_count[0] += 1
                print(f"[{current}] {name}: 解析失败 ({state})")

    except Exception as e:
        print(f"[批次 {batch_num}] 错误: {e}")
        with counter_lock:
            fail_count[0] += len(file_names)

def main():
    global processed_count, success_count, fail_count

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 获取待处理文件（检查_extract文件夹是否存在）
    existing = set()
    for d in os.listdir(OUTPUT_DIR):
        if d.endswith('_extract'):
            existing.add(d.replace('_extract', '.png'))

    all_files = [f for f in os.listdir(IMAGE_DIR) if f.endswith('.png')]
    todo_files = [f for f in all_files if f not in existing]

    print(f"总PNG文件: {len(all_files)}")
    print(f"已处理: {len(existing)}")
    print(f"待处理: {len(todo_files)}")

    if not todo_files:
        print("没有待处理的文件")
        return

    # 分批
    batches = []
    for i in range(0, len(todo_files), BATCH_SIZE):
        batch = todo_files[i:i+BATCH_SIZE]
        batches.append((batch, i//BATCH_SIZE + 1, len(todo_files)//BATCH_SIZE + 1))

    print(f"共 {len(batches)} 批，每批最多 {BATCH_SIZE} 个")

    # 并发处理批次
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_batch, batch, batch_num, total): batch
            for batch, batch_num, total in batches
        }

        for future in as_completed(futures):
            batch = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"批次处理异常: {e}")

    print()
    print("=" * 60)
    print(f"批量处理完成")
    print(f"成功: {success_count[0]}")
    print(f"失败: {fail_count[0]}")
    print(f"总计: {processed_count[0]}")

if __name__ == "__main__":
    main()