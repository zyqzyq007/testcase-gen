"""
UpstreamService — 检测并解析上游工具（文档审查、需求追溯）的输出，
为测试用例生成提供需求上下文。

上游输出目录（位于源码 zip 解压文件夹内）：
  - document-validator/requirement.json   → 扁平化需求列表
  - traceability-link-recovery/traceability.json → 需求↔函数映射（仅取 rank=1）
"""

import os
import json
import re
from typing import Optional, Dict, Any, List, Tuple

from app.services.project_service import ProjectService


class UpstreamService:
    """从共享卷读取上游工具输出，按函数名+源文件匹配需求上下文。"""

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _find_dir_in_project(project_path: str, target_dir: str) -> Optional[str]:
        """
        在 project_path 下递归搜索名为 target_dir 的目录，返回完整路径。
        最多向下搜索 3 层。
        """
        for root, dirs, _ in os.walk(project_path):
            depth = root[len(project_path):].count(os.sep)
            if depth > 3:
                dirs[:] = []  # 不再深入
                continue
            if target_dir in dirs:
                return os.path.join(root, target_dir)
        return None

    @staticmethod
    def _load_json_file(file_path: str) -> Optional[Any]:
        """安全加载 JSON 文件，失败返回 None。"""
        if not file_path or not os.path.isfile(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 上游数据加载
    # ------------------------------------------------------------------

    @staticmethod
    def get_upstream_status(project_id: str) -> Dict[str, Any]:
        """
        检测上游数据可用性。
        返回 { has_requirements: bool, has_traceability: bool, requirement_count: int, trace_link_count: int }
        """
        project_path = ProjectService.get_project_path(project_id)
        req_dir = UpstreamService._find_dir_in_project(project_path, "document-validator")
        trace_dir = UpstreamService._find_dir_in_project(project_path, "traceability-link-recovery")

        req_file = os.path.join(req_dir, "requirement.json") if req_dir else None
        trace_file = os.path.join(trace_dir, "traceability.json") if trace_dir else None

        req_data = UpstreamService._load_json_file(req_file)
        trace_data = UpstreamService._load_json_file(trace_file)

        req_count = len(req_data) if isinstance(req_data, list) else 0
        trace_links = trace_data.get("trace_links", []) if isinstance(trace_data, dict) else []

        return {
            "has_requirements": req_count > 0,
            "has_traceability": len(trace_links) > 0,
            "requirement_count": req_count,
            "trace_link_count": len(trace_links),
        }

    @staticmethod
    def _load_requirements(project_id: str) -> Dict[str, Dict[str, Any]]:
        """
        加载需求文档，返回 { title: requirement_node } 的映射。
        requirement.json 是数组，每个元素有 title、content、tables、images 等。
        """
        project_path = ProjectService.get_project_path(project_id)
        req_dir = UpstreamService._find_dir_in_project(project_path, "document-validator")
        if not req_dir:
            return {}
        req_file = os.path.join(req_dir, "requirement.json")
        req_list = UpstreamService._load_json_file(req_file)
        if not isinstance(req_list, list):
            return {}
        result = {}
        for node in req_list:
            title = (node.get("title") or "").strip()
            if title:
                result[title] = node
        return result

    @staticmethod
    def _load_traceability(project_id: str) -> List[Dict[str, Any]]:
        """
        加载追溯链路，只返回 rank=1 的链接。
        """
        project_path = ProjectService.get_project_path(project_id)
        trace_dir = UpstreamService._find_dir_in_project(project_path, "traceability-link-recovery")
        if not trace_dir:
            return []
        trace_file = os.path.join(trace_dir, "traceability.json")
        trace_data = UpstreamService._load_json_file(trace_file)
        if not isinstance(trace_data, dict):
            return []
        all_links = trace_data.get("trace_links", [])
        # 仅取 rank=1，保证需求与代码一对一
        return [link for link in all_links if link.get("rank") == 1]

    # ------------------------------------------------------------------
    # 核心方法：按函数匹配需求
    # ------------------------------------------------------------------

    @staticmethod
    def _source_files_match(trace_source: str, func_source: str) -> bool:
        """
        判断追溯链路中的源文件路径与工具解析出的源文件路径是否匹配。
        规则：basename 相同 或 trace_source 是 func_source 的后缀。
        """
        if not trace_source or not func_source:
            return False
        t_norm = trace_source.replace("\\", "/").strip("/")
        f_norm = func_source.replace("\\", "/").strip("/")
        if t_norm == f_norm:
            return True
        if os.path.basename(t_norm) == os.path.basename(f_norm):
            return True
        if f_norm.endswith(t_norm) or t_norm.endswith(f_norm):
            return True
        return False

    @staticmethod
    def get_requirement_context(
        project_id: str,
        function_name: str,
        source_file: str,
        signature: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        根据函数名和源文件路径，从上游数据中获取需求上下文。

        匹配策略：
          1. 在 traceability.json 的 rank=1 链接中查找 function_name 精确匹配
          2. source_file 做 basename/后缀匹配
          3. 若还有多个候选，再对比函数签名
          4. 用匹配到的 requirement_label 去 requirement.json 查找需求内容

        返回:
          {
            "requirement_label": "XQ001 配置项初始化功能",
            "requirement_title": "3.2.2 配置项初始化",
            "requirement_content": "配置项初始化功能需求见表2...",
            "tables": [...],
            "similarity": 0.87,
            "source": "traceability-link-recovery"
          }
          或 None（无匹配）。
        """
        trace_links = UpstreamService._load_traceability(project_id)
        if not trace_links:
            return None

        # Step 1: 按函数名精确匹配
        by_name = [
            link for link in trace_links
            if link.get("function_name", "").strip() == function_name.strip()
        ]
        if not by_name:
            return None

        # Step 2: 按源文件过滤
        by_file = [
            link for link in by_name
            if UpstreamService._source_files_match(
                link.get("source_file", ""), source_file
            )
        ]
        candidate = None
        if len(by_file) == 1:
            candidate = by_file[0]
        elif len(by_file) > 1 and signature:
            # Step 3: 签名辅助筛选（取相似度最高的签名匹配项）
            best = None
            best_score = -1
            for link in by_file:
                link_sig = link.get("function_signature", "")
                score = link.get("similarity", 0)
                if link_sig and signature and link_sig.strip() == signature.strip():
                    score += 1.0  # 签名精确匹配加权
                if score > best_score:
                    best_score = score
                    best = link
            candidate = best
        elif len(by_file) == 0:
            # 文件名没匹配上，退而求其次：只要函数名匹配就取相似度最高的
            if len(by_name) == 1:
                candidate = by_name[0]
            else:
                candidate = max(by_name, key=lambda x: x.get("similarity", 0))

        if not candidate:
            return None

        # Step 4: 用 requirement_label 查找需求内容
        req_label = candidate.get("requirement_label", "").strip()
        if not req_label:
            return None

        all_reqs = UpstreamService._load_requirements(project_id)
        req_node = all_reqs.get(req_label)
        # 如果精确匹配失败，尝试前缀匹配（requirement_label ≈ title）
        if not req_node:
            for title, node in all_reqs.items():
                if title.startswith(req_label) or req_label.startswith(title):
                    req_node = node
                    break

        # 构建返回结果
        result = {
            "requirement_label": req_label,
            "similarity": candidate.get("similarity"),
            "source": "traceability-link-recovery",
        }

        if req_node:
            result["requirement_title"] = req_node.get("title", "")
            result["requirement_content"] = req_node.get("content", "")
            result["requirement_content_html"] = req_node.get("content_html", "")
            tables = req_node.get("tables", [])
            if tables:
                result["tables"] = tables
            images = req_node.get("images", [])
            if images:
                result["images"] = images

        return result
