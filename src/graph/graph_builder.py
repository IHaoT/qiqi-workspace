"""
知识图谱构建模块 - 基于LLM总结文档构建图谱
系统性设计：
- 节点：社区 + 研究领域
- 边：社区 → 研究领域
- 适合Neo4j导入和问答查询
"""
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import GRAPH_DIR, SUMMARY_DIR


class GraphBuilder:
    def __init__(self):
        GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    def build_graph_from_summary(self, summary: Dict) -> Dict:
        """
        从总结文档构建图谱（通用版本）

        图谱结构：
        - community: 社区节点（整个表格）
        - field_nodes: 实体节点列表
        - edges: 边列表
        """
        source_file = summary.get("source_file", "")
        table_name = source_file.replace(".csv", "")

        # 1. 构建社区节点
        community = {
            "id": table_name,
            "label": summary.get("table_description", table_name)[:50],
            "description": summary.get("table_description", ""),
            "table_type": summary.get("table_type", "通用"),
            "overall_analysis": summary.get("overall_analysis", ""),
            "key_insights": summary.get("key_insights", []),
            "data_quality": summary.get("data_quality", ""),
            "type": "community",
            "source_file": source_file,
            "dimensions": summary.get("dimensions", {})
        }

        # 2. 构建实体节点（去重）
        field_node_map = {}  # entity_name -> field_node
        edge_set = set()    # 防止重复边
        edges = []          # 存储边

        for entity in summary.get("entity_summary", []):
            entity_name = entity.get("entity_name", "")
            if not entity_name:
                continue

            # 确保entity_name是字符串，并处理纯数字ID的情况
            entity_name = str(entity.get("entity_name", ""))
            if not entity_name:
                continue

            # 如果entity_name是纯数字，加前缀避免Neo4j ID问题
            if entity_name.replace(".", "").replace("-", "").isdigit():
                node_id = f"field_{entity_name}"
            else:
                node_id = entity_name

            # 去重：如果已存在同名节点，跳过
            if node_id in field_node_map:
                continue

            # 构建实体节点 - 通用版本
            raw_data = entity.get("raw_data", {})
            statistics = entity.get("statistics", {})

            # 提取所有属性
            properties = {}

            # 添加统计信息（如果有）
            if statistics:
                properties["stat_count"] = statistics.get("count", 0)
                properties["stat_min"] = statistics.get("min_value", 0)
                properties["stat_max"] = statistics.get("max_value", 0)
                properties["stat_avg"] = statistics.get("avg_value", 0)

            # 从raw_data提取所有数值型属性
            numeric_keys = []
            for k, v in raw_data.items():
                if k in ["entity_name", "group_label", "entity_description"]:
                    continue
                num_val = self._parse_number(v)
                if num_val is not None:
                    # 简化属性名（取最后部分）
                    simple_key = k.split("_")[-1] if "_" in k else k
                    # 如果有重复，取最后一个
                    if simple_key in properties:
                        simple_key = f"{simple_key}_{len(numeric_keys)}"
                    properties[simple_key] = num_val
                    numeric_keys.append(simple_key)

            field_node = {
                "id": node_id,
                "label": entity_name,  # 显示名称用原始值
                "description": entity.get("entity_description", ""),
                "type": "field",
                "parent_community": table_name,
                "group_label": entity.get("group_label", ""),
                "properties": properties
            }
            field_node_map[node_id] = field_node

            # 构建边：社区 → 领域（去重）
            edge_key = (table_name, node_id, "has_field")
            if edge_key not in edge_set:
                edges.append({
                    "from": table_name,
                    "to": node_id,
                    "relation": "has_field",
                    "from_type": "community",
                    "to_type": "field"
                })
                edge_set.add(edge_key)

        field_nodes = list(field_node_map.values())

        # 添加 ROOT → community 边
        edges.append({
            "from": "universe",
            "to": table_name,
            "relation": "contains_community",
            "from_type": "root",
            "to_type": "community"
        })

        # 构建根节点
        root = {
            "id": "universe",
            "label": "所有表格数据",
            "type": "root"
        }

        # 3. 构建图谱
        graph = {
            "root": root,
            "community": community,
            "field_nodes": field_nodes,
            "edges": edges,
            "stats": {
                "total_fields": len(field_nodes),
                "total_edges": len(edges)
            }
        }

        return graph

    def _parse_number(self, value: Any) -> Optional[float]:
        """解析数值，处理空值和非数值"""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        s = str(value).strip()
        # 清理常见非数字字符
        s = s.replace(',', '').replace('$', '').replace('%', '').replace('£', '').replace('€', '')
        if s in ["", "na", "nan", "None", "[no data]", "-"]:
            return None
        try:
            return float(s)
        except:
            return None

    def save_graph(self, graph: Dict, community_id: str):
        """保存图谱到文件"""
        output_path = GRAPH_DIR / f"{community_id}_graph.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
        print(f"  图谱已保存: {output_path}")
        return output_path

    def build_from_summary_file(self, summary_path: Path) -> Optional[Dict]:
        """从总结文件构建图谱"""
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                summary = json.load(f)
        except Exception as e:
            print(f"  读取总结文件失败: {e}")
            return None

        community_id = summary.get("source_file", "").replace(".csv", "")
        print(f"  构建图谱: {community_id}")

        graph = self.build_graph_from_summary(summary)

        print(f"  - 社区: {graph['community']['label']}")
        print(f"  - 研究领域: {len(graph['field_nodes'])} 个")
        print(f"  - 边: {len(graph['edges'])} 条")

        # 保存
        self.save_graph(graph, community_id)

        return graph

    def process_all_summaries(self):
        """处理所有总结文件"""
        summary_files = list(SUMMARY_DIR.glob("*_summary.json"))
        print(f"找到 {len(summary_files)} 个总结文件")

        if not summary_files:
            print(f"在 {SUMMARY_DIR} 中未找到总结文件")
            print(f"请先运行: python src/summarizer.py")
            return []

        all_graphs = []
        for summary_file in summary_files:
            print(f"\n处理: {summary_file.name}")
            try:
                graph = self.build_from_summary_file(summary_file)
                if graph:
                    all_graphs.append(graph)
            except Exception as e:
                print(f"  构建失败: {e}")
                import traceback
                traceback.print_exc()

        print(f"\n========== 处理完成 ==========")
        print(f"成功构建 {len(all_graphs)} 个图谱")

        if all_graphs:
            self.save_summary(all_graphs)

        return all_graphs

    def save_summary(self, all_graphs: List[Dict]):
        """保存图谱构建总结"""
        summary = {
            "total_communities": len(all_graphs),
            "communities": []
        }

        for g in all_graphs:
            comm = g["community"]
            summary["communities"].append({
                "id": comm["id"],
                "label": comm["label"],
                "description": comm.get("description", "")[:100],
                "field_count": len(g["field_nodes"]),
                "key_insights": comm.get("key_insights", [])[:3]
            })

        summary_path = GRAPH_DIR / "communities_summary.json"
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"图谱总结已保存: {summary_path}")


if __name__ == "__main__":
    builder = GraphBuilder()
    builder.process_all_summaries()
