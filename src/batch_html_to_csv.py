"""
批量HTML转CSV
从mineru新格式(*.json)的markdown字段转换
"""
import sys
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from table_parser import TableParser
from config import TABLE_DIR, TABLES_CSV_DIR
import pandas as pd
import json

def main():
    parser = TableParser()
    TABLES_CSV_DIR.mkdir(parents=True, exist_ok=True)

    # 获取所有json文件
    json_files = list(TABLE_DIR.glob('*.json'))
    total = len(json_files)

    print(f'总JSON文件: {total}')
    print(f'输出目录: {TABLES_CSV_DIR}')
    print()

    success = 0
    failed = 0
    skip = 0

    for i, json_file in enumerate(json_files, 1):
        try:
            # 检查是否已转换
            csv_name = json_file.stem + '.csv'
            csv_path = TABLES_CSV_DIR / csv_name

            if csv_path.exists():
                skip += 1
                continue

            # 读取json文件
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 从markdown字段获取HTML
            html = data.get('markdown', '')

            if not html or '<table' not in html:
                failed += 1
                print(f'[{i}/{total}] 跳过(无效HTML): {json_file.name}')
                continue

            # 解析并转CSV
            grid = parser.parse_html_table(html)
            if grid is None:
                failed += 1
                print(f'[{i}/{total}] 跳过(解析失败): {json_file.name}')
                continue

            df = parser.process_header_and_data(grid)
            if df is None or df.empty:
                failed += 1
                print(f'[{i}/{total}] 跳过(无数据): {json_file.name}')
                continue

            # 保存CSV
            df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            success += 1

            if i % 50 == 0 or i == total:
                print(f'进度: {i}/{total}, 成功: {success}, 失败: {failed}, 跳过: {skip}')

        except Exception as e:
            failed += 1
            print(f'[{i}/{total}] 错误: {json_file.name} - {e}')

    print()
    print('=' * 60)
    print(f'完成!')
    print(f'成功: {success}, 失败: {failed}, 跳过(已存在): {skip}')
    print(f'输出目录: {TABLES_CSV_DIR}')
    print('=' * 60)

if __name__ == '__main__':
    main()
