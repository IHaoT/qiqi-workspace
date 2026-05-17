"""
表格解析模块 - 通用HTML表格转CSV
自动识别表头行数、跳过分组标题行、处理多级表头
"""
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple, Dict
import re

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("需要安装 beautifulsoup4: pip install beautifulsoup4")
    sys.exit(1)

import pandas as pd
import sys
_parent = str(Path(__file__).resolve().parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)


class TableParser:
    def __init__(self):
        pass

    def parse_html_table(self, html_content: str) -> Optional[List[List]]:
        """
        使用BeautifulSoup解析HTML表格，正确处理rowspan/colspan
        返回二维列表
        """
        if not html_content or html_content == "null":
            return None

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            table = soup.find('table')
            if not table:
                return None

            rows = table.find_all('tr')

            # 记录每列的 rowspan 状态
            rowspan_state = {}  # {col_idx: remaining_rows}

            grid = []  # 最终的二维列表

            for row_idx, row in enumerate(rows):
                cells = row.find_all(['td', 'th'])

                current_row = []
                col_idx = 0

                # 处理 rowspan（从上一行继承的空位）
                while col_idx in rowspan_state and rowspan_state[col_idx] > 0:
                    current_row.append(None)
                    rowspan_state[col_idx] -= 1
                    col_idx += 1

                for cell in cells:
                    # 跳过 rowspan 占据的空位
                    while col_idx in rowspan_state and rowspan_state[col_idx] > 0:
                        current_row.append(None)
                        rowspan_state[col_idx] -= 1
                        col_idx += 1

                    # 获取单元格信息
                    value = cell.get_text(strip=True)
                    # 空字符串当作 None 处理
                    if value == '':
                        value = None
                    rowspan = int(cell.get('rowspan', 1))
                    colspan = int(cell.get('colspan', 1))

                    # 添加 colspan 个位置（值只在第一个位置，后面填充None）
                    current_row.append(value)
                    col_idx += 1
                    # colspan-1 个空位
                    for c in range(colspan - 1):
                        current_row.append(None)
                        col_idx += 1

                    # 设置 rowspan（影响后续行）- rowspan作用于单元格占据的每个列位置
                    if rowspan > 1:
                        for c in range(colspan):
                            rowspan_state[col_idx - 1 - c] = rowspan - 1

                grid.append(current_row)

            if not grid:
                return None

            # 补齐所有行，使列数一致
            max_cols = max(len(row) for row in grid)
            for i in range(len(grid)):
                while len(grid[i]) < max_cols:
                    grid[i].append(None)

            return grid

        except Exception as e:
            print(f"HTML表格解析失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def is_numeric_row(self, row: List) -> bool:
        """判断某行是否为数据行（包含数值）"""
        if not row:
            return False

        # 统计非空单元格和数值单元格
        non_empty_cells = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]

        if not non_empty_cells:
            return False

        numeric_count = 0
        for val in non_empty_cells:
            # 移除常见的数值格式字符（保留小数点）
            val_clean = re.sub(r'[,\-\(\)%~+\$\s]', '', val)
            # 检查是否是数值（包含数字和小数点）
            if val_clean and re.match(r'^-?\d+(\.\d+)?$', val_clean):
                numeric_count += 1

        # 对于非常宽的表格（100+列），降低阈值
        total_count = len(non_empty_cells)
        threshold = 0.1 if total_count > 100 else 0.3

        # 如果超过阈值的单元格包含数值，认为是数据行
        return numeric_count / total_count >= threshold

    def is_group_header_row(self, row: List, max_cols: int) -> bool:
        """
        判断是否为分组标题行或表格标题行
        分组标题行特征：只有第一列有内容，其他列都是空的，且内容较短（如 "Manager 109"）
        表格标题特征：只有第一列有内容，内容较长（如 "Employees who Report to Manager 109"）
        """
        if not row:
            return False

        # 计算非空单元格数量
        non_empty = sum(1 for cell in row if cell is not None and str(cell).strip())

        # 如果只有1个非空单元格，且在第一列
        if non_empty == 1:
            if row[0] is not None and str(row[0]).strip():
                content = str(row[0]).strip()
                # 分组标题通常较短（小于25个字符）
                # 表格标题可能较长（超过25个字符），但也是标题行，不是数据行
                if len(content) < 25:
                    return True
                # 如果内容较长（表格标题），也认为是标题行（非数据行）
                # 通过 is_header_row 来判断
                return False  # 让 is_header_row 来判断

        # 如果2个非空单元格，且第一列有内容，其他列空的比例很高
        if non_empty == 2:
            if row[0] is not None and str(row[0]).strip():
                content = str(row[0]).strip()
                # 分组标题通常较短
                if len(content) < 25:
                    empty_ratio = (max_cols - non_empty) / max_cols if max_cols > 0 else 1
                    if empty_ratio > 0.7:
                        return True

        return False

    def is_header_row(self, row: List, max_cols: int) -> bool:
        """
        判断是否为表头行（而不是数据行）
        表头行通常不包含数值，或者数值比例很低
        """
        if not row:
            return False

        non_empty = sum(1 for cell in row if cell is not None and str(cell).strip())
        if non_empty == 0:
            return False

        # 计算数值比例
        numeric_count = 0
        for cell in row:
            if cell is not None and str(cell).strip():
                val = str(cell).strip()
                val_clean = re.sub(r'[,\-\(\)%~+\$\s]', '', val)
                if val_clean and re.match(r'^-?\d+(\.\d+)?$', val_clean):
                    numeric_count += 1

        # 如果数值单元格超过30%，认为是数据行
        if non_empty > 0 and numeric_count / non_empty >= 0.3:
            return False

        return True

    def detect_header_and_data_rows(self, grid: List[List]) -> Tuple[int, List[int], List[int]]:
        """
        自动检测表头行数和数据行
        返回: (header_row_count, data_row_indices, group_header_indices)
        """
        if not grid:
            return 0, [], []

        max_cols = max(len(row) for row in grid)

        header_rows = []  # 记录哪些是表头行
        data_rows = []
        group_headers = []

        for i, row in enumerate(grid):
            # 检查是否为分组标题行
            if self.is_group_header_row(row, max_cols):
                group_headers.append(i)
                continue

            # 检查是否为数据行
            if self.is_numeric_row(row):
                data_rows.append(i)
            else:
                # 非数据行，加入表头
                header_rows.append(i)

        # 表头行数 = 第一个数据行的索引（去重后）
        header_count = header_rows[0] if header_rows else 0
        # 重新计算表头行数（从0到第一个数据行之间有多少连续的表头行）
        for i in range(len(grid)):
            if i in group_headers:
                continue
            if i not in data_rows:
                header_count = i + 1
            else:
                break

        # 特殊处理：如果检测到的表头行数为0，但有数据行
        # 说明可能第一行就是数据（如只有一列表格）
        if header_count == 0 and data_rows:
            header_count = data_rows[0]  # 第一行是数据，那表头就是0行

        # 备选方案：如果数据行太少（<30%总行数）或没有数据行，可能是无表头表格
        # 使用内容最丰富的行作为数据（排除分组标题行）
        total_rows = len(grid) - len(group_headers)
        data_ratio = len(data_rows) / total_rows if total_rows > 0 else 0

        # 检查是否是无表头表格：如果数据行在所有表头行之后，且数据行比例<50%
        is_no_header_table = False
        if data_rows and header_count > 0:
            # 如果所有数据行都在表头行之后，且数据行比例<50%，认为是无表头表格
            if data_rows[0] >= header_count and data_ratio < 0.5:
                is_no_header_table = True

        if (not data_rows or data_ratio < 0.3 or is_no_header_table) and len(grid) > 0:
            # 跳过分组标题行，找到内容最多的行
            candidate_rows = []
            for i, row in enumerate(grid):
                if i not in group_headers:
                    non_empty = sum(1 for c in row if c and str(c).strip())
                    if non_empty > 0:
                        candidate_rows.append((i, non_empty, row))

            # 按内容丰富度排序
            if candidate_rows:
                candidate_rows.sort(key=lambda x: x[1], reverse=True)

                # 如果内容最丰富的行显著多于其他行（3倍以上），识别为表头
                if len(candidate_rows) > 1:
                    richest = candidate_rows[0]
                    second_richest = candidate_rows[1]

                    # 如果最丰富的行比第二丰富的行多3倍以上，认为是表头
                    if richest[1] >= second_richest[1] * 3:
                        # richest 的索引 + 1 = 表头行数（包含标题行和表头行）
                        header_count = richest[0] + 1
                        # 数据行从 richest 之后开始（排除 richest 本身和之前的标题）
                        data_rows = [r[0] for r in candidate_rows[1:] if r[0] > richest[0]]
                        data_rows.sort()
                    else:
                        # 检查是否所有行结构相似（无表头表格）
                        # 如果 richest 和 second_richest 的差异小于50%，认为是无表头表格
                        if richest[1] <= second_richest[1] * 1.5:
                            # 无表头表格：所有行都是数据
                            header_count = 0
                            data_rows = [r[0] for r in candidate_rows]
                            data_rows.sort()
                        else:
                            # 取前50%作为数据行
                            top_count = max(1, len(candidate_rows) // 2)
                            data_rows = [r[0] for r in candidate_rows[:top_count]]
                            data_rows.sort()
                            if data_rows:
                                header_count = min(data_rows[0], header_count) if header_count > 0 else 0
                else:
                    # 只有一个候选行，作为表头
                    header_count = candidate_rows[0][0] + 1
                    data_rows = []

        return header_count, data_rows, group_headers

    def build_column_names(self, header_rows: List[List], max_cols: int) -> List[str]:
        """
        根据表头行构建列名
        合并多级表头，只取有意义的标签，避免重复
        """
        if not header_rows:
            return [f"col_{i}" for i in range(max_cols)]

        columns = []

        # 如果有多行表头，跳过表格标题行（第一列内容超过30字符）
        filtered_header_rows = []
        for i, header_row in enumerate(header_rows):
            if i == 0 and header_row[0] and len(str(header_row[0]).strip()) > 30:
                # 第一行是表格标题，跳过
                continue
            filtered_header_rows.append(header_row)

        if not filtered_header_rows:
            return [f"col_{i}" for i in range(max_cols)]

        for col_idx in range(max_cols):
            col_parts = []

            # 从上到下收集每一层表头的值
            for header_row in filtered_header_rows:
                if col_idx < len(header_row):
                    cell_val = header_row[col_idx]
                    if cell_val is not None and str(cell_val).strip():
                        val = str(cell_val).strip()
                        # 过滤掉太长的值（可能是表格标题）和太短的值
                        # 表头内容通常 2-50 个字符
                        if len(val) >= 2 and len(val) < 100:
                            col_parts.append(val)

            # 合并列名，避免重复
            if col_parts:
                # 去重并保持顺序
                seen = set()
                unique_parts = []
                for part in col_parts:
                    # 跳过已经被包含的部分
                    is_duplicate = False
                    for existing in unique_parts:
                        if part in existing or existing in part:
                            is_duplicate = True
                            break
                    if not is_duplicate:
                        unique_parts.append(part)

                col_name = "_".join(unique_parts) if unique_parts else f"col_{col_idx}"
                # 如果列名太长，截断
                if len(col_name) > 80:
                    col_name = col_name[:80] + "..."
            else:
                col_name = f"col_{col_idx}"

            # 确保列名唯一
            if col_name in columns:
                col_name = f"{col_name}_{col_idx}"

            columns.append(col_name)

        return columns

    def process_header_and_data(self, grid: List[List]) -> Optional[pd.DataFrame]:
        """
        通用表头和数据处理
        自动识别表头行数、跳过分组标题行
        """
        if not grid:
            return None

        # 检测表头和数据行
        header_count, data_rows, group_headers = self.detect_header_and_data_rows(grid)

        if not data_rows:
            print("  未检测到数据行")
            return None

        print(f"  检测到 {header_count} 行表头, {len(data_rows)} 行数据, {len(group_headers)} 个分组标题行")

        # 获取表头行（不包含分组标题）
        header_rows = [grid[i] for i in range(header_count) if i not in group_headers]

        # 确定最大列数，并限制最大列数（防止极端宽表）
        max_cols = max(len(grid[i]) for i in data_rows)
        MAX_COLS = 50  # 最大列数限制
        if max_cols > MAX_COLS:
            print(f"  警告: 表格列数({max_cols})超过限制({MAX_COLS})，将截断")
            max_cols = MAX_COLS

        # 构建列名
        columns = self.build_column_names(header_rows, max_cols)

        # 如果没有表头（无表头表格），用第一行数据作为列名
        if header_count == 0 and data_rows:
            first_data_row = grid[data_rows[0]]
            # 用第一行作为列名
            columns = []
            for i, val in enumerate(first_data_row[:max_cols]):
                if val is not None and str(val).strip():
                    col_name = str(val).strip()
                    # 如果太长，截断
                    if len(col_name) > 50:
                        col_name = col_name[:50] + "..."
                else:
                    col_name = f"col_{i}"
                columns.append(col_name)
            # 确保列名唯一
            seen = set()
            for i, col in enumerate(columns):
                if col in seen:
                    columns[i] = f"{col}_{i}"
                seen.add(columns[i])

        # 构建数据
        data = []
        for row_idx in data_rows:
            row = grid[row_idx]
            # 补齐到相同列数
            while len(row) < max_cols:
                row.append(None)
            data.append(row[:max_cols])

        # 创建DataFrame
        df = pd.DataFrame(data, columns=columns)

        # 清理数据
        for col in df.columns:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
                df[col] = df[col].replace(['nan', 'None', ''], None)

        # 清理空行
        df = df.dropna(how='all')

        return df

    def parse_and_save(self, json_file: str, output_dir: str) -> bool:
        """解析单个MinerU JSON文件，输出CSV"""
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        file_name = data.get("file_name", Path(json_file).stem)
        html_content = data.get("markdown", "")

        print(f"\n处理文件: {file_name}")

        grid = self.parse_html_table(html_content)
        if grid is None:
            print(f"  HTML解析失败")
            return False

        print(f"  表格共 {len(grid)} 行")

        df = self.process_header_and_data(grid)

        if df is None or df.empty:
            print(f"  表头处理失败或无数据")
            return False

        print(f"  输出列数: {len(df.columns)}, 数据行数: {len(df)}")

        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{file_name}.csv")
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"  已保存: {output_file}")

        return True

    def process_directory(self, input_dir: str, output_dir: str):
        """批量处理目录下的所有JSON文件"""
        os.makedirs(output_dir, exist_ok=True)

        json_files = list(Path(input_dir).glob("*.json"))
        print(f"找到 {len(json_files)} 个JSON文件")

        success = 0
        failed = []

        for json_file in json_files:
            try:
                if self.parse_and_save(str(json_file), output_dir):
                    success += 1
                else:
                    failed.append(json_file.name)
            except Exception as e:
                print(f"  处理出错 {json_file.name}: {e}")
                import traceback
                traceback.print_exc()
                failed.append(json_file.name)
                continue

        print(f"\n========== 处理完成 ==========")
        print(f"成功: {success}/{len(json_files)}")
        if failed:
            print(f"失败: {len(failed)} 个")
            for f in failed[:10]:
                print(f"  - {f}")
            if len(failed) > 10:
                print(f"  ... 还有 {len(failed) - 10} 个")

        return success


if __name__ == "__main__":
    # 确保能导入 config
    _config_path = str(Path(__file__).resolve().parent.parent)
    if _config_path not in sys.path:
        sys.path.insert(0, _config_path)

    from config import TABLE_DIR, TABLES_CSV_DIR

    parser = TableParser()

    os.makedirs(TABLES_CSV_DIR, exist_ok=True)

    parser.process_directory(str(TABLE_DIR), str(TABLES_CSV_DIR))

    csv_files = list(TABLES_CSV_DIR.glob("*.csv"))
    if csv_files:
        print("\n========== 示例数据 ==========")
        df = pd.read_csv(csv_files[0])
        print(f"文件: {csv_files[0].name}")
        print(f"形状: {df.shape}")
        print(f"列名: {df.columns.tolist()}")
        print(df.head(10).to_string())