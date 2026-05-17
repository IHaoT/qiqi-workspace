# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

TableQA 是一个从科学论文表格图片中提取结构化数据、生成摘要并构建知识图谱的 Python 数据管道。数据来源为 PubMed Central (PMC) 文章中的表格。

## 运行命令

项目没有统一的构建系统或测试框架，每个阶段独立运行：

```bash
# 阶段1: OCR - 从图片提取表格
python src/ocr/batch_process.py
python src/ocr/batch_process_concurrent.py  # 并发版本

# 阶段2: HTML表格解析为CSV
python src/batch_html_to_csv.py

# 阶段3: LLM摘要生成
python src/summarizer.py              # 基本运行
python src/summarizer.py --retry      # 重试失败的
python src/summarizer.py --workers 5  # 指定并发数
python src/summarizer.py --force      # 强制重新生成

# 阶段4: 知识图谱
python src/graph/graph_builder.py     # 构建图谱JSON
python src/graph/neo4j_importer.py    # 导入Neo4j
```

## 依赖

无 requirements.txt，需手动安装：`beautifulsoup4`, `pandas`, `openai`, `neo4j`, `requests`

## 架构

四阶段流水线，数据单向流动：

```
图片(data/images/) → OCR JSON(data/tables/) → CSV(data/tables_csv/) → 摘要JSON(data/summary/) → 图谱JSON(data/graph/) → Neo4j
```

- `src/config.py` — 全局配置（API密钥、路径、Neo4j连接等），所有模块共用
- `src/ocr/mineru_client.py` — MinerU云OCR API客户端（上传、轮询、下载、解析）
- `src/ocr/batch_process.py` — 批量OCR处理，支持断点续传
- `src/table_parser.py` — `TableParser` 类，处理 rowspan/colspan，自动检测表头/数据行/分组行
- `src/summarizer.py` — `TableSummarizer` 类，调用 OpenAI 兼容 API 生成表格分析和逐行实体摘要，含重试和JSON修复逻辑
- `src/graph/graph_builder.py` — `GraphBuilder` 类，将摘要转为 Root → Community → Field 三层图结构
- `src/graph/neo4j_importer.py` — `Neo4jImporter` 类，将图谱JSON导入Neo4j

## 注意事项

- 无 `__init__.py`，各模块通过 `sys.path` 操作解决导入问题
- `src/config.py` 中包含硬编码的 API 密钥和 Neo4j 密码
- LLM 调用使用 OpenAI 兼容接口（`gpt-4.1-nano`，endpoint 为 `reelxai.com/v1`）
- Neo4j 默认连接 `neo4j://127.0.0.1:7687`
- 代码注释和 LLM prompt 均为中文
