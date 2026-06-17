import os
import shutil
import tarfile
import zipfile
import uuid
from datetime import datetime
from fastapi import UploadFile, HTTPException
from typing import List, Tuple, Dict, Any, Optional

# 源码读取目录：集成模式下指向共享卷（只读），独立模式下指向本地 workspaces/
WORKSPACES_DIR = os.path.abspath(os.getenv("UNIPORTAL_STORAGE_PATH", "workspaces"))
# 集成模式标志：由 UNIPORTAL_STORAGE_PATH 是否被设置决定
UNIPORTAL_MODE = bool(os.getenv("UNIPORTAL_STORAGE_PATH"))
# 本工具私有可读写目录：存放缓存、图谱、CPG 等生成物（独立模式与 WORKSPACES_DIR 相同）
LOCAL_WORKSPACES_DIR = os.path.abspath(os.getenv("LOCAL_WORKSPACES_DIR", "workspaces"))

import json

class ProjectService:
    @staticmethod
    def _default_framework_for_language(language: str) -> str:
        return "pytest" if language == "python" else "unity"

    @staticmethod
    def _scan_project_characteristics(project_dir: str) -> Dict[str, Any]:
        counts = {
            "c": 0,
            "h": 0,
            "py": 0,
            "json": 0,
        }
        files_seen = set()
        dependency_manager = "pip"

        for root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in {".git", ".venv", "venv", "__pycache__", "node_modules", "_cache", "_tasks", ".pytest_cache", ".conda_env"}]
            for f in files:
                rel_path = os.path.relpath(os.path.join(root, f), project_dir)
                files_seen.add(rel_path)
                lower = f.lower()
                if lower.endswith(".c"):
                    counts["c"] += 1
                elif lower.endswith(".h"):
                    counts["h"] += 1
                elif lower.endswith(".py"):
                    counts["py"] += 1
                elif lower.endswith(".json"):
                    counts["json"] += 1

        if "pyproject.toml" in files_seen:
            dependency_manager = "pyproject"
        elif "requirements.txt" in files_seen:
            dependency_manager = "pip"

        language = "c"
        if counts["py"] > 0 and counts["py"] >= counts["c"]:
            language = "python"
        elif counts["c"] == 0 and counts["py"] == 0:
            language = "unknown"

        source_count = counts["py"] if language == "python" else (counts["c"] + counts["h"])

        return {
            "language": language,
            "dependency_manager": dependency_manager,
            "counts": counts,
            "source_count": source_count,
        }

    @staticmethod
    def _meta_path(project_id: str) -> str:
        base_dir = LOCAL_WORKSPACES_DIR if UNIPORTAL_MODE else WORKSPACES_DIR
        return os.path.join(base_dir, project_id, "meta.json")

    @staticmethod
    def get_project_meta(project_id: str) -> Dict[str, Any]:
        meta_path = ProjectService._meta_path(project_id)
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    # conda-pack 环境包识别时优先匹配的文件名前缀（大小写不敏感）
    _CONDA_ENV_HINTS = ("env", "environment", "conda", "venv")

    @staticmethod
    def _looks_like_conda_env_name(fname: str) -> bool:
        """文件名是否暗示这是一个 conda-pack 环境包（如 env.tar.gz）。"""
        lower = fname.lower()
        base = lower.rsplit(".", 1)[0] if lower.endswith((".gz", ".bz2")) else lower
        base = base.replace("-", "_").replace(".", "_")
        return any(base == hint or base.startswith(hint + "_") for hint in ProjectService._CONDA_ENV_HINTS)

    @staticmethod
    def _archive_is_conda_env(path: str) -> bool:
        """
        打开压缩包窥探，判断顶层（或第一层目录下）是否含 bin/ + conda-meta/，
        这是 conda-pack 打包环境的典型结构。
        支持 .tar.gz / .tgz / .tar.bz2 / .zip。
        """
        lower = path.lower()
        try:
            if lower.endswith((".tar.gz", ".tgz")):
                opener = lambda p: tarfile.open(p, "r:gz")
            elif lower.endswith(".tar.bz2"):
                opener = lambda p: tarfile.open(p, "r:bz2")
            elif lower.endswith(".tar"):
                opener = lambda p: tarfile.open(p, "r:")
            elif lower.endswith(".zip"):
                opener = lambda p: zipfile.ZipFile(p, "r")
            else:
                return False
            with opener(path) as arc:
                if isinstance(arc, zipfile.ZipFile):
                    names = arc.namelist()
                else:
                    names = arc.getnames()
        except Exception:
            return False

        # 规范化成员名：去掉开头的 "/" 或 "./"，便于统一匹配
        # （conda-pack 打包出来是根级 bin/、conda-meta/，无 "./" 前缀；
        #  而 `tar -czf x .` 打包则带 "./" 前缀，两种都要兼容）
        norm = []
        for n in names:
            n = n.lstrip("/")
            if n.startswith("./"):
                n = n[2:]
            norm.append(n)
        names = norm

        def _has_marker(prefixes):
            for p in prefixes:
                # p 为 "" (根级) 或 "dirname/" (单层包裹目录)
                has_bin = any(n == p + "bin" or n.startswith(p + "bin/") for n in names)
                has_meta = any(n.startswith(p + "conda-meta/") for n in names)
                if has_bin and has_meta:
                    return True
            return False

        # 情况1: 顶层直接是 bin/ + conda-meta/（conda-pack 默认输出）
        # 情况2: 单层包裹目录 <dir>/bin/ + <dir>/conda-meta/
        prefixes = [""]
        # 收集所有"第一层目录"作为候选前缀
        first_dirs = set()
        for n in names:
            if "/" in n:
                first_dirs.add(n.split("/")[0])
        prefixes += [d + "/" for d in first_dirs]
        return _has_marker(prefixes)

    @staticmethod
    def _extract_conda_env(archive_path: str, dest_dir: str) -> bool:
        """
        把 conda-pack 环境包解压到 dest_dir，保证 dest_dir 下直接是 bin/ conda-meta/。
        若包内多了一层包裹目录，则把内层上移。
        解压成功返回 True。
        """
        os.makedirs(dest_dir, exist_ok=True)
        lower = archive_path.lower()
        try:
            if lower.endswith((".tar.gz", ".tgz")):
                with tarfile.open(archive_path, "r:gz") as t:
                    t.extractall(dest_dir)
            elif lower.endswith(".tar.bz2"):
                with tarfile.open(archive_path, "r:bz2") as t:
                    t.extractall(dest_dir)
            elif lower.endswith(".tar"):
                with tarfile.open(archive_path, "r:") as t:
                    t.extractall(dest_dir)
            elif lower.endswith(".zip"):
                with zipfile.ZipFile(archive_path, "r") as z:
                    z.extractall(dest_dir)
            else:
                return False
        except Exception:
            return False

        # 如果解出来多了一层包裹目录，把内层内容上移
        if not (os.path.isdir(os.path.join(dest_dir, "bin")) and os.path.isdir(os.path.join(dest_dir, "conda-meta"))):
            for entry in os.listdir(dest_dir):
                inner = os.path.join(dest_dir, entry)
                if os.path.isdir(inner) and os.path.isdir(os.path.join(inner, "bin")) and os.path.isdir(os.path.join(inner, "conda-meta")):
                    # 把 inner/* 移到 dest_dir
                    for item in os.listdir(inner):
                        shutil.move(os.path.join(inner, item), os.path.join(dest_dir, item))
                    try:
                        os.rmdir(inner)
                    except OSError:
                        pass
                    break
        return os.path.isdir(os.path.join(dest_dir, "bin")) and os.path.isdir(os.path.join(dest_dir, "conda-meta"))

    @staticmethod
    def _safe_extract_tar(archive_path: str, dest_dir: str) -> bool:
        """把普通 tar 源码包解压到 dest_dir（非 conda 环境包的通用解压）。成功返回 True。"""
        os.makedirs(dest_dir, exist_ok=True)
        lower = archive_path.lower()
        try:
            if lower.endswith((".tar.gz", ".tgz")):
                mode = "r:gz"
            elif lower.endswith(".tar.bz2"):
                mode = "r:bz2"
            elif lower.endswith(".tar"):
                mode = "r:"
            else:
                return False
            with tarfile.open(archive_path, mode) as t:
                t.extractall(dest_dir)
        except Exception:
            return False
        return True

    @staticmethod
    def _scan_for_conda_env(project_dir: str) -> Optional[Dict[str, Any]]:
        """
        扫描项目目录（最多 3 层）查找 conda-pack 环境包。
        返回 {"archive_path": ..., "conda_env_dir": ".conda_env"} 或 None。
        解压后的环境统一放入 project_dir/.conda_env。
        """
        candidates = []
        for root, dirs, files in os.walk(project_dir):
            depth = root[len(project_dir):].count(os.sep)
            if depth > 3:
                dirs[:] = []
                continue
            for f in files:
                lower = f.lower()
                if not lower.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar", ".zip")):
                    continue
                # 跳过源码 zip 本身的二次嵌套、以及 meta.json 等
                full = os.path.join(root, f)
                # 优先：名字暗示是环境包；否则也允许内容判断兜底
                name_hit = ProjectService._looks_like_conda_env_name(f)
                content_hit = ProjectService._archive_is_conda_env(full)
                if name_hit or content_hit:
                    candidates.append((full, name_hit, content_hit))

        if not candidates:
            return None
        # 排序：内容确认为准优先，其次名字命中
        candidates.sort(key=lambda x: (not x[2], not x[1]))
        archive_path = candidates[0][0]

        return {
            "archive_path": archive_path,
            "conda_env_dir": ".conda_env",
        }

    @staticmethod
    async def create_project(file: UploadFile, project_name: str = None) -> Tuple[str, str, int]:
        project_id = f"proj_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        # 集成模式下写入本工具私有的可读写目录，避免写入只读的共享卷
        save_dir = LOCAL_WORKSPACES_DIR if UNIPORTAL_MODE else WORKSPACES_DIR
        project_dir = os.path.join(save_dir, project_id)
        os.makedirs(project_dir, exist_ok=True)

        if not project_name:
            project_name = file.filename.split('.')[0]

        file_location = os.path.join(project_dir, file.filename)
        
        # Save uploaded file
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_count = 0
        lower_name = file.filename.lower()
        is_zip = lower_name.endswith(".zip")
        is_tar = lower_name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar"))
        is_srcfile = lower_name.endswith((".c", ".h", ".py"))

        env_source = "none"
        conda_env_dir = None

        if is_zip:
            with zipfile.ZipFile(file_location, 'r') as zip_ref:
                zip_ref.extractall(project_dir)
            os.remove(file_location)
        elif is_tar:
            # tar 族：可能是源码包，也可能是用户直接上传的 conda-pack 环境包本身
            if ProjectService._archive_is_conda_env(file_location):
                # 直接就是离线环境包 → 解到 .conda_env，无需再扫描
                dest = os.path.join(project_dir, ".conda_env")
                if ProjectService._extract_conda_env(file_location, dest):
                    env_source = "conda_pack"
                    conda_env_dir = ".conda_env"
                os.remove(file_location)
            else:
                # 当作源码包解压（其中可能仍嵌套 env.tar.gz，交给下面扫描处理）
                ProjectService._safe_extract_tar(file_location, project_dir)
                os.remove(file_location)
        elif is_srcfile:
            file_count = 1

        # 扫描并解压嵌套的 conda-pack 离线环境包（Python 项目专用）
        # 直接上传的环境包已在上面处理；这里只处理 zip / 源码包 tar / 单 .py 内嵌套的 env
        if (is_zip or is_tar or file.filename.endswith(".py")) and env_source == "none":
            env_info = ProjectService._scan_for_conda_env(project_dir)
            if env_info:
                dest = os.path.join(project_dir, env_info["conda_env_dir"])
                ok = ProjectService._extract_conda_env(env_info["archive_path"], dest)
                if ok:
                    env_source = "conda_pack"
                    conda_env_dir = env_info["conda_env_dir"]
                    # 删除原始环境包压缩文件，节省空间
                    try:
                        os.remove(env_info["archive_path"])
                    except OSError:
                        pass

        # Scan for function design documents (JSON files with function_design structure)
        design_docs = {}
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                if not f.endswith('.json') or f == 'meta.json':
                    continue
                json_path = os.path.join(root, f)
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    # Support both {"function_design": {...}} and direct array/list of such objects
                    entries = []
                    if isinstance(data, dict) and 'function_design' in data:
                        entries = [data]
                    elif isinstance(data, list):
                        entries = [item for item in data if isinstance(item, dict) and 'function_design' in item]
                    for entry in entries:
                        fd = entry['function_design']
                        fname = fd.get('basic_info', {}).get('function_name', '').strip()
                        if fname:
                            design_docs[fname] = fd
                except Exception:
                    pass

        scan_info = ProjectService._scan_project_characteristics(project_dir)
        language = scan_info["language"]
        dependency_manager = scan_info["dependency_manager"]
        test_framework = ProjectService._default_framework_for_language(language)
        file_count = scan_info["source_count"]

        # Save metadata
        meta = {
            "project_name": project_name,
            "created_at": datetime.now().isoformat(),
            "original_filename": file.filename,
            "design_docs": design_docs,
            "language": language,
            "test_framework": test_framework,
            "dependency_manager": dependency_manager,
            "env_source": env_source,
            "conda_env_dir": conda_env_dir,
        }
        with open(os.path.join(project_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return project_id, project_name, file_count

    @staticmethod
    def get_function_design_doc(project_id: str, function_name: str, qualified_name: str = None) -> dict:
        """
        按函数名查找项目中的设计文档（function_design 结构）。
        优先查找 function_name，其次尝试 qualified_name（Python 类方法）。
        优先从 meta.json 中读取上传时扫描到的 design_docs，
        若 meta.json 不含该字段则实时扫描项目目录中的 JSON 文件。
        返回 function_design 字典，未找到则返回 None。
        """
        if not function_name:
            return None

        # 构建候选名列表：优先精确名，再尝试 Python qualified name
        candidates = [function_name]
        if qualified_name and qualified_name != function_name:
            candidates.append(qualified_name)
            # 也尝试用 "." 替换 "__" 的形式（有些设计文档用 Python module 风格）
            candidates.append(qualified_name.replace(".", "__"))

        # Try reading from meta.json first (fastest path)
        base_dir = LOCAL_WORKSPACES_DIR if UNIPORTAL_MODE else WORKSPACES_DIR
        meta_path = os.path.join(base_dir, project_id, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                if 'design_docs' in meta:
                    for candidate in candidates:
                        if candidate in meta['design_docs']:
                            return meta['design_docs'][candidate]
            except Exception:
                pass
        # Fallback: live scan of JSON files in the project directory
        try:
            project_dir = ProjectService.get_project_path(project_id)
        except Exception:
            return None
        for root, dirs, files in os.walk(project_dir):
            for fname in files:
                if not fname.endswith('.json') or fname == 'meta.json':
                    continue
                json_path = os.path.join(root, fname)
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    entries = []
                    if isinstance(data, dict) and 'function_design' in data:
                        entries = [data]
                    elif isinstance(data, list):
                        entries = [item for item in data if isinstance(item, dict) and 'function_design' in item]
                    for entry in entries:
                        fd = entry['function_design']
                        doc_name = fd.get('basic_info', {}).get('function_name', '').strip()
                        if doc_name in candidates:
                            return fd
                except Exception:
                    pass
        return None

    @staticmethod
    def has_design_doc(project_id: str) -> bool:
        """
        判断该项目是否包含任意设计文档（meta.json 里的 design_docs 非空，
        或者项目目录下有含 function_design 结构的 JSON 文件）。
        """
        base_dir = LOCAL_WORKSPACES_DIR if UNIPORTAL_MODE else WORKSPACES_DIR
        meta_path = os.path.join(base_dir, project_id, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                if meta.get('design_docs'):
                    return True
            except Exception:
                pass
        # Fallback: live scan
        try:
            project_dir = ProjectService.get_project_path(project_id)
        except Exception:
            return False
        for root, _, files in os.walk(project_dir):
            for fname in files:
                if not fname.endswith('.json') or fname == 'meta.json':
                    continue
                try:
                    with open(os.path.join(root, fname), 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    if isinstance(data, dict) and 'function_design' in data:
                        return True
                    if isinstance(data, list) and any(
                        isinstance(i, dict) and 'function_design' in i for i in data
                    ):
                        return True
                except Exception:
                    pass
        return False

    @staticmethod
    def get_local_project_dir(project_id: str) -> str:
        """
        返回本工具私有的可读写目录（用于存 cpg.bin、graphs/、_cache/ 等生成物）。
        集成模式：LOCAL_WORKSPACES_DIR/{project_id}（挂载到读写卷）
        独立模式：与 get_project_path 相同
        """
        local_dir = os.path.join(LOCAL_WORKSPACES_DIR, project_id)
        os.makedirs(local_dir, exist_ok=True)
        return local_dir

    @staticmethod
    def _build_item_index() -> dict:
        """
        集成模式专用：遍历共享卷两层目录，建立
          { item_id: abs_item_path } 的索引
        目录结构：{portal_project_id}/{item_id}/{zip解压文件夹}/源码
        """
        index = {}
        if not os.path.exists(WORKSPACES_DIR):
            return index
        for proj_dir in os.listdir(WORKSPACES_DIR):
            proj_path = os.path.join(WORKSPACES_DIR, proj_dir)
            if not os.path.isdir(proj_path) or proj_dir.startswith('.'):
                continue
            for item_id in os.listdir(proj_path):
                item_path = os.path.join(proj_path, item_id)
                if os.path.isdir(item_path) and not item_id.startswith(('.', '_')):
                    index[item_id] = item_path
        return index

    @staticmethod
    def get_project_path(project_id: str) -> str:
        if UNIPORTAL_MODE:
            # 优先查 UniPortal 共享卷:
            #   private 卷里可能存在以 item_id 命名的"空壳目录"
            #   (上次跑测试时留下的 _cache/、cpg.bin、graphs/ 等生成物),
            #   如果先查 private 会被空壳遮挡, 拿不到真正的源码.
            #   反转优先级是安全的: UniPortal item_id 是纯 UUID,
            #   私有上传 project_id 是 proj_ 前缀, 两个命名空间不冲突.
            index = ProjectService._build_item_index()
            path = index.get(project_id)
            if path and os.path.exists(path):
                return path
            # 共享卷找不到时再回退到私有目录 (用户自行上传的 proj_xxxx 项目)
            local_path = os.path.join(LOCAL_WORKSPACES_DIR, project_id)
            if os.path.exists(local_path):
                return local_path
            raise HTTPException(status_code=404, detail="Project not found")
        else:
            path = os.path.join(WORKSPACES_DIR, project_id)
            if not os.path.exists(path):
                raise HTTPException(status_code=404, detail="Project not found")
            return path

    @staticmethod
    def list_files(project_id: str) -> List[str]:
        project_dir = ProjectService.get_project_path(project_id)
        file_paths = []
        for root, dirs, files in os.walk(project_dir):
            # 不展示系统/环境相关目录：_cache、_tasks、.conda_env（用户上传的离线 conda 环境）、
            # .venv/venv（工具自动建的虚拟环境）、各种缓存。这些都不应被解析为项目函数或展示给用户浏览。
            dirs[:] = [d for d in dirs if d not in {
                "_cache", "_tasks", ".git", "__pycache__",
                ".venv", "venv", "node_modules", ".pytest_cache",
                ".conda_env",  # conda-pack 离线环境（含海量 site-packages 的 .py/.txt）
            }]
            for file in files:
                if file.endswith(('.c', '.h', '.py', '.json', '.toml', '.txt')):
                    # Skip meta.json at the project root
                    full = os.path.join(root, file)
                    rel_path = os.path.relpath(full, project_dir)
                    if rel_path == 'meta.json':
                        continue
                    if file not in {"requirements.txt", "pyproject.toml", "pytest.ini", "setup.py"} and not file.endswith(('.c', '.h', '.py', '.json')):
                        continue
                    file_paths.append(rel_path)
        return sorted(file_paths)

    @staticmethod
    def get_file_content(project_id: str, file_path: str) -> str:
        project_dir = ProjectService.get_project_path(project_id)
        # Prevent directory traversal
        full_path = os.path.abspath(os.path.join(project_dir, file_path))
        if not full_path.startswith(project_dir):
             raise HTTPException(status_code=400, detail="Invalid file path")
        
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="File not found")
            
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    @staticmethod
    def get_project_name(project_id: str) -> str:
        meta = ProjectService.get_project_meta(project_id)
        if meta.get("project_name"):
            return meta["project_name"]

        # Use the actual source directory (not LOCAL_WORKSPACES_DIR which only has artifacts)
        try:
            project_dir = ProjectService.get_project_path(project_id)
        except Exception:
            project_dir = os.path.join(LOCAL_WORKSPACES_DIR if UNIPORTAL_MODE else WORKSPACES_DIR, project_id)

        # Fallback for old projects: try to find a meaningful name
        try:
            items = os.listdir(project_dir)
            # 1. If there's a single directory, use it (exclude generated artifact dirs)
            dirs = [d for d in items
                    if os.path.isdir(os.path.join(project_dir, d))
                    and not d.startswith(('_', '.'))
                    and d not in {"graphs", "__pycache__", ".venv", "venv"}]
            if len(dirs) == 1:
                return dirs[0]

            # 2. Look for any .c or .py file and use its parent directory name
            for root, _, files in os.walk(project_dir):
                src_files = [f for f in files if f.endswith(('.c', '.py'))]
                if src_files:
                    rel_dir = os.path.relpath(root, project_dir)
                    if rel_dir != '.':
                        return rel_dir.split(os.sep)[0]
                    return src_files[0].split('.')[0]
        except:
            pass

        # 3. Last resort: use the random part of the ID instead of the date
        parts = project_id.split('_')
        return parts[-1] if len(parts) > 1 else project_id

    @staticmethod
    def get_project_language(project_id: str) -> str:
        meta = ProjectService.get_project_meta(project_id)
        if meta.get("language"):
            return meta["language"]
        project_dir = ProjectService.get_project_path(project_id)
        scan_info = ProjectService._scan_project_characteristics(project_dir)
        return scan_info["language"]

    @staticmethod
    def get_project_framework(project_id: str) -> str:
        meta = ProjectService.get_project_meta(project_id)
        if meta.get("test_framework"):
            return meta["test_framework"]
        return ProjectService._default_framework_for_language(ProjectService.get_project_language(project_id))

    @staticmethod
    def delete_project(project_id: str) -> bool:
        if UNIPORTAL_MODE:
            # 集成模式下只允许删除本工具私有目录中用户自行上传的项目
            local_path = os.path.join(LOCAL_WORKSPACES_DIR, project_id.strip())
            if not os.path.exists(local_path):
                raise HTTPException(
                    status_code=403,
                    detail="集成模式下不允许删除 UniPortal 项目，请通过 UniPortal 管理。"
                )
            try:
                shutil.rmtree(local_path)
                return True
            except Exception as e:
                print(f"Error deleting project {project_id}: {e}")
                return False
        # Debug logging
        print(f"DEBUG: Attempting to delete project_id: {repr(project_id)}")
        
        # Clean up project_id just in case (strip whitespace/newlines)
        project_id = project_id.strip()
        
        project_dir = os.path.join(WORKSPACES_DIR, project_id)
        print(f"DEBUG: Target directory: {project_dir}, Exists: {os.path.exists(project_dir)}")
        
        if not os.path.exists(project_dir):
            return False
        try:
            shutil.rmtree(project_dir)
            return True
        except Exception as e:
            print(f"Error deleting project {project_id}: {e}")
            return False

    @staticmethod
    def list_projects(portal_project_id: str = None) -> List[dict]:
        # portal_project_id: 当从 UniPortal iframe 进入时传入，仅列出该工程下的 item；
        # 为 None 时表示子工具被独立访问，不列出任何 UniPortal 项目（避免跨工程暴露数据）。
        # 私有上传（source="local"）永远列出。
        if not os.path.exists(WORKSPACES_DIR):
            return []

        projects = []

        if UNIPORTAL_MODE:
            # 集成模式：目录结构为 {portal_project_id}/{item_id}/{zip解压文件夹}/源码
            # project_id = item_id（单段 UUID，不含斜杠，URL 路由安全）
            # project_name = item 下第一层子文件夹名（即 zip 解压出的文件夹名）
            if portal_project_id:
                # 仅扫描指定工程目录下的 item
                proj_path = os.path.join(WORKSPACES_DIR, portal_project_id)
                if os.path.isdir(proj_path):
                    index = {}
                    for item_id in os.listdir(proj_path):
                        item_path = os.path.join(proj_path, item_id)
                        if os.path.isdir(item_path) and not item_id.startswith(('.', '_')):
                            index[item_id] = item_path
                else:
                    index = {}
            else:
                # 未指定工程：不列出任何 UniPortal 项目（仅显示下方私有上传）
                index = {}
            for item_id, item_path in sorted(index.items()):
                scan_info = ProjectService._scan_project_characteristics(item_path)
                display_name = item_id  # 兜底用 item_id
                try:
                    sub_dirs = [
                        d for d in os.listdir(item_path)
                        if os.path.isdir(os.path.join(item_path, d))
                        and not d.startswith(('.', '_'))
                    ]
                    if sub_dirs:
                        display_name = sub_dirs[0]  # 取第一个子目录名作为展示名
                except Exception:
                    pass
                projects.append({
                    "project_id": item_id,
                    "project_name": display_name,
                    "file_count": scan_info["source_count"],
                    "status": "available",
                    "source": "uniportal",
                    "language": scan_info["language"],
                    "test_framework": ProjectService._default_framework_for_language(scan_info["language"]),
                    "dependency_manager": scan_info["dependency_manager"],
                })

            # 合并本工具私有目录中用户自行上传的项目（proj_ 前缀）
            if os.path.exists(LOCAL_WORKSPACES_DIR):
                for d in os.listdir(LOCAL_WORKSPACES_DIR):
                    path = os.path.join(LOCAL_WORKSPACES_DIR, d)
                    if os.path.isdir(path) and d.startswith("proj_"):
                        meta = ProjectService.get_project_meta(d)
                        scan_info = ProjectService._scan_project_characteristics(path)
                        project_name = ProjectService.get_project_name(d)
                        projects.append({
                            "project_id": d,
                            "project_name": project_name,
                            "file_count": scan_info["source_count"],
                            "status": "available",
                            "source": "local",
                            "language": meta.get("language", scan_info["language"]),
                            "test_framework": meta.get("test_framework", ProjectService._default_framework_for_language(scan_info["language"])),
                            "dependency_manager": meta.get("dependency_manager", scan_info["dependency_manager"]),
                            "env_source": meta.get("env_source", "none"),
                        })
        else:
            # 独立模式：目录结构为 proj_YYYYMMDD_xxxx/
            for d in os.listdir(WORKSPACES_DIR):
                path = os.path.join(WORKSPACES_DIR, d)
                if os.path.isdir(path) and d.startswith("proj_"):
                    meta = ProjectService.get_project_meta(d)
                    scan_info = ProjectService._scan_project_characteristics(path)
                    project_name = ProjectService.get_project_name(d)
                    projects.append({
                        "project_id": d,
                        "project_name": project_name,
                        "file_count": scan_info["source_count"],
                        "status": "available",
                        "source": "local",
                        "language": meta.get("language", scan_info["language"]),
                        "test_framework": meta.get("test_framework", ProjectService._default_framework_for_language(scan_info["language"])),
                        "dependency_manager": meta.get("dependency_manager", scan_info["dependency_manager"]),
                        "env_source": meta.get("env_source", "none"),
                    })
            projects = sorted(projects, key=lambda x: x['project_id'], reverse=True)

        return projects
