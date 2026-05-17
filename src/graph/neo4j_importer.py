"""
Neo4j 图谱导入脚本
适配新图谱结构：Community + Field 节点，has_field 边
"""
import json
import sys
from pathlib import Path
from typing import Dict

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from neo4j import GraphDatabase
from config import GRAPH_DIR, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


class Neo4jImporter:
    def __init__(self):
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    def close(self):
        self.driver.close()

    def clear_database(self):
        """清空数据库"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("数据库已清空")

    def import_graph(self, graph: Dict):
        """导入单个图谱"""
        root = graph.get("root", {})
        community = graph.get("community", {})
        field_nodes = graph.get("field_nodes", [])
        edges = graph.get("edges", [])

        with self.driver.session() as session:
            # 0. 创建根节点（如果不存在）
            if root:
                session.run("""
                    MERGE (r:Root {
                        id: $id,
                        label: $label,
                        type: 'root'
                    })
                """,
                    id=root.get("id"),
                    label=root.get("label")
                )

            # 1. 创建社区节点（用 MERGE 去重）
            session.run("""
                MERGE (c:Community {id: $id})
                ON CREATE SET c.label = $label, c.description = $description,
                              c.table_type = $table_type, c.type = 'community',
                              c.source_file = $source_file
                SET c.overall_analysis = $overall_analysis,
                    c.key_insights = $key_insights,
                    c.data_quality = $data_quality
            """,
                id=community.get("id"),
                label=community.get("label"),
                description=community.get("description", ""),
                table_type=community.get("table_type", ""),
                overall_analysis=community.get("overall_analysis", ""),
                key_insights=json.dumps(community.get("key_insights", [])),
                data_quality=community.get("data_quality", ""),
                source_file=community.get("source_file", "")
            )
            print(f"  创建社区节点: {community.get('label', '')[:50]}")

            # 2. 创建研究领域节点（用 MERGE 去重）
            for field in field_nodes:
                props = field.get("properties", {})
                group_label = field.get("group_label", "")

                # 构建动态属性（属性名中的空格替换成下划线，避免参数名有空格）
                node_props = {
                    "id": field.get("id"),
                    "label": field.get("label"),
                    "description": (field.get("description", "") or "").replace("\n", " ").replace("\r", " "),
                    "type": "field",
                    "parent_community": field.get("parent_community", ""),
                    "group_label": group_label
                }

                # 只添加标准统计属性，不存储原始列名（避免Neo4j产生大量不同属性）
                if props.get("stat_count") is not None:
                    node_props["stat_count"] = props.get("stat_count")
                if props.get("stat_min") is not None:
                    node_props["stat_min"] = props.get("stat_min")
                if props.get("stat_max") is not None:
                    node_props["stat_max"] = props.get("stat_max")
                if props.get("stat_avg") is not None:
                    node_props["stat_avg"] = props.get("stat_avg")

                # 使用参数化查询
                session.run(f"""
                    MERGE (f:Field {{id: $id}})
                    ON CREATE SET f.label = $label, f.description = $description,
                                  f.type = $type, f.parent_community = $parent_community,
                                  f.group_label = $group_label
                """, **node_props)

                # 如果有标准统计属性，更新它们
                if len(node_props) > 6:  # 除了基本属性外还有统计属性
                    for key in ["stat_count", "stat_min", "stat_max", "stat_avg"]:
                        if key in node_props:
                            session.run(f"""
                                MATCH (f:Field {{id: $id}})
                                SET f.`{key}` = $value
                            """, id=field.get("id"), value=node_props[key])

            print(f"  创建领域节点: {len(field_nodes)} 个")

            # 3. 创建边（用 MERGE 防止重复）
            for edge in edges:
                relation = edge.get("relation", "has_field")
                from_id = edge["from"]
                to_id = edge["to"]

                if relation == "has_field":
                    session.run("""
                        MERGE (c:Community {id: $from_id})
                        MERGE (f:Field {id: $to_id})
                        MERGE (c)-[:has_field]->(f)
                    """, from_id=from_id, to_id=to_id)
                elif relation == "contains_community":
                    session.run("""
                        MERGE (r:Root {id: $from_id})
                        MERGE (c:Community {id: $to_id})
                        MERGE (r)-[:contains_community]->(c)
                    """, from_id=from_id, to_id=to_id)

            print(f"  创建边: {len(edges)} 条")

    def import_all(self):
        """导入所有图谱"""
        graph_files = list(GRAPH_DIR.glob("*_graph.json"))
        print(f"找到 {len(graph_files)} 个图谱文件")

        self.clear_database()

        for graph_file in graph_files:
            print(f"\n导入: {graph_file.name}")
            try:
                with open(graph_file, 'r', encoding='utf-8') as f:
                    graph = json.load(f)
                self.import_graph(graph)
            except Exception as e:
                print(f"  导入失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        print(f"\n========== 导入完成 ==========")

    def print_stats(self):
        """打印统计信息"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (n) RETURN labels(n)[0] as type, count(*) as count
            """)
            print("\n节点统计:")
            for record in result:
                print(f"  {record['type']}: {record['count']}")

            result = session.run("""
                MATCH ()-[r]->() RETURN type(r) as type, count(*) as count
            """)
            print("\n边统计:")
            for record in result:
                print(f"  {record['type']}: {record['count']}")

    def verify_data(self):
        """验证数据完整性"""
        with self.driver.session() as session:
            # 检查社区数量
            result = session.run("MATCH (c:Community) RETURN count(c) as count")
            comm_count = result.single()
            print(f"\n社区数量: {comm_count['count'] if comm_count else 0}")

            # 检查领域数量
            result = session.run("MATCH (f:Field) RETURN count(f) as count")
            field_count = result.single()
            print(f"领域数量: {field_count['count'] if field_count else 0}")

            # 检查边数量
            result = session.run("MATCH ()-[r:has_field]->() RETURN count(r) as count")
            edge_count = result.single()
            print(f"has_field边: {edge_count['count'] if edge_count else 0}")

            # 展示有最大值的领域
            print("\nstat_max最高的前5个领域:")
            result = session.run("""
                MATCH (c:Community)-[:has_field]->(f:Field)
                WHERE f.stat_max IS NOT NULL
                RETURN f.label as name, f.stat_max as max_val
                ORDER BY f.stat_max DESC
                LIMIT 5
            """)
            for record in result:
                print(f"  {record['name']}: {record['max_val']}")


if __name__ == "__main__":
    importer = Neo4jImporter()
    importer.import_all()
    importer.print_stats()
    importer.verify_data()
    importer.close()
