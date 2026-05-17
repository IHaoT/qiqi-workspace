"""
批量OCR处理脚本
支持断点续传，自动跳过已处理的文件
"""
import os
import sys
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加当前目录和父目录到路径
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from mineru_client import (
    apply_upload_urls, upload_file, get_results, wait_for_results,
    download_and_extract, parse_mineru_result
)
from config import IMAGE_DIR, TABLE_DIR, TEMP_DIR, BATCH_SIZE, MAX_WORKERS, POLLING_INTERVAL

# 打印路径信息
print(f"图片目录: {IMAGE_DIR}")
print(f"表格目录: {TABLE_DIR}")

# 检查数据目录
if not IMAGE_DIR.exists():
    print(f"\n错误: 图片目录不存在: {IMAGE_DIR}")
    print(f"\n请确保数据文件夹结构正确:")
    print(f"  项目根目录/")
    print(f"  ├── data/")
    print(f"  │   ├── images/  <-- 放入图片")
    print(f"  │   ├── tables/")
    print(f"  │   ├── tables_csv/")
    print(f"  │   ├── summary/")
    print(f"  │   └── graph/")
    print(f"  └── src/")
    sys.exit(1)


def get_processed_files(output_dir):
    """获取已处理的文件列表"""
    processed = set()
    if output_dir.exists():
        for f in output_dir.iterdir():
            if f.suffix == '.json':
                processed.add(f.stem)
    return processed


def process_single_file(file_name, file_path, output_dir, temp_dir):
    """处理单个文件"""
    output_file = output_dir / f"{file_name}.json"

    # 跳过已处理的文件
    if output_file.exists():
        return True, file_name, "已存在，跳过"

    try:
        # 申请上传链接
        batch_id, upload_urls = apply_upload_urls([file_name])

        # 上传文件
        success, _ = upload_file(upload_urls[0], str(file_path))
        if not success:
            return False, file_name, "上传失败"

        # 等待解析结果
        for i in range(120):  # 最多等待10分钟
            time.sleep(5)
            result = get_results(batch_id)
            state = result["data"]["extract_result"][0]["state"]

            if state == "done":
                zip_url = result["data"]["extract_result"][0]["full_zip_url"]
                extract_path = temp_dir / f"{file_name}_extract"

                if download_and_extract(zip_url, extract_path):
                    parsed = parse_mineru_result(extract_path)
                    parsed["file_name"] = file_name
                    parsed["batch_id"] = batch_id

                    with open(output_file, 'w', encoding='utf-8') as f:
                        json.dump(parsed, f, ensure_ascii=False, indent=2)

                    return True, file_name, "解析成功"
                else:
                    return False, file_name, "下载解压失败"
            elif state == "failed":
                return False, file_name, "解析失败"
            else:
                print(f"    {file_name}: 状态={state}")

        return False, file_name, "等待超时"

    except Exception as e:
        return False, file_name, str(e)


def batch_process_images():
    """批量处理图片"""
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # 获取所有PNG和JPG图片
    image_extensions = {'.jpg', '.jpeg', '.png'}
    image_files = [
        (f.name, f)
        for f in IMAGE_DIR.iterdir()
        if f.suffix.lower() in image_extensions
    ]

    if not image_files:
        print(f"目录中没有找到图片: {IMAGE_DIR}")
        return

    print(f"找到 {len(image_files)} 张图片")

    # 获取已处理的文件
    processed = get_processed_files(TABLE_DIR)
    print(f"已处理: {len(processed)} 张")

    # 过滤未处理的文件
    todo_files = [(name, path) for name, path in image_files if name not in processed]
    print(f"待处理: {len(todo_files)} 张")

    if not todo_files:
        print("没有待处理的文件")
        return

    # 分批处理
    success_count = 0
    fail_count = 0
    failed_files = []

    for i in range(0, len(todo_files), BATCH_SIZE):
        batch = todo_files[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(todo_files) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n========== 批次 {batch_num}/{total_batches} ({len(batch)} 张) ==========")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_single_file, name, path, TABLE_DIR, TEMP_DIR): name
                for name, path in batch
            }

            for future in as_completed(futures):
                name = futures[future]
                try:
                    success, fname, msg = future.result()
                    if success:
                        success_count += 1
                        print(f"  [成功] {fname}: {msg}")
                    else:
                        fail_count += 1
                        failed_files.append(fname)
                        print(f"  [失败] {fname}: {msg}")
                except Exception as e:
                    fail_count += 1
                    failed_files.append(name)
                    print(f"  [异常] {name}: {e}")

    print(f"\n========== 处理完成 ==========")
    print(f"成功: {success_count}")
    print(f"失败: {fail_count}")
    if failed_files:
        print(f"失败文件列表:")
        for f in failed_files[:20]:
            print(f"  - {f}")


if __name__ == "__main__":
    batch_process_images()
