"""
MinerU 批量图片表格提取
"""
import requests
import os
import time
import json
import zipfile
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 添加父目录到路径（避免重复添加）
_parent = str(Path(__file__).parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from config import (
    MINERU_TOKEN, MINERU_API_BASE,
    IMAGE_DIR, TABLE_DIR, TEMP_DIR,
    BATCH_SIZE, MAX_WORKERS, POLLING_INTERVAL
)


def get_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MINERU_TOKEN}"
    }


def apply_upload_urls(file_names):
    """申请上传链接"""
    url = f"{MINERU_API_BASE}/file-urls/batch"
    data = {
        "files": [{"name": name, "data_id": name} for name in file_names],
        "model_version": "vlm"
    }
    resp = requests.post(url, headers=get_headers(), json=data)
    result = resp.json()

    if result["code"] == 0:
        batch_id = result["data"]["batch_id"]
        file_urls = result["data"]["file_urls"]
        if file_urls and isinstance(file_urls[0], str):
            return batch_id, file_urls
        else:
            upload_urls = [item["upload_url"] for item in file_urls]
            return batch_id, upload_urls
    else:
        raise Exception(f"申请上传链接失败: {result['msg']}")


def upload_file(upload_url, file_path, max_retries=3):
    """上传单个文件，带重试机制"""
    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as f:
                resp = requests.put(upload_url, data=f, timeout=60)
            if resp.status_code == 200:
                return True, file_path
            return False, file_path
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  上传失败，重试 ({attempt + 1}/{max_retries}): {e}")
                time.sleep(2)
            else:
                return False, file_path


def batch_upload(file_paths):
    """批量上传文件"""
    file_names = [os.path.basename(p) for p in file_paths]
    batch_id, upload_urls = apply_upload_urls(file_names)
    print(f"batch_id: {batch_id}")

    success_files = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(upload_file, url, path): path
            for url, path in zip(upload_urls, file_paths)
        }
        for future in as_completed(futures):
            success, path = future.result()
            if success:
                success_files.append(path)
                print(f"上传成功: {path}")
            else:
                print(f"上传失败: {path}")

    return batch_id, success_files


def get_results(batch_id):
    """查询解析结果"""
    url = f"{MINERU_API_BASE}/extract-results/batch/{batch_id}"
    resp = requests.get(url, headers=get_headers())
    return resp.json()


def wait_for_results(batch_id, timeout=3600):
    """轮询等待解析结果"""
    start_time = time.time()

    while time.time() - start_time < timeout:
        result = get_results(batch_id)

        if result["code"] != 0:
            print(f"查询失败: {result['msg']}")
            return None

        data = result["data"]
        extract_results = data.get("extract_result", [])

        states = [item.get("state") for item in extract_results]
        print(f"batch_id: {batch_id}, states: {states}")

        if all(state == "done" for state in states):
            return extract_results
        elif any(state == "failed" for state in states):
            for item in extract_results:
                if item.get("state") == "failed":
                    print(f"解析失败 [{item.get('file_name')}]: {item.get('err_msg')}")
            return extract_results
        else:
            time.sleep(POLLING_INTERVAL)

    print("等待超时")
    return None


def download_and_extract(zip_url, output_path):
    """下载zip并解压"""
    try:
        resp = requests.get(zip_url, timeout=60)
        if resp.status_code != 200:
            return False

        zip_path = str(output_path) + ".zip"
        with open(zip_path, 'wb') as f:
            f.write(resp.content)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(output_path)

        os.remove(zip_path)
        return True
    except Exception as e:
        print(f"下载解压失败: {e}")
        return False


def parse_mineru_result(result_dir):
    """解析 MinerU 结果目录"""
    result_dir = Path(result_dir)
    result_info = {
        "file_name": "",
        "tables": [],
        "markdown": ""
    }

    full_md_path = result_dir / "full.md"
    if full_md_path.exists():
        with open(full_md_path, 'r', encoding='utf-8') as f:
            result_info["markdown"] = f.read()

    content_list_path = result_dir / "content_list.json"
    if content_list_path.exists():
        with open(content_list_path, 'r', encoding='utf-8') as f:
            result_info["content_list"] = json.load(f)

    model_path = result_dir / "model.json"
    if model_path.exists():
        with open(model_path, 'r', encoding='utf-8') as f:
            result_info["model"] = json.load(f)

    layout_path = result_dir / "layout.json"
    if layout_path.exists():
        with open(layout_path, 'r', encoding='utf-8') as f:
            result_info["layout"] = json.load(f)

    return result_info


def process_all_images():
    """处理目录下所有图片"""
    os.makedirs(TABLE_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}
    image_files = [
        f for f in os.listdir(IMAGE_DIR)
        if os.path.splitext(f.lower())[1] in image_extensions
    ]

    if not image_files:
        print(f"目录中没有找到图片: {IMAGE_DIR}")
        return

    print(f"找到 {len(image_files)} 张图片")

    all_results = []
    for i in range(0, len(image_files), BATCH_SIZE):
        batch = image_files[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(image_files) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n========== 处理批次 {batch_num}/{total_batches} ==========")

        try:
            file_paths = [str(IMAGE_DIR / name) for name in batch]
            batch_id, success_files = batch_upload(file_paths)

            extract_results = wait_for_results(batch_id)

            if extract_results:
                for item in extract_results:
                    if item.get("state") == "done":
                        zip_url = item.get("full_zip_url")
                        file_name = item.get("file_name")

                        extract_path = TEMP_DIR / f"{file_name}_extract"
                        if download_and_extract(zip_url, extract_path):
                            parsed = parse_mineru_result(extract_path)
                            parsed["file_name"] = file_name
                            parsed["batch_id"] = batch_id
                            all_results.append(parsed)

                            output_file = TABLE_DIR / f"{file_name}.json"
                            with open(output_file, 'w', encoding='utf-8') as f:
                                json.dump(parsed, f, ensure_ascii=False, indent=2)
                            print(f"解析完成: {file_name}")
                        else:
                            print(f"下载解压失败: {file_name}")

        except Exception as e:
            print(f"批次处理出错: {e}")
            continue

    print(f"\n========== 处理完成 ==========")
    print(f"总处理: {len(image_files)} 张")
    print(f"成功获取结果: {len(all_results)} 张")
    print(f"结果保存在: {TABLE_DIR}")

    return all_results


if __name__ == "__main__":
    process_all_images()
