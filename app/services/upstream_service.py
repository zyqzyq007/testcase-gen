"""
UpstreamService — 检测并解析上游工具（文档审查、需求追溯）的输出，
为测试用例生成提供需求上下文。

上游输出目录（位于源码 zip 解压文件夹内）：
  - document-validator/requirements.json        → 扁平化需求列表
    （上游工具不同版本也会输出 requirement.json，两者等价、均支持）
  - traceability_link_recovery/traceability.json → 需求↔函数映射（仅取 src_rank=1，每个函数的最佳需求）
"""

import os
import json
import re
from typing import Optional, Dict, Any, List, Tuple

from app.services.project_service import ProjectService


class UpstreamService:
    """从共享卷读取上游工具输出，按函数名+源文件匹配需求上下文。"""

    # 上游文档审查工具的需求文件候选名（按优先级）。
    # 兼容上游不同版本输出：requirements.json（复数，历史默认）与
    # requirement.json（单数，新版）。两者格式完全一致，取到任意一个即可。
    REQUIREMENT_FILE_CANDIDATES = ["requirements.json", "requirement.json"]

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

    @staticmethod
    def _find_requirements_file(req_dir: str) -> Optional[str]:
        """
        在 document-validator 目录中定位需求 JSON 文件。

        兼容上游文档审查工具的两种输出命名：
          - requirements.json（复数，历史默认，优先）
          - requirement.json（单数）

        匹配顺序：
          1. 按候选名逐个精确匹配（大小写敏感，文件名完全一致）；
          2. 兜底：对目录内条目做大小写不敏感匹配 requirements?.json，
             以应对上游命名漂移（如 Requirements.json）。
        任意一步命中即返回其完整路径，全部未命中返回 None。
        """
        if not req_dir:
            return None
        # 1) 候选名精确匹配
        for name in UpstreamService.REQUIREMENT_FILE_CANDIDATES:
            candidate = os.path.join(req_dir, name)
            if os.path.isfile(candidate):
                return candidate
        # 2) 兜底：大小写不敏感匹配，应对上游命名漂移
        pattern = re.compile(r"^requirements?\.json$", re.IGNORECASE)
        try:
            for entry in os.listdir(req_dir):
                if pattern.match(entry):
                    full = os.path.join(req_dir, entry)
                    if os.path.isfile(full):
                        return full
        except OSError:
            return None
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
        trace_dir = UpstreamService._find_dir_in_project(project_path, "traceability_link_recovery")

        req_file = UpstreamService._find_requirements_file(req_dir) if req_dir else None
        trace_file = os.path.join(trace_dir, "traceability.json") if trace_dir else None

        req_data = UpstreamService._load_json_file(req_file)
        trace_data = UpstreamService._load_json_file(trace_file)

        req_count = len(req_data) if isinstance(req_data, list) else 0
        trace_links = trace_data.get("trace_links", []) if isinstance(trace_data, dict) else []
        # 仅统计 src_rank=1 的链接（每个函数的最佳需求匹配）
        primary_links = [l for l in trace_links if l.get("src_rank") == 1]

        return {
            "has_requirements": req_count > 0,
            "has_traceability": len(primary_links) > 0,
            "requirement_count": req_count,
            "trace_link_count": len(primary_links),
        }

    @staticmethod
    def _load_requirements(project_id: str) -> Dict[str, Dict[str, Any]]:
        """
        加载需求文档，返回 { id_or_title: requirement_node } 的映射。
        需求文件（requirements.json 或 requirement.json，由 _find_requirements_file 解析）
        是数组，每个元素有 id、title、content、tables、images 等。
        同时以 id 和 title 为 key 建立索引，方便通过 requirement_label 查找。
        """
        project_path = ProjectService.get_project_path(project_id)
        req_dir = UpstreamService._find_dir_in_project(project_path, "document-validator")
        if not req_dir:
            return {}
        req_file = UpstreamService._find_requirements_file(req_dir)
        if not req_file:
            return {}
        req_list = UpstreamService._load_json_file(req_file)
        if not isinstance(req_list, list):
            return {}
        result = {}
        for node in req_list:
            req_id = (node.get("id") or "").strip()
            title = (node.get("title") or "").strip()
            if req_id:
                result[req_id] = node
            if title and title != req_id:
                result[title] = node
        return result

    @staticmethod
    def _load_all_trace_links(project_id: str) -> List[Dict[str, Any]]:
        """加载所有追溯链路（不过滤 rank），供展示用。"""
        project_path = ProjectService.get_project_path(project_id)
        trace_dir = UpstreamService._find_dir_in_project(project_path, "traceability_link_recovery")
        if not trace_dir:
            return []
        trace_file = os.path.join(trace_dir, "traceability.json")
        trace_data = UpstreamService._load_json_file(trace_file)
        if not isinstance(trace_data, dict):
            return []
        return trace_data.get("trace_links", [])

    @staticmethod
    def _load_traceability(project_id: str) -> List[Dict[str, Any]]:
        """
        加载追溯链路，只返回 src_rank=1（每个函数的最佳匹配需求）的链接，
        保证需求与代码一对一。供测试生成使用。
        """
        all_links = UpstreamService._load_all_trace_links(project_id)
        return [link for link in all_links if link.get("src_rank") == 1]

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
        strict_rank1: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        根据函数名和源文件路径，从上游数据中获取需求上下文。

        strict_rank1=False（前端展示）：加载全量链接，按 src_rank 升序取最佳匹配。
        strict_rank1=True（测试生成）：仅匹配 src_rank=1，保证一对一。

        匹配策略：
          1. 在链接中查找 function_name 精确匹配
          2. source_file 做 basename/后缀匹配
          3. 按 src_rank 升序 + similarity 降序排，取最佳
          4. 用匹配到的 requirement_label 去需求文件（requirements.json / requirement.json）查找需求内容

        返回:
          {
            "requirement_label": "req10",
            "requirement_title": "3.2.2 配置项初始化",
            "requirement_content": "配置项初始化功能需求见表2...",
            "tables": [...],
            "similarity": 0.53,
            "source": "traceability_link_recovery"
          }
          或 None（无匹配）。
        """
        # 加载链接：展示用全量，生成用仅 src_rank=1
        if strict_rank1:
            trace_links = UpstreamService._load_traceability(project_id)
        else:
            trace_links = UpstreamService._load_all_trace_links(project_id)
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
        # 若文件名匹配为空，回退到仅函数名匹配
        candidates = by_file if by_file else by_name

        # Step 3: 按 src_rank 升序 + similarity 降序 取最佳
        candidate = min(candidates, key=lambda x: (x.get("src_rank", 999), -(x.get("similarity", 0))))

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
            "src_rank": candidate.get("src_rank"),
            "source": "traceability_link_recovery",
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
