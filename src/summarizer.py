"""
表格总结模块 - LLM生成表格内容深度总结
逐行处理 + 详细整体分析
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import pandas as pd
from openai import OpenAI

sys.path.append(str(Path(__file__).parent))
from config import GRAPHRAG_API_KEY, GRAPHRAG_API_BASE, GRAPHRAG_MODEL

# Prompt模板 - 用于生成表格整体详细分析
SUMMARIZER_PROMPT_OVERVIEW = '''
你是一个专业的数据分析师。请分析以下表格，生成极其详细的总结报告。

## 表格信息
文件名: {file_name}
列名: {columns}
数据行数: {row_count}

## 【重要】全部原始数据
以下是该表格的完整数据，你必须基于这些数据进行深度分析，不要基于摘要或样本：

{data_full}

## 【强制要求】
你必须对上述数据进行全面分析，生成以下内容：

### 1. 表格整体理解
- 这是什么类型的表格？数据的主题、范围、核心价值是什么
- 时间跨度（如果有年份列）和空间范围是什么
- 表格中有哪些主要类别或分组

### 2. 列的含义分析
- 逐列说明其含义、数据类型、取值范围
- 各列之间的关系

### 3. 数据深度分析（必须包含具体数值）
- 数据总和、最大值、最小值、平均值等统计量
- 数据的分布特征
- 如果有年份列，需要描述各年份的数据变化（具体数值）
- 指出数据的极值行和极值列（给出具体数值）

### 4. 关键发现（至少5条，每条必须包含具体数值）
- 例如："2012年博士后发现代科学领域共2108人，占比75.5%；2019年增至2132人但占比降至63.9%"
- 不能只说"基础科学占比较高"，必须给出具体数字

### 5. 数据质量评估
- 数据完整性（缺失值情况）
- 数据一致性
- 任何异常值或需要注意的问题

## 输出格式（严格JSON，无注释）：
{{
    "source_file": "{file_name}",
    "table_type": "表格类型",
    "table_description": "整体描述（详细说明表格是什么、数据主题、范围、主要内容）",
    "columns": [{{"name": "列名", "description": "列含义", "data_type": "类型"}}],
    "dimensions": {{"row_count": {row_count}, "col_count": {col_count}, "key_column": "主键列名"}},
    "overall_analysis": "整体详细分析（必须包含具体数值，不能泛泛而谈）",
    "key_insights": ["关键发现1（必须包含具体数值）", "关键发现2", "关键发现3", "关键发现4", "关键发现5"],
    "data_quality": "数据质量评估"
}}

【禁止】不要有注释，不要有尾部逗号，不要返回任何非JSON内容
'''

# Prompt模板 - 用于逐行生成entity_summary
SUMMARIZER_PROMPT_ENTITY_SINGLE = '''
你是一个专业的数据分析师。请分析以下数据行，生成实体摘要。

## 表格背景
文件名: {file_name}
总行数: {total_rows}
当前处理: 第{row_idx}行（共{total_rows}行）

## 该行原始数据
{row_data}

## 列名
{columns}

## 【强制要求】
你必须为上述数据行生成一个完整的entity_summary对象，包含以下字段：

1. **entity_name**: 该行第一列（entity_name列）的值，这是实体的唯一标识符

2. **group_label**: 该行所属的分组/类别名称。如果没有明显的分组，写"整体"。注意：group_label应该是一个类别名称，如"科学类"、"工程类"，而不是单个实体名。

3. **entity_description**: 【最重要】详细分析描述，要求：
   - 说明该实体是什么
   - 列出该行的所有具体数值（与相邻实体对比）
   - 指出该行的特点：最大/最小/异常值
   - 如果有年份变化，给出具体的变化数值
   - 给出简短结论性描述
   - 必须包含raw_data中的所有数值！

4. **raw_data**: 【关键】必须是完整的原始数据字典，包含该行所有列的值。
   从上面"该行原始数据"中提取所有列名和对应的值。

5. **statistics**: 该行的数值型数据的统计信息：
   - count: 数值个数
   - min_value: 最小值
   - max_value: 最大值
   - avg_value: 平均值
   - 如果没有数值型数据，设为null

## 输出格式（严格JSON对象，无数组包装）：
{{
    "entity_name": "实体名",
    "group_label": "分组名",
    "entity_description": "详细分析描述，包含所有具体数值",
    "raw_data": {{"列名1": "值1", "列名2": "值2", ...}},
    "statistics": {{"count": 数值个数, "min_value": 最小值, "max_value": 最大值, "avg_value": 平均值}}
}}

【禁止】不要有注释，不要有尾部逗号，不要返回数组，只返回一个JSON对象
'''


class TableSummarizer:
    def __init__(self):
        self.client = OpenAI(
            api_key=GRAPHRAG_API_KEY,
            base_url=GRAPHRAG_API_BASE
        )
        from config import TABLES_CSV_DIR, SUMMARY_DIR
        self.csv_dir = TABLES_CSV_DIR
        self.summary_dir = SUMMARY_DIR
        self.summary_dir.mkdir(parents=True, exist_ok=True)

    def load_csv_data(self, csv_path: str) -> Optional[pd.DataFrame]:
        """加载CSV文件"""
        try:
            df = pd.read_csv(csv_path)
            return df
        except Exception as e:
            print(f"读取CSV失败: {csv_path}, 错误: {e}")
            return None

    def prepare_prompt_data(self, df: pd.DataFrame, file_name: str) -> Dict:
        """准备Prompt需要的数据"""
        columns = df.columns.tolist()
        row_count = len(df)

        # 完整的CSV数据（全部行）
        data_full = df.to_csv(index=False)

        return {
            "file_name": file_name,
            "columns": columns,
            "row_count": row_count,
            "col_count": len(columns),
            "data_full": data_full
        }

    def call_llm(self, prompt: str, max_retries: int = 3) -> Optional[Dict]:
        """调用LLM生成总结，带重试机制和JSON修复"""
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=GRAPHRAG_MODEL,
                    messages=[
                        {"role": "system", "content": "你是一个专业的数据分析师，擅长深度分析各类表格数据。请只返回标准JSON格式，不要添加任何注释或说明。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3
                )
                content = response.choices[0].message.content.strip()

                # 提取JSON
                json_str = self.extract_json(content)
                if not json_str:
                    if attempt < max_retries - 1:
                        print(f"  未找到JSON，重试 ({attempt+1}/{max_retries})")
                        continue
                    print(f"  LLM返回不是JSON格式")
                    return None

                # 修复JSON格式问题
                json_str = self.fix_json(json_str)

                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as e:
                    if attempt < max_retries - 1:
                        print(f"  JSON解析失败，尝试修复后重试 ({attempt+1}/{max_retries})")
                        continue
                    else:
                        print(f"  JSON解析失败: {e}")
                        # 保存失败的原始输出用于调试
                        debug_path = os.path.join(self.summary_dir, "debug_failed.txt")
                        with open(debug_path, 'w', encoding='utf-8') as f:
                            f.write(f"原始内容:\n{content}\n\n修复后:\n{json_str}\n\n错误: {e}")
                        print(f"  已保存调试文件: {debug_path}")
                        return None

            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  调用失败，重试 ({attempt+1}/{max_retries})")
                    continue
                print(f"  LLM调用失败: {e}")
                return None

        return None

    def call_llm_for_object(self, prompt: str, max_retries: int = 3) -> Optional[Dict]:
        """调用LLM生成单个JSON对象（用于entity），带重试机制"""
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=GRAPHRAG_MODEL,
                    messages=[
                        {"role": "system", "content": "你是一个专业的数据分析师。请只返回标准JSON对象格式，不要添加任何注释或说明。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3
                )
                content = response.choices[0].message.content.strip()

                # 提取JSON
                json_str = self.extract_json(content)
                if not json_str:
                    if attempt < max_retries - 1:
                        continue
                    return None

                # 修复JSON格式问题
                json_str = self.fix_json(json_str)

                try:
                    result = json.loads(json_str)
                    if isinstance(result, dict):
                        return result
                    else:
                        # 返回的是数组，取第一个
                        if isinstance(result, list) and len(result) > 0:
                            return result[0]
                        return None
                except json.JSONDecodeError:
                    if attempt < max_retries - 1:
                        continue
                    return None

            except Exception as e:
                if attempt < max_retries - 1:
                    continue
                return None

        return None

    def fix_json(self, json_str: str) -> str:
        """修复常见的JSON格式问题"""
        import re
        import json as json_module

        # 1. 移除JavaScript风格注释 // ... 行注释
        json_str = re.sub(r'//.*$', '', json_str, flags=re.MULTILINE)

        # 2. 移除 /* ... */ 块注释（跨行）
        json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)

        # 3. 移除尾随逗号（逗号后是 } 或 ]）
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

        # 4. 移除多余逗号在数组开头
        json_str = json_str.replace('[,', '[')
        json_str = json_str.replace('{,', '{')

        # 5. 尝试解析
        try:
            json_module.loads(json_str)
        except json.JSONDecodeError as e:
            if "Extra data" in str(e) or "Duplicate" in str(e):
                try:
                    data = json_module.loads(json_str)
                    if isinstance(data, list):
                        fixed_data = []
                        for item in data:
                            if isinstance(item, dict):
                                seen = {}
                                for k, v in item.items():
                                    seen[k] = v
                                fixed_data.append(seen)
                            else:
                                fixed_data.append(item)
                        json_str = json_module.dumps(fixed_data)
                    elif isinstance(data, dict):
                        seen = {}
                        for k, v in data.items():
                            seen[k] = v
                        json_str = json_module.dumps(seen)
                except:
                    pass

        json_str = json_str.strip()

        return json_str

    def extract_json(self, content: str) -> Optional[str]:
        """从LLM输出中提取JSON字符串"""
        # 去掉markdown代码块
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.rfind("```")
            content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.rfind("```")
            content = content[start:end].strip()

        # 提取JSON对象
        if "{" in content and "}" in content:
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            return content[json_start:json_end]

        # 提取JSON数组
        if "[" in content and "]" in content:
            json_start = content.find("[")
            json_end = content.rfind("]") + 1
            return content[json_start:json_end]

        return None

    def call_llm_overview(self, df: pd.DataFrame, file_name: str) -> Optional[Dict]:
        """生成表格概述（详细版本）"""
        data = self.prepare_prompt_data(df, file_name)

        prompt = SUMMARIZER_PROMPT_OVERVIEW.format(
            file_name=data["file_name"],
            columns=str(data["columns"]),
            row_count=data["row_count"],
            col_count=data["col_count"],
            data_full=data["data_full"]
        )

        result = self.call_llm(prompt)
        if result:
            result["original_columns"] = data["columns"]
        return result

    def call_llm_entity_single(self, row_data: str, columns: List[str],
                                file_name: str, row_idx: int, total_rows: int) -> Optional[Dict]:
        """为单行数据生成entity_summary"""
        prompt = SUMMARIZER_PROMPT_ENTITY_SINGLE.format(
            file_name=file_name,
            total_rows=total_rows,
            row_idx=row_idx,
            row_data=row_data,
            columns=", ".join(columns)
        )

        result = self.call_llm_for_object(prompt)

        if not result:
            return None

        # 确保raw_data包含所有列
        if 'raw_data' not in result or not result['raw_data']:
            # 从row_data解析
            lines = row_data.strip().split('\n')
            if len(lines) >= 2:
                headers = [h.strip() for h in lines[0].split(',')]
                values = [v.strip() for v in lines[1].split(',')]
                result['raw_data'] = dict(zip(headers, values))

        # 计算statistics
        if 'raw_data' in result and 'statistics' not in result:
            raw = result['raw_data']
            numeric_values = self._extract_numeric(raw)
            if numeric_values:
                result['statistics'] = {
                    'count': len(numeric_values),
                    'min_value': min(numeric_values.values()),
                    'max_value': max(numeric_values.values()),
                    'avg_value': sum(numeric_values.values()) / len(numeric_values)
                }

        return result

    def _extract_numeric(self, raw_data: Dict) -> Dict:
        """从raw_data中提取数值"""
        numeric = {}
        for k, v in raw_data.items():
            if isinstance(v, (int, float)):
                numeric[k] = v
            elif isinstance(v, str):
                cleaned = v.replace(',', '').replace('$', '').replace('%', '').replace('£', '').replace('€', '').strip()
                if cleaned in ['[no data]', 'N/A', 'na', 'nan', '', '-']:
                    continue
                try:
                    numeric[k] = float(cleaned)
                except:
                    pass
        return numeric

    def _determine_group_label(self, df: pd.DataFrame, row_idx: int, entity_name: str) -> str:
        """根据行位置和数据特征确定group_label"""
        # 简单启发式：根据行索引判断分组
        # 如果entity_name包含特定关键词，归入对应分组

        # 常见分组关键词
        group_keywords = {
            'science': ['science', 'biological', 'chemistry', 'physics', 'geosciences', 'mathematics',
                        'computer', 'psychology', 'social sciences', 'natural resources'],
            'engineering': ['engineering', 'aerospace', 'agricultural engineering', 'bioengineering',
                           'chemical engineering', 'civil engineering', 'electrical', 'mechanical',
                           'materials', 'nuclear', 'petroleum', 'industrial'],
            'health': ['health', 'medical'],
            'multidisciplinary': ['multidisciplinary', 'non-science', 'not known']
        }

        entity_lower = entity_name.lower()

        # 检查是否在特定分组区域（基于行索引的启发式）
        # 科学类通常在前半部分，工程类在中间
        for group, keywords in group_keywords.items():
            for kw in keywords:
                if kw in entity_lower:
                    return group

        # 基于行索引的启发式分组
        total_rows = len(df)
        if row_idx < total_rows * 0.3:
            return 'science'
        elif row_idx < total_rows * 0.7:
            return 'engineering'
        else:
            return 'other'

    def summarize_dataframe(self, df: pd.DataFrame, file_name: str) -> Optional[Dict]:
        """对DataFrame进行总结（逐行处理）"""
        row_count = len(df)
        print(f"  表格共{row_count}行，逐行处理...")

        # 1. 先生成表格概述（详细版本）
        print(f"    生成表格整体分析...")
        overview = self.call_llm_overview(df, file_name)
        if not overview:
            return None

        # 2. 逐行生成entity_summary
        all_entities = []
        columns = df.columns.tolist()

        for row_idx in range(row_count):
            if (row_idx + 1) % 10 == 0 or row_idx == 0:
                print(f"    处理行 {row_idx + 1}/{row_count}...")

            # 获取该行数据
            row_series = df.iloc[row_idx]
            row_dict = {}
            for col in columns:
                val = row_series[col]
                if pd.notna(val):
                    row_dict[col] = str(val)
                else:
                    row_dict[col] = ""

            # 转换为CSV格式的行
            row_data = ",".join([str(row_dict[col]) for col in columns])

            # 调用LLM生成entity_summary
            entity = self.call_llm_entity_single(row_data, columns, file_name, row_idx + 1, row_count)

            if entity:
                # 确保raw_data完整（从CSV重新提取，避免LLM遗漏）
                entity['raw_data'] = row_dict

                # 确定group_label
                entity_name = entity.get('entity_name', '')
                if not entity.get('group_label') or entity.get('group_label') == '':
                    entity['group_label'] = self._determine_group_label(df, row_idx, entity_name)

                # 计算statistics（确保有）
                if 'statistics' not in entity:
                    numeric = self._extract_numeric(row_dict)
                    if numeric:
                        entity['statistics'] = {
                            'count': len(numeric),
                            'min_value': min(numeric.values()),
                            'max_value': max(numeric.values()),
                            'avg_value': sum(numeric.values()) / len(numeric)
                        }

                all_entities.append(entity)
            else:
                # LLM调用失败，使用备用方案
                print(f"    行{row_idx + 1}生成失败，使用备用方案")
                entity = {
                    'entity_name': row_dict.get(columns[0], f'Row_{row_idx}'),
                    'group_label': self._determine_group_label(df, row_idx, row_dict.get(columns[0], '')),
                    'entity_description': f"数据行，包含值: {row_dict}",
                    'raw_data': row_dict,
                    'statistics': None
                }
                numeric = self._extract_numeric(row_dict)
                if numeric:
                    entity['statistics'] = {
                        'count': len(numeric),
                        'min_value': min(numeric.values()),
                        'max_value': max(numeric.values()),
                        'avg_value': sum(numeric.values()) / len(numeric)
                    }
                all_entities.append(entity)

        # 3. 合并结果
        overview["entity_summary"] = all_entities

        print(f"    完成，共生成 {len(all_entities)} 个entity摘要")

        return overview

    def summarize_csv(self, csv_path: str) -> Optional[Dict]:
        """总结单个CSV文件"""
        df = self.load_csv_data(csv_path)
        if df is None:
            return None

        file_name = Path(csv_path).name

        print(f"  正在分析 {file_name} ...")
        summary = self.summarize_dataframe(df, file_name)

        if summary:
            # 保存总结文档
            output_path = os.path.join(
                self.summary_dir,
                file_name.replace(".csv", "_summary.json")
            )
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            print(f"  总结已保存: {output_path}")
        else:
            print(f"  总结生成失败")

        return summary

    def process_single_csv(self, csv_path: str) -> tuple:
        """处理单个CSV文件，返回(成功状态, 文件名)"""
        try:
            summary = self.summarize_csv(csv_path)
            return (True, Path(csv_path).name) if summary else (False, Path(csv_path).name)
        except Exception as e:
            print(f"  处理失败 {Path(csv_path).name}: {e}")
            import traceback
            traceback.print_exc()
            return (False, Path(csv_path).name)

    def get_pending_csv_files(self) -> List[Path]:
        """获取待处理的CSV文件列表（跳过已成功的）"""
        csv_files = list(Path(self.csv_dir).glob("*.csv"))
        pending = []

        for csv_file in csv_files:
            summary_file = Path(self.summary_dir) / csv_file.name.replace(".csv", "_summary.json")
            if not summary_file.exists():
                pending.append(csv_file)

        return pending

    def validate_summary(self, summary_path: Path) -> tuple:
        """验证summary文件是否有效"""
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 检查必需字段
            required_fields = ['source_file', 'table_type', 'table_description',
                              'dimensions', 'overall_analysis', 'entity_summary',
                              'key_insights', 'data_quality']
            for field in required_fields:
                if field not in data:
                    return (False, f"缺少字段: {field}")

            # 检查entity_summary是否为空
            if not data.get('entity_summary') or len(data.get('entity_summary', [])) == 0:
                return (False, "entity_summary为空")

            # 检查entity_summary数量是否与表格行数匹配
            expected_rows = data.get('dimensions', {}).get('row_count', 0)
            actual_rows = len(data.get('entity_summary', []))
            if expected_rows > 0 and actual_rows != expected_rows:
                return (False, f"entity_summary数量不匹配: 期望{expected_rows}行, 实际{actual_rows}个")

            # 检查每个entity是否有必要的字段
            for i, entity in enumerate(data.get('entity_summary', [])):
                if 'entity_name' not in entity:
                    return (False, f"第{i}个entity缺少entity_name")
                if 'entity_description' not in entity:
                    return (False, f"第{i}个entity缺少entity_description")
                if 'raw_data' not in entity:
                    return (False, f"第{i}个entity缺少raw_data")

            return (True, "")

        except json.JSONDecodeError as e:
            return (False, f"JSON解析失败: {e}")
        except Exception as e:
            return (False, f"验证异常: {e}")

    def get_failed_summary_files(self) -> List[Path]:
        """获取真正失败的summary文件（用于重试）"""
        all_summaries = list(Path(self.summary_dir).glob("*_summary.json"))
        failed = []

        for summary_path in all_summaries:
            is_valid, error_msg = self.validate_summary(summary_path)
            if not is_valid:
                print(f"  发现无效summary: {summary_path.name} - {error_msg}")
                failed.append(summary_path)

        return failed

    def process_all_csv(self, max_workers: int = 10, skip_existing: bool = True):
        """并行处理所有CSV文件"""
        if skip_existing:
            csv_files = self.get_pending_csv_files()
            print(f"待处理文件: {len(csv_files)} 个（跳过已成功的）")
        else:
            csv_files = list(Path(self.csv_dir).glob("*.csv"))
            print(f"找到 {len(csv_files)} 个CSV文件，并行处理 (max_workers={max_workers})")

        total = len(csv_files)
        if total == 0:
            print("没有待处理的文件")
            return 0

        # 计数器（线程安全）
        counter_lock = threading.Lock()
        success_count = [0]
        completed_count = [0]

        def update_counter(success: bool):
            with counter_lock:
                completed_count[0] += 1
                if success:
                    success_count[0] += 1
                if completed_count[0] % 10 == 0 or completed_count[0] == total:
                    print(f"进度: {completed_count[0]}/{total}, 成功: {success_count[0]}")

        # 并行处理
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.process_single_csv, str(csv_file)): csv_file
                for csv_file in csv_files
            }

            for future in as_completed(futures):
                success, name = future.result()
                update_counter(success)

        print(f"\n========== 处理完成 ==========")
        print(f"成功: {success_count[0]}/{total}")

        return success_count[0]

    def retry_failed(self, max_workers: int = 10, retry_count: int = 3):
        """重试失败的summary文件"""
        failed_files = self.get_failed_summary_files()
        print(f"发现 {len(failed_files)} 个已存在的summary文件，开始重试...")

        counter_lock = threading.Lock()
        success_count = [0]
        total_retried = [0]

        def update_counter(success: bool):
            with counter_lock:
                total_retried[0] += 1
                if success:
                    success_count[0] += 1
                if total_retried[0] % 10 == 0 or total_retried[0] == len(failed_files):
                    print(f"重试进度: {total_retried[0]}/{len(failed_files)}, 成功: {success_count[0]}")

        def retry_single(summary_path: Path) -> tuple:
            """重试单个文件"""
            csv_name = summary_path.name.replace("_summary.json", ".csv")
            csv_path = Path(self.csv_dir) / csv_name

            if not csv_path.exists():
                return (False, csv_name)

            for attempt in range(retry_count):
                # 删除旧的summary
                summary_path.unlink(missing_ok=True)

                # 重新处理
                try:
                    summary = self.summarize_csv(str(csv_path))
                    if summary:
                        return (True, csv_name)
                except Exception as e:
                    print(f"  重试失败 {csv_name}: {e}")

            return (False, csv_name)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(retry_single, f): f
                for f in failed_files
            }

            for future in as_completed(futures):
                success, name = future.result()
                update_counter(success)

        print(f"\n========== 重试完成 ==========")
        print(f"成功: {success_count[0]}/{len(failed_files)}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="表格总结工具")
    parser.add_argument("--retry", action="store_true", help="重试失败的文件")
    parser.add_argument("--workers", type=int, default=10, help="并发数")
    parser.add_argument("--force", action="store_true", help="强制重新处理所有文件（跳过断点续传）")
    args = parser.parse_args()

    summarizer = TableSummarizer()

    if args.retry:
        summarizer.retry_failed(max_workers=args.workers)
    else:
        summarizer.process_all_csv(max_workers=args.workers, skip_existing=not args.force)
