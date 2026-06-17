import os
import json
import uuid
import shutil
import subprocess
import asyncio
import re
from datetime import datetime
from typing import Tuple, Dict, Any, Optional, List
from app.models.testcase import TestResultDetail, ExecuteTestResponse, TestCoverage, FileCoverage, TestCoverageDetail, FunctionCoverageDetail
from app.services.project_service import ProjectService

TASKS_DIR = os.path.abspath("workspaces/_tasks")
UNITY_SRC_DIR = os.path.abspath("resources/unity")
# Timeouts for Python task stages (seconds)
PYTHON_VENV_TIMEOUT = int(os.getenv("PYTHON_VENV_TIMEOUT", "60"))
PYTHON_INSTALL_TIMEOUT = int(os.getenv("PYTHON_INSTALL_TIMEOUT", "300"))
PYTEST_TIMEOUT = int(os.getenv("PYTEST_TIMEOUT", "300"))

class RunnerService:
    @staticmethod
    def _get_test_file_name(metadata: Dict[str, Any]) -> str:
        language = metadata.get("language", "c")
        return metadata.get("test_file_name") or ("test_generated.py" if language == "python" else "test_runner.c")

    @staticmethod
    def _parse_lcov_info(info_path: str, base_dir: str) -> List[FileCoverage]:
        files_coverage = []
        current_file = None
        
        # File-level counters
        l_total = 0; l_covered = 0
        b_total = 0; b_covered = 0
        f_total = 0; f_covered = 0
        
        # Function-level data mapping
        # function_map: {func_name: {'start_line': int, 'end_line': int, 'l_total': 0, 'l_covered': 0, ...}}
        # But LCOV info usually gives:
        # FN:line,name
        # FNDA:count,name
        # It DOES NOT give end_line. So it's hard to map lines to functions strictly from LCOV info alone 
        # without parsing source code or assuming functions are sequential.
        # However, typical approach is:
        # 1. Read FN records to know function start lines.
        # 2. Sort functions by start line.
        # 3. Assign lines/branches to the function with the nearest start line <= line number.
        
        functions_info = {} # name -> {'line': int, 'execution_count': int}
        lines_data = {} # line_no -> execution_count
        branches_data = {} # line_no -> list of taken counts
        
        if not os.path.exists(info_path):
            return []

        with open(info_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('SF:'):
                    current_file = line[3:]
                    l_total = 0; l_covered = 0
                    b_total = 0; b_covered = 0
                    f_total = 0; f_covered = 0
                    functions_info = {}
                    lines_data = {}
                    branches_data = {}
                    
                elif line.startswith('FN:'): # FN:line,name
                    parts = line[3:].split(',')
                    if len(parts) >= 2:
                        func_line = int(parts[0])
                        func_name = parts[1]
                        functions_info[func_name] = {'line': func_line, 'execution_count': 0}
                        # print(f"DEBUG: Found function {func_name} at line {func_line}")
                        
                elif line.startswith('FNDA:'): # FNDA:count,name
                    parts = line[5:].split(',')
                    if len(parts) >= 2:
                        count = int(parts[0])
                        func_name = parts[1]
                        if func_name in functions_info:
                            functions_info[func_name]['execution_count'] = count

                elif line.startswith('DA:'): # DA:line,count
                    parts = line[3:].split(',')
                    if len(parts) >= 2:
                        ln = int(parts[0])
                        cnt = int(parts[1])
                        lines_data[ln] = cnt
                        
                elif line.startswith('BRDA:'): # BRDA:line,block,branch,taken
                    parts = line[5:].split(',')
                    if len(parts) >= 4:
                        ln = int(parts[0])
                        taken = parts[3]
                        cnt = int(taken) if taken != '-' else 0
                        if ln not in branches_data:
                            branches_data[ln] = []
                        branches_data[ln].append(cnt)

                elif line == 'end_of_record':
                    if current_file and ("test_runner.c" not in current_file and "unity.c" not in current_file):
                        # Calculate File Totals
                        l_total = len(lines_data)
                        l_covered = sum(1 for c in lines_data.values() if c > 0)
                        
                        b_total = sum(len(brs) for brs in branches_data.values())
                        b_covered = sum(sum(1 for c in brs if c > 0) for brs in branches_data.values())
                        
                        f_total = len(functions_info)
                        f_covered = sum(1 for f in functions_info.values() if f['execution_count'] > 0)

                        # Calculate Function Details
                        # Sort functions by start line
                        sorted_funcs = sorted(functions_info.items(), key=lambda x: x[1]['line'])
                        func_details = []
                        # print(f"DEBUG: Processing {len(sorted_funcs)} functions for {current_file}")
                        
                        for i, (name, info) in enumerate(sorted_funcs):
                            start_line = info['line']
                            # End line is start of next function - 1, or infinity (max line in file)
                            if i < len(sorted_funcs) - 1:
                                end_line = sorted_funcs[i+1][1]['line'] - 1
                            else:
                                end_line = max(lines_data.keys()) if lines_data else start_line + 1000
                                
                            # Aggregate lines for this function
                            fl_total = 0; fl_covered = 0
                            fb_total = 0; fb_covered = 0
                            
                            for ln, cnt in lines_data.items():
                                if start_line <= ln <= end_line:
                                    fl_total += 1
                                    if cnt > 0: fl_covered += 1
                                    
                            for ln, brs in branches_data.items():
                                if start_line <= ln <= end_line:
                                    fb_total += len(brs)
                                    fb_covered += sum(1 for c in brs if c > 0)
                                    
                            func_details.append(FunctionCoverageDetail(
                                name=name,
                                line=TestCoverageDetail(covered=fl_covered, total=fl_total, rate=fl_covered/fl_total if fl_total else 1.0),
                                branch=TestCoverageDetail(covered=fb_covered, total=fb_total, rate=fb_covered/fb_total if fb_total else 1.0)
                            ))

                        # Calculate relative path from base_dir
                        try:
                            rel_file = os.path.relpath(current_file, base_dir)
                        except ValueError:
                            rel_file = os.path.basename(current_file)
                        
                        files_coverage.append(FileCoverage(
                            file=rel_file,
                            line=TestCoverageDetail(covered=l_covered, total=l_total, rate=l_covered/l_total if l_total else 1.0),
                            function=TestCoverageDetail(covered=f_covered, total=f_total, rate=f_covered/f_total if f_total else 1.0),
                            branch=TestCoverageDetail(covered=b_covered, total=b_total, rate=b_covered/b_total if b_total else 1.0),
                            functions=func_details,
                            lines=lines_data  # Pass the line execution data
                        ))
        return files_coverage

    @staticmethod
    def _parse_gcov_file(gcov_path: str, source_file: str) -> FileCoverage:
        lines_total = 0
        lines_covered = 0
        branches_total = 0
        branches_covered = 0
        
        # Naive parsing of gcov output (or lcov .info if we switched to that)
        # But gcov .c.gcov files are textual.
        # Format:
        #        -:    0:Source:foo.c
        #        1:    1:int add(int a, int b) {
        #    #####:    2:    return a + b;
        # branch  0 taken 0% (fallthrough)
        # branch  1 taken 100%
        
        # However, getting branch info from plain gcov requires -b flag and parsing is tricky.
        # Let's use lcov as suggested by user for easier parsing? 
        # User said: "Parsing to your JSON... read .gcov or lcov output"
        # LCOV info format is easier to parse standardly.
        
        # Let's stick to gcov -b -c for now if we want text, OR use lcov -> coverage.info -> parse
        # LCOV flow:
        # lcov --capture --directory . --output-file coverage.info
        # Then parse coverage.info
        pass

    @staticmethod
    async def _run_subprocess(cmd: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None, timeout: Optional[int] = None):
        """Run a subprocess with a timeout. Returns (returncode, stdout_bytes, stderr_bytes, timed_out_bool)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            return (None, b"", str(e).encode(), False)

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return (proc.returncode, out or b"", err or b"", False)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                out, err = await proc.communicate()
            except Exception:
                out, err = b"", b""
            return (proc.returncode, out or b"", err or b"", True)

    @staticmethod
    def _parse_lcov_info(info_path: str, base_dir: str) -> List[FileCoverage]:
        files_coverage = []
        current_file = None
        
        # File-level counters
        l_total = 0; l_covered = 0
        b_total = 0; b_covered = 0
        f_total = 0; f_covered = 0
        
        functions_info = {} # name -> {'line': int, 'execution_count': int}
        lines_data = {} # line_no -> execution_count
        branches_data = {} # line_no -> list of taken counts
        
        if not os.path.exists(info_path):
            return []

        with open(info_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('SF:'):
                    current_file = line[3:]
                    # Ensure current_file is absolute for relpath calculation if it's not
                    if not os.path.isabs(current_file):
                         current_file = os.path.join(base_dir, current_file)
                    
                    l_total = 0; l_covered = 0
                    b_total = 0; b_covered = 0
                    f_total = 0; f_covered = 0
                    functions_info = {}
                    lines_data = {}
                    branches_data = {}
                    
                elif line.startswith('FN:'): # FN:line,name
                    parts = line[3:].split(',')
                    if len(parts) >= 2:
                        func_line = int(parts[0])
                        func_name = parts[1]
                        functions_info[func_name] = {'line': func_line, 'execution_count': 0}
                        
                elif line.startswith('FNDA:'): # FNDA:count,name
                    parts = line[5:].split(',')
                    if len(parts) >= 2:
                        count = int(parts[0])
                        func_name = parts[1]
                        if func_name in functions_info:
                            functions_info[func_name]['execution_count'] = count

                elif line.startswith('DA:'): # DA:line,count
                    parts = line[3:].split(',')
                    if len(parts) >= 2:
                        ln = int(parts[0])
                        cnt = int(parts[1])
                        lines_data[ln] = cnt
                        
                elif line.startswith('BRDA:'): # BRDA:line,block,branch,taken
                    parts = line[5:].split(',')
                    if len(parts) >= 4:
                        ln = int(parts[0])
                        taken = parts[3]
                        cnt = int(taken) if taken != '-' else 0
                        if ln not in branches_data:
                            branches_data[ln] = []
                        branches_data[ln].append(cnt)

                elif line == 'end_of_record':
                    if current_file and ("test_runner.c" not in current_file and "unity.c" not in current_file):
                        # Calculate File Totals
                        l_total = len(lines_data)
                        l_covered = sum(1 for c in lines_data.values() if c > 0)
                        
                        b_total = sum(len(brs) for brs in branches_data.values())
                        b_covered = sum(sum(1 for c in brs if c > 0) for brs in branches_data.values())
                        
                        f_total = len(functions_info)
                        f_covered = sum(1 for f in functions_info.values() if f['execution_count'] > 0)

                        # Calculate Function Details
                        sorted_funcs = sorted(functions_info.items(), key=lambda x: x[1]['line'])
                        func_details = []
                        
                        for i, (name, info) in enumerate(sorted_funcs):
                            start_line = info['line']
                            if i < len(sorted_funcs) - 1:
                                end_line = sorted_funcs[i+1][1]['line'] - 1
                            else:
                                end_line = max(lines_data.keys()) if lines_data else start_line + 1000
                                
                            fl_total = 0; fl_covered = 0
                            fb_total = 0; fb_covered = 0
                            
                            for ln, cnt in lines_data.items():
                                if start_line <= ln <= end_line:
                                    fl_total += 1
                                    if cnt > 0: fl_covered += 1
                                    
                            for ln, brs in branches_data.items():
                                if start_line <= ln <= end_line:
                                    fb_total += len(brs)
                                    fb_covered += sum(1 for c in brs if c > 0)
                                    
                            func_details.append(FunctionCoverageDetail(
                                name=name,
                                line=TestCoverageDetail(covered=fl_covered, total=fl_total, rate=fl_covered/fl_total if fl_total else 1.0),
                                branch=TestCoverageDetail(covered=fb_covered, total=fb_total, rate=fb_covered/fb_total if fb_total else 1.0)
                            ))

                        # Calculate relative path from base_dir
                        try:
                            rel_file = os.path.relpath(current_file, base_dir)
                        except ValueError:
                            rel_file = os.path.basename(current_file)
                        
                        files_coverage.append(FileCoverage(
                            file=rel_file,
                            line=TestCoverageDetail(covered=l_covered, total=l_total, rate=l_covered/l_total if l_total else 1.0),
                            function=TestCoverageDetail(covered=f_covered, total=f_total, rate=f_covered/f_total if f_total else 1.0),
                            branch=TestCoverageDetail(covered=b_covered, total=b_total, rate=b_covered/b_total if b_total else 1.0),
                            functions=func_details,
                            lines=lines_data
                        ))
        return files_coverage
    @staticmethod
    def create_task(
        project_id: str,
        function_id: str,
        test_code: str,
        source_file_path: str,
        function_name: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        language: str = "c",
        test_framework: str = "unity",
    ) -> str:
        task_id = str(uuid.uuid4())
        task_dir = os.path.join(TASKS_DIR, task_id)
        os.makedirs(task_dir, exist_ok=True)
        test_file_name = "test_generated.py" if language == "python" else "test_runner.c"

        metadata = {
            "project_id": project_id,
            "function_id": function_id,
            "function_name": function_name,
            "source_file_path": source_file_path,
            "created_at": str(datetime.now()),
            "start_line": start_line,
            "end_line": end_line,
            "language": language,
            "test_framework": test_framework,
            "test_file_name": test_file_name,
        }
        
        with open(os.path.join(task_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f)
            
        with open(os.path.join(task_dir, test_file_name), "w") as f:
            f.write(test_code)
            
        return task_id

    @staticmethod
    def get_task_code(task_id: str) -> Optional[str]:
        task_dir = os.path.join(TASKS_DIR, task_id)
        meta_path = os.path.join(task_dir, "metadata.json")
        test_file_name = "test_runner.c"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    metadata = json.load(f)
                test_file_name = RunnerService._get_test_file_name(metadata)
            except Exception:
                pass
        code_path = os.path.join(task_dir, test_file_name)
        if not os.path.exists(code_path):
            return None
        with open(code_path, "r") as f:
            return f.read()

    @staticmethod
    def get_task_metadata(task_id: str) -> Optional[Dict[str, Any]]:
        task_dir = os.path.join(TASKS_DIR, task_id)
        meta_path = os.path.join(task_dir, "metadata.json")
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, "r") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def get_task_status(task_id: str) -> Dict[str, Any]:
        task_dir = os.path.join(TASKS_DIR, task_id)
        meta_path = os.path.join(task_dir, "metadata.json")
        if not os.path.exists(meta_path):
            return None
        with open(meta_path, "r") as f:
            return json.load(f)

    @staticmethod
    def _parse_pytest_summary(output: str) -> Tuple[int, int, int]:
        passed = failed = ignored = 0
        summary_match = re.search(r"=+ .*? in [\d.]+s =+", output)
        summary_text = summary_match.group(0) if summary_match else output
        for pattern, bucket in [
            (r"(\d+)\s+passed", "passed"),
            (r"(\d+)\s+failed", "failed"),
            (r"(\d+)\s+skipped", "ignored"),
            (r"(\d+)\s+error", "failed"),
        ]:
            m = re.search(pattern, summary_text)
            if not m:
                continue
            count = int(m.group(1))
            if bucket == "passed":
                passed += count
            elif bucket == "failed":
                failed += count
            else:
                ignored += count
        return passed, failed, ignored

    @staticmethod
    def _parse_python_coverage(
        coverage_json_path: str,
        task_dir: str,
        target_file_rel: str,
        function_name: str,
        start_line: Optional[int],
        end_line: Optional[int],
    ) -> Optional[TestCoverage]:
        if not os.path.exists(coverage_json_path):
            return None
        with open(coverage_json_path, "r") as f:
            cov = json.load(f)

        files = cov.get("files", {})
        if not files:
            return None

        target_norm = os.path.normpath(target_file_rel)
        selected_rel = None
        selected_data = None
        for file_path, file_data in files.items():
            rel = os.path.normpath(os.path.relpath(file_path, task_dir)) if os.path.isabs(file_path) else os.path.normpath(file_path)
            if rel == target_norm or rel.endswith(target_norm):
                selected_rel = rel
                selected_data = file_data
                break
        if not selected_data:
            return None

        executed_lines = set(selected_data.get("executed_lines", []))
        missing_lines = set(selected_data.get("missing_lines", []))
        line_map = {ln: 1 for ln in executed_lines}
        for ln in missing_lines:
            line_map.setdefault(ln, 0)
        instrumented_lines = sorted(line_map.keys())

        executed_branches = {
            tuple(branch[:2])
            for branch in selected_data.get("executed_branches", [])
            if isinstance(branch, (list, tuple)) and len(branch) >= 2
        }
        missing_branches = {
            tuple(branch[:2])
            for branch in selected_data.get("missing_branches", [])
            if isinstance(branch, (list, tuple)) and len(branch) >= 2
        }
        all_branches = executed_branches | missing_branches

        summary = selected_data.get("summary", {})
        line_total = summary.get("num_statements", len(instrumented_lines))
        line_covered = summary.get("covered_lines", len(executed_lines))
        branch_total = len(all_branches) if all_branches else summary.get("num_branches", 0)
        branch_covered = len(executed_branches) if all_branches else summary.get("covered_branches", 0)

        func_lines = []
        if start_line is not None and end_line is not None:
            func_lines = [ln for ln in instrumented_lines if start_line <= ln <= end_line]
        func_total = len(func_lines)
        func_covered = sum(1 for ln in func_lines if line_map.get(ln, 0) > 0)

        func_branches = []
        if start_line is not None and end_line is not None and all_branches:
            func_branches = [branch for branch in all_branches if start_line <= branch[0] <= end_line]
        func_branch_total = len(func_branches)
        func_branch_covered = sum(1 for branch in func_branches if branch in executed_branches)

        file_cov = FileCoverage(
            file=selected_rel,
            line=TestCoverageDetail(
                covered=line_covered,
                total=line_total,
                rate=line_covered / line_total if line_total else 1.0,
            ),
            function=TestCoverageDetail(
                covered=1 if func_covered > 0 else 0,
                total=1 if func_total > 0 else 0,
                rate=1.0 if func_covered > 0 else 0.0 if func_total > 0 else 1.0,
            ),
            branch=TestCoverageDetail(
                    covered=func_branch_covered,
                    total=func_branch_total,
                    rate=func_branch_covered / func_branch_total if func_branch_total else 1.0,
            ),
            functions=[
                FunctionCoverageDetail(
                    name=function_name,
                    line=TestCoverageDetail(
                        covered=func_covered,
                        total=func_total,
                        rate=func_covered / func_total if func_total else 1.0,
                    ),
                    branch=TestCoverageDetail(
                        covered=func_branch_covered,
                        total=func_branch_total,
                        rate=func_branch_covered / func_branch_total if func_branch_total else 1.0,
                    ),
                )
            ],
            lines=line_map,
        )
        return TestCoverage(scope="function", files=[file_cov])

    @staticmethod
    def _save_cache_result(metadata: Dict[str, Any], task_id: str, compile_success: bool, passed: int = 0, failed: int = 0, ignored: int = 0, line_rate: float = 0.0, branch_rate: float = 0.0):
        try:
            from app.services.cache_service import CacheService
            if metadata.get("project_id") and metadata.get("source_file_path") and metadata.get("function_name"):
                CacheService.save_function_data(
                    metadata["project_id"],
                    metadata["source_file_path"],
                    metadata["function_name"],
                    {
                        "latest_task_id": task_id,
                        "last_execution_time": str(datetime.now()),
                        "compile_success": compile_success,
                        "passed": passed,
                        "failed": failed,
                        "ignored": ignored,
                        "line_coverage": line_rate,
                        "branch_coverage": branch_rate,
                    }
                )
        except Exception as e:
            print(f"Failed to save execution result to cache: {e}")

    @staticmethod
    def _compute_deps_fingerprint(project_dir: str) -> str:
        """计算项目依赖文件的指纹，用于判断是否需要重新安装。"""
        import hashlib
        h = hashlib.sha256()
        for fname in ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"):
            fpath = os.path.join(project_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "rb") as f:
                        h.update(fname.encode() + b"\x00" + f.read())
                except Exception:
                    pass
        return h.hexdigest()

    @staticmethod
    async def _prepare_project_venv(
        project_id: str, project_dir: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        准备项目级别的持久化 venv，返回 (python_bin, pip_bin, error_msg)。
        - venv 存放在项目可写目录下，跨任务复用
        - 通过依赖文件指纹判断是否需要重新安装
        - 离线环境下若 venv 已存在且依赖已满足则跳过安装
        - 并发安全：批量首跑时多个任务会同时进入，用 mkdir 原子锁保证只有一个
          任务真正创建/安装 .venv，其余任务等待 .venv 就绪后复用，避免并发写冲突
          导致 venv 损坏或 pip 安装不完整。
        """
        local_dir = ProjectService.get_local_project_dir(project_id)
        venv_dir = os.path.join(local_dir, ".venv")
        hash_file = os.path.join(local_dir, ".venv.deps.hash")
        python_bin = os.path.join(venv_dir, "bin", "python")
        pip_bin = os.path.join(venv_dir, "bin", "pip")
        lock_dir = os.path.join(local_dir, ".venv.lock")

        current_hash = RunnerService._compute_deps_fingerprint(project_dir)

        def _read_cached_hash() -> str:
            if os.path.exists(hash_file):
                try:
                    with open(hash_file, "r") as f:
                        return f.read().strip()
                except Exception:
                    pass
            return ""

        def _venv_ready() -> bool:
            return os.path.exists(python_bin) and _read_cached_hash() == current_hash

        # 快路径：venv 已就绪，直接复用（无需锁）
        if _venv_ready():
            return python_bin, pip_bin, None

        # 需要构建 → 抢锁（mkdir 是原子操作）
        def _try_acquire() -> bool:
            try:
                os.mkdir(lock_dir)
                return True
            except FileExistsError:
                return False

        acquired = _try_acquire()
        if not acquired:
            # 等待持锁者完成构建（最多约 PYTHON_INSTALL_TIMEOUT/2 秒）
            wait_steps = max(1, PYTHON_INSTALL_TIMEOUT // 2)
            for _ in range(wait_steps):
                if _venv_ready():
                    return python_bin, pip_bin, None
                await asyncio.sleep(1)
            # 超时后再试抢一次锁（持锁者可能已崩溃）
            acquired = _try_acquire()

        if acquired:
            try:
                # 拿到锁后再次核对：等待期间可能已被别的任务建好
                if _venv_ready():
                    return python_bin, pip_bin, None

                cached_hash = _read_cached_hash()
                venv_exists = os.path.exists(python_bin)
                deps_changed = current_hash != cached_hash or not cached_hash

                # --- 创建或重建 venv ---
                if deps_changed and venv_exists:
                    shutil.rmtree(venv_dir, ignore_errors=True)
                    venv_exists = False
                if not venv_exists:
                    rc, _, venv_err, timed_out = await RunnerService._run_subprocess(
                        ["python3", "-m", "venv", "--system-site-packages", ".venv"],
                        cwd=local_dir,
                        timeout=PYTHON_VENV_TIMEOUT,
                    )
                    if timed_out or rc != 0:
                        return None, None, (
                            "Virtualenv creation timed out"
                            if timed_out
                            else f"Virtualenv creation failed:\n{venv_err.decode(errors='replace')}"
                        )

                # --- 安装依赖（仅在指纹变更时执行）---
                if deps_changed:
                    install_cmds = [
                        [pip_bin, "install", "--disable-pip-version-check", "-q", "pytest", "coverage", "pytest-cov"],
                    ]
                    pyproject_path = os.path.join(project_dir, "pyproject.toml")
                    if os.path.exists(pyproject_path):
                        install_cmds.append(
                            [pip_bin, "install", "--disable-pip-version-check", "-q", "-e", "."]
                        )
                    requirements_path = os.path.join(project_dir, "requirements.txt")
                    if os.path.exists(requirements_path):
                        install_cmds.append(
                            [pip_bin, "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt"]
                        )

                    install_logs = []
                    all_ok = True
                    for cmd in install_cmds:
                        rc, out, err, timed_out = await RunnerService._run_subprocess(
                            cmd, cwd=project_dir, timeout=PYTHON_INSTALL_TIMEOUT
                        )
                        install_logs.append(out.decode(errors="replace"))
                        install_logs.append(err.decode(errors="replace"))
                        if timed_out or rc != 0:
                            all_ok = False

                    if not all_ok:
                        # 离线场景容错：如果 pytest + coverage 已经可用，继续执行
                        rc_check, _, _, _ = await RunnerService._run_subprocess(
                            [python_bin, "-c", "import pytest, coverage"],
                            timeout=10,
                        )
                        if rc_check != 0:
                            msg = "Dependency installation failed"
                            return None, None, msg + ":\n" + "\n".join(install_logs)

                    # 记录指纹（仅在安装成功时）
                    try:
                        with open(hash_file, "w") as f:
                            f.write(current_hash)
                    except Exception:
                        pass

                return python_bin, pip_bin, None
            finally:
                try:
                    os.rmdir(lock_dir)
                except OSError:
                    pass

        # 既没拿到锁、等待也超时：最后兜底——若 venv 至少可用就复用，否则报错
        if os.path.exists(python_bin):
            return python_bin, pip_bin, None
        return None, None, "Virtualenv is being prepared by another task; please retry shortly."

    @staticmethod
    async def _ensure_conda_unpacked(
        env_dir: str, env_python: str, run_env: Dict[str, str]
    ) -> None:
        """
        一次性执行 conda-unpack（幂等 + 并发安全）。
        - 用 .unpacked 标记文件避免重复执行
        - 用 mkdir 作为原子文件锁，保证并发的批量任务只有一个执行 unpack
        - conda-unpack 用 env 自带 python（PYTHONHOME 引导）启动，重写构建机旧前缀
        - 失败视为非致命（真 conda-pack 的 bin/python 本就用相对路径，开箱即用）
        """
        marker = os.path.join(env_dir, ".unpacked")
        if os.path.exists(marker):
            return

        lock_dir = os.path.join(env_dir, ".unpack.lock")
        got_lock = False
        try:
            os.mkdir(lock_dir)  # 原子操作：成功者获得锁
            got_lock = True
        except FileExistsError:
            pass

        if not got_lock:
            # 等待持锁任务完成（最多 ~90s），完成后直接返回
            for _ in range(180):
                if os.path.exists(marker):
                    return
                await asyncio.sleep(0.5)
            return

        try:
            unpack_script = os.path.join(env_dir, "bin", "conda-unpack")
            cmd = None
            if os.path.exists(unpack_script):
                cmd = [env_python, unpack_script]
            elif shutil.which("conda"):
                cmd = ["conda", "unpack", "--prefix", env_dir]
            if cmd:
                rc, out, err, timed_out = await RunnerService._run_subprocess(
                    cmd, cwd=env_dir, env=run_env, timeout=PYTHON_INSTALL_TIMEOUT
                )
                if timed_out:
                    print("[conda-unpack] timed out (non-fatal)")
                elif rc != 0:
                    msg = (err.decode(errors='replace') if err else "") or (out.decode(errors='replace') if out else "")
                    print(f"[conda-unpack] non-zero rc={rc} (non-fatal): {msg[:300]}")
            try:
                with open(marker, "w") as f:
                    f.write(datetime.now().isoformat())
            except Exception:
                pass
        finally:
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass

    @staticmethod
    async def _prepare_conda_env(
        project_id: str, project_dir: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        准备 conda-pack 离线环境，返回 (python_bin, env_dir_abs, error_msg)。

        版本隔离原则：必须使用 env 自带的 Python（如 3.8），绝不回退到 Docker 宿主
        Python（否则 C 扩展 .so 版本不匹配，且 PYTHONHOME 会让宿主 Python 崩溃）。

        流程：
        1. 用 _build_conda_env_vars（含 PYTHONHOME=env_dir）测试 env 自带 python；
           真 conda-pack 的 bin/python 用相对路径解析 stdlib，开箱即用。
        2. 若裸测失败，运行一次 conda-unpack（重写构建机旧前缀）后重试。
        3. 仍不可用则硬失败，给出清晰错误（不静默回退系统 Python）。
        """
        meta = ProjectService.get_project_meta(project_id)
        conda_env_rel = meta.get("conda_env_dir") or ".conda_env"
        env_dir = os.path.join(project_dir, conda_env_rel)

        if not os.path.isdir(env_dir):
            return None, None, f"Conda env directory not found: {conda_env_rel}"

        env_python = os.path.join(env_dir, "bin", "python")
        if not os.path.exists(env_python):
            return None, None, f"Python interpreter not found in conda env: {env_python}"

        run_env = RunnerService._build_conda_env_vars(env_dir)

        async def _env_python_ok() -> bool:
            rc, _, _, _ = await RunnerService._run_subprocess(
                [env_python, "-c", "import sys; print(sys.version.split()[0])"],
                env=run_env, timeout=20,
            )
            return rc == 0

        # 1) 直接测试 env 自带 python
        if not await _env_python_ok():
            # 2) 尝试 conda-unpack 修复前缀后重试
            await RunnerService._ensure_conda_unpacked(env_dir, env_python, run_env)
            ok = await _env_python_ok()
        else:
            ok = True

        if not ok:
            return None, None, (
                "Conda env Python interpreter is not functional.\n"
                f"  interpreter: {env_python}\n"
                "The packed environment may be corrupted, built for an incompatible "
                "platform, or missing its bundled standard library.\n"
                "Please rebuild it with `conda pack` (not a plain venv), so that "
                "bin/python + lib/pythonX.Y/stdlib + site-packages are all bundled."
            )

        return env_python, env_dir, None

    @staticmethod
    def _build_conda_env_vars(env_dir: str) -> Dict[str, str]:
        """
        构造等价于 `source bin/activate` 的环境变量字典（供 subprocess env 合并用）。
        - PATH 前置 env_dir/bin
        - VIRTUAL_ENV 指向环境根（触发 Python venv 发现机制）
        - CONDA_PREFIX 指向环境根
        - LD_LIBRARY_PATH 前置 env_dir/lib（C 扩展 .so 依赖）
        - 若缺少 pyvenv.cfg，则手动将 env 内 site-packages 加入 PYTHONPATH
        """
        env = os.environ.copy()
        env["PATH"] = os.path.join(env_dir, "bin") + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = env_dir
        # PYTHONHOME 让 env 自带 Python 二进制从 env/lib/pythonX.Y 加载 stdlib，
        # 不依赖宿主机 Python 路径（实现真正的版本隔离）
        env["PYTHONHOME"] = env_dir
        env["CONDA_PREFIX"] = env_dir
        env["CONDA_DEFAULT_ENV"] = os.path.basename(env_dir)
        lib_dir = os.path.join(env_dir, "lib")
        if os.path.isdir(lib_dir):
            env["LD_LIBRARY_PATH"] = lib_dir + os.pathsep + env.get("LD_LIBRARY_PATH", "")

        # 兜底：把 env 内 site-packages 加入 PYTHONPATH，解决跨 Python 版本
        # （如 env 用 3.8 构建但宿主运行时是 3.10）导致 venv 发现机制找不到
        # site-packages 的问题。
        # 关键：只加入【与 bin/python 实际版本匹配】的 site-packages，避免多版本
        # site-packages 互相污染（3.8 解释器加载 3.10 编译的 .so 会 ImportError）。
        # 版本通过 bin/python 的真实指向（python3.X）推断，无需额外子进程。
        py_tag = None
        try:
            real_py = os.path.realpath(os.path.join(env_dir, "bin", "python"))
            m = re.match(r"python(\d+\.\d+)$", os.path.basename(real_py))
            if m:
                py_tag = "python" + m.group(1)
        except Exception:
            pass

        sp_dirs = []
        if py_tag:
            sp = os.path.join(lib_dir, py_tag, "site-packages")
            if os.path.isdir(sp):
                sp_dirs.append(sp)
        if not sp_dirs and os.path.isdir(lib_dir):
            # 回退：推断失败（罕见）时才无差别扫描
            for entry in sorted(os.listdir(lib_dir)):
                sp = os.path.join(lib_dir, entry, "site-packages")
                if os.path.isdir(sp):
                    sp_dirs.append(sp)
        if sp_dirs:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(sp_dirs) + (os.pathsep + existing if existing else "")
        return env

    @staticmethod
    async def _execute_python_task(task_id: str, task_dir: str, metadata: Dict[str, Any]) -> ExecuteTestResponse:
        project_dir = ProjectService.get_project_path(metadata["project_id"])

        def ignore_patterns(path, names):
            return [
                n
                for n in names
                if n in {".venv", "__pycache__", ".pytest_cache", ".conda_env"}
                or n.endswith((".pyc", ".pyo", ".coverage"))
            ]

        for item in os.listdir(project_dir):
            if item in ("_tasks", ".conda_env", ".venv"):
                continue
            s = os.path.join(project_dir, item)
            d = os.path.join(task_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True, ignore=ignore_patterns)
            else:
                shutil.copy2(s, d)

        test_file_name = RunnerService._get_test_file_name(metadata)

        # 根据项目是否携带 conda-pack 离线环境分流：
        #   conda_pack → 复用用户上传的预置环境（含解释器），全程不联网
        #   否则      → 回退到现有 _prepare_project_venv（联网 pip install）
        meta = ProjectService.get_project_meta(metadata["project_id"])
        env_source = meta.get("env_source", "none")

        conda_env_dir_abs = None
        pip_bin = None
        if env_source == "conda_pack":
            python_bin, conda_env_dir_abs, venv_error = await RunnerService._prepare_conda_env(
                metadata["project_id"], project_dir
            )
            if python_bin:
                pip_bin = os.path.join(os.path.dirname(python_bin), "pip")
        else:
            python_bin, pip_bin, venv_error = await RunnerService._prepare_project_venv(
                metadata["project_id"], project_dir
            )

        if venv_error:
            metadata["error"] = {"compile_error": venv_error, "stage": "venv"}
            with open(os.path.join(task_dir, "metadata.json"), "w") as f:
                json.dump(metadata, f, default=str)
            RunnerService._save_cache_result(metadata, task_id, False)
            return ExecuteTestResponse(
                task_id=task_id,
                compile_success=False,
                execution_started=False,
                language="python",
                test_framework=metadata.get("test_framework", "pytest"),
                install_success=False,
                stderr=venv_error,
            )

        # 构造运行环境变量：conda 环境用等价 activate 的变量集合，venv 用基础环境
        if conda_env_dir_abs:
            env = RunnerService._build_conda_env_vars(conda_env_dir_abs)
        else:
            env = os.environ.copy()
        env["PYTHONPATH"] = task_dir + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")

        # 预检：验证被测模块及其依赖在当前环境下是否可导入。
        # 关键：只对【依赖缺失】(ModuleNotFoundError) 短路报错并给出清晰提示；
        # 被测模块顶层副作用代码（读 argv、相对路径 open、联网等）抛出的其它异常
        # 不视为依赖问题——放行交由 pytest 处理，避免误报“依赖未安装”。
        source_file = metadata.get("source_file_path", "")
        if source_file.endswith(".py"):
            module_name = source_file.replace("/", ".").replace("\\", ".").removesuffix(".py")
            module_name = module_name.lstrip(".")
            # 模块名若以数字开头（如 "123.dir.module"），标准 import 语法报 SyntaxError
            # 此时改用 importlib 按文件路径加载，绕过标识符限制
            first_part = module_name.split(".")[0]
            if first_part and first_part[0].isdigit():
                # 文件路径导入
                py_path = os.path.join(task_dir, source_file)
                check_snippet = (
                    "import importlib.util as _u,sys;"
                    "try:\n"
                    f"  _s = _u.spec_from_file_location('_precheck', r'{py_path}');\n"
                    "  _m = _u.module_from_spec(_s); _s.loader.exec_module(_m)\n"
                    "except ModuleNotFoundError as _e:\n"
                    "  print('MISSING_DEP:', _e); sys.exit(2)\n"
                    "except Exception:\n"
                    "  pass\n"
                )
                rc_check, check_out, check_err, _ = await RunnerService._run_subprocess(
                    [python_bin, "-c", check_snippet],
                    cwd=task_dir, env=env, timeout=15,
                )
            else:
                check_snippet = (
                    "import sys\n"
                    "try:\n"
                    f"  import {module_name}\n"
                    "except ModuleNotFoundError as _e:\n"
                    "  print('MISSING_DEP:', _e); sys.exit(2)\n"
                    "except Exception:\n"
                    "  pass\n"
                )
                rc_check, check_out, check_err, _ = await RunnerService._run_subprocess(
                    [python_bin, "-c", check_snippet],
                    cwd=task_dir, env=env, timeout=15,
                )
            missing_dep = (rc_check == 2)  # 仅依赖缺失才短路
            if rc_check not in (0, 2):
                # 子进程启动失败等，保守放行
                missing_dep = False
            if missing_dep:
                err_text = (check_err.decode(errors="replace") if check_err else "") or (check_out.decode(errors="replace") if check_out else "")
                if env_source == "conda_pack":
                    dep_error = (
                        f"Module '{module_name}' failed to import in the conda env.\n"
                        f"A dependency is not included in env.tar.gz. Please rebuild the\n"
                        f"environment with `conda pack` after installing ALL project deps\n"
                        f"(e.g. `conda install numpy ...` or `pip install -r requirements.txt`)\n"
                        f"inside the env, then re-pack and re-upload.\n"
                        f"conda env dir: {conda_env_dir_abs}\n\n"
                        f"Import error:\n{err_text}"
                    )
                else:
                    dep_error = (
                        f"Module '{module_name}' failed to import.\n"
                        f"This means the project has dependencies that are not installed.\n"
                        f"Install them before running tests, or add them to the Docker image.\n\n"
                        f"Import error:\n{err_text}"
                    )
                metadata["error"] = {"compile_error": dep_error, "stage": "import_check"}
                with open(os.path.join(task_dir, "metadata.json"), "w") as f:
                    json.dump(metadata, f, default=str)
                RunnerService._save_cache_result(metadata, task_id, False)
                return ExecuteTestResponse(
                    task_id=task_id,
                    compile_success=False,
                    execution_started=False,
                    language="python",
                    test_framework=metadata.get("test_framework", "pytest"),
                    install_success=False,
                    stderr=dep_error,
                )

        # 确保 pytest-cov 可用（已有项目的 venv 缓存可能缺少该包）
        rc_pytest_cov, _, _, _ = await RunnerService._run_subprocess(
            [python_bin, "-c", "import pytest_cov"],
            cwd=task_dir, timeout=10)
        coverage_warning = ""
        if rc_pytest_cov != 0:
            # 在线/离线场景均尝试安装；若 pip 也失败则回退到 coverage run
            if pip_bin:
                pip_rc, _, pip_err, _ = await RunnerService._run_subprocess(
                    [pip_bin, "install", "--disable-pip-version-check", "-q", "pytest-cov"],
                    cwd=task_dir, timeout=60)
                if pip_rc != 0:
                    # 离线 conda 环境常装不上：把根因透出给用户，避免覆盖率静默为 0
                    detail = pip_err.decode(errors="replace").strip() if pip_err else "unknown error"
                    coverage_warning = (
                        "pytest-cov could not be installed in this environment "
                        "(likely offline conda env). Falling back to `coverage run`; "
                        "coverage statistics may be unavailable. Rebuild the env with "
                        "pytest-cov pre-installed for full coverage.\n"
                        f"pip stderr: {detail}"
                    )

        # 写入 conftest.py：显式将 task_dir 与包根加入 sys.path，
        # 解决 coverage/pytest-cov 在 venv 下 PYTHONPATH 被丢弃的问题，
        # 同时支持 src-layout（包根 ≠ task_dir）的多层包导入。
        # 注意：若项目自带 conftest.py，只【前置追加】路径注入，保留其原有内容
        # （fixtures / autouse 钩子 / 自定义插件等），绝不能整体覆盖。
        conftest_path = os.path.join(task_dir, "conftest.py")
        injection_lines = ["import sys", f"sys.path.insert(0, {task_dir!r})"]
        # 向上回溯到包根：第一个不含 __init__.py 的祖先目录
        cur = os.path.dirname(os.path.abspath(os.path.join(task_dir, source_file)))
        task_dir_abs = os.path.abspath(task_dir)
        seen = set()
        while cur and cur not in seen and os.path.isfile(os.path.join(cur, "__init__.py")):
            seen.add(cur)
            cur = os.path.dirname(cur)
        if cur and os.path.abspath(cur) != task_dir_abs:
            injection_lines.append(f"sys.path.insert(0, {cur!r})")
        injection = "\n".join(injection_lines) + "\n"
        if os.path.exists(conftest_path):
            try:
                with open(conftest_path, "r", encoding="utf-8") as f:
                    existing = f.read()
                with open(conftest_path, "w", encoding="utf-8") as f:
                    f.write(injection + "\n" + existing)
            except Exception:
                with open(conftest_path, "w", encoding="utf-8") as f:
                    f.write(injection)
        else:
            with open(conftest_path, "w", encoding="utf-8") as f:
                f.write(injection)

        # 根据 pytest-cov 是否可用选择测试命令
        use_pytest_cov = rc_pytest_cov == 0  # 之前已有或刚装好
        if not use_pytest_cov:
            rc_retry, _, _, _ = await RunnerService._run_subprocess(
                [python_bin, "-c", "import pytest_cov"],
                cwd=task_dir, timeout=10)
            use_pytest_cov = rc_retry == 0

        if use_pytest_cov:
            test_cmd = [python_bin, "-m", "pytest", "-q", "--tb=short",
                        "--cov", "--cov-branch", "--cov-report=json:coverage.json",
                        test_file_name]
        else:
            # fallback: coverage run 需要显式确保 PYTHONPATH 传递
            env["COVERAGE_FILE"] = os.path.join(task_dir, ".coverage")
            test_cmd = [python_bin, "-m", "coverage", "run", "--branch",
                        "-m", "pytest", "-q", test_file_name]

        rc, run_stdout, run_stderr, timed_out = await RunnerService._run_subprocess(
            test_cmd,
            cwd=task_dir,
            env=env,
            timeout=PYTEST_TIMEOUT,
        )
        output = (run_stdout.decode(errors='replace') if run_stdout else "") + "\n" + (run_stderr.decode(errors='replace') if run_stderr else "")
        if timed_out:
            output += "\n[System] pytest run timed out"
        passed, failed, ignored = RunnerService._parse_pytest_summary(output)
        if rc is None:
            # Subprocess failed to start; mark as failed
            if failed == 0:
                failed = 1
        else:
            if rc != 0 and failed == 0:
                failed = 1
        total = passed + failed + ignored

        # coverage run 回退分支只生成二进制 .coverage，不会产出 coverage.json；
        # 这里显式转换，否则 _parse_python_coverage 会静默返回 0 覆盖率。
        cov_json_path = os.path.join(task_dir, "coverage.json")
        if not use_pytest_cov and not os.path.exists(cov_json_path):
            await RunnerService._run_subprocess(
                [python_bin, "-m", "coverage", "json", "-o", "coverage.json"],
                cwd=task_dir, env=env, timeout=30)

        # pytest-cov generates coverage.json directly; no separate coverage step needed
        cov_timed_out = False
        coverage_data = RunnerService._parse_python_coverage(
            os.path.join(task_dir, "coverage.json"),
            task_dir,
            metadata["source_file_path"],
            metadata.get("function_name", ""),
            metadata.get("start_line"),
            metadata.get("end_line"),
        )

        metadata["result"] = {
            "passed": passed,
            "failed": failed,
            "ignored": ignored,
            "total": total,
            "stdout": output,
            "coverage": coverage_data.model_dump() if coverage_data else None,
        }
        with open(os.path.join(task_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, default=str)

        line_rate = 0.0
        branch_rate = 0.0
        if coverage_data and coverage_data.files and coverage_data.files[0].functions:
            func_cov = coverage_data.files[0].functions[0]
            line_rate = func_cov.line.rate
            branch_rate = func_cov.branch.rate
        RunnerService._save_cache_result(metadata, task_id, True, passed, failed, ignored, line_rate, branch_rate)

        source_code_content = None
        target_abs_path = os.path.join(task_dir, metadata["source_file_path"])
        if os.path.exists(target_abs_path):
            with open(target_abs_path, "r") as f:
                source_code_content = f.read()

        stderr_combined = (run_stderr.decode(errors='replace') if run_stderr else "")
        if coverage_warning:
            stderr_combined += "\n" + coverage_warning
        # pytest-cov includes coverage warnings/errors in the test output itself;
        # check if coverage.json was generated as a sanity check
        if cov_timed_out:
            stderr_combined += "\nCoverage report generation timed out"
        elif not os.path.exists(os.path.join(task_dir, "coverage.json")):
            stderr_combined += "\nCoverage report (coverage.json) was not generated"

        return ExecuteTestResponse(
            task_id=task_id,
            compile_success=True,
            execution_started=True,
            language="python",
            test_framework=metadata.get("test_framework", "pytest"),
            install_success=True,
            test_result=TestResultDetail(passed=passed, failed=failed, total=total),
            coverage=coverage_data,
            stdout=output,
            stderr=stderr_combined,
            source_code=source_code_content,
            function_start_line=metadata.get("start_line"),
            function_end_line=metadata.get("end_line"),
        )

    @staticmethod
    async def execute_task(task_id: str) -> ExecuteTestResponse:
        task_dir = os.path.join(TASKS_DIR, task_id)
        if not os.path.exists(task_dir):
            raise Exception("Task not found")
            
        with open(os.path.join(task_dir, "metadata.json"), "r") as f:
            metadata = json.load(f)

        if metadata.get("language") == "python":
            return await RunnerService._execute_python_task(task_id, task_dir, metadata)
            
        # 1. Setup Environment
        # Copy Unity files
        if not os.path.exists(UNITY_SRC_DIR):
             pass
        else:
            for f in ["unity.h", "unity.c", "unity_internals.h"]:
                src = os.path.join(UNITY_SRC_DIR, f)
                if os.path.exists(src):
                    shutil.copy(src, task_dir)
        
        # 2. Prepare Source
        # Copy the entire project to task_dir to ensure headers and other dependencies are available
        project_dir = ProjectService.get_project_path(metadata["project_id"])
        # Use shutil.copytree with ignore to avoid copying binary artifacts
        def ignore_patterns(path, names):
            return [n for n in names if n.endswith(('.o', '.gcda', '.gcno', '.info')) or n == 'coverage.info' or n == 'runner']

        for item in os.listdir(project_dir):
            if item == "_tasks": continue
            s = os.path.join(project_dir, item)
            d = os.path.join(task_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True, ignore=ignore_patterns)
            else:
                if not any(item.endswith(ext) for ext in ['.o', '.gcda', '.gcno', '.info']):
                    shutil.copy2(s, d)
        
        # The source file path is relative to project root
        target_file_rel = metadata["source_file_path"]
        function_name   = metadata.get("function_name", "")
        
        # 2.5 Make target file testable
        # Remove 'static' so internal functions are visible to the linker.
        # (We no longer inject __attribute__((weak)) at the source level because
        #  the regex approach is unreliable for complex C code.  Instead we use
        #  `objcopy --weaken-symbol` at the binary level after compilation — see
        #  the post-compile step below.)
        target_abs_path = os.path.join(task_dir, target_file_rel)
        if os.path.exists(target_abs_path):
            try:
                with open(target_abs_path, 'r') as f:
                    content = f.read()
                # Strip leading 'static ' (but not 'static inline')
                content = re.sub(r'^static\s+(?!inline)', '', content, flags=re.MULTILINE)
                with open(target_abs_path, 'w') as f:
                    f.write(content)
            except Exception as e:
                print(f"Failed to strip static: {e}")

        # 3. Compile
        # Implementation:
        
        # 0. Clean up previous coverage data recursively
        for root, dirs, files in os.walk(task_dir):
            for f in files:
                if f.endswith((".gcda", ".gcno", ".o", ".info")):
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass

        # 1. Compile target with coverage
        # We use -I. and also -I<project_root> and maybe -I<dir_of_target>
        target_dir = os.path.dirname(target_file_rel)
        include_paths = ["-I.", f"-I{task_dir}"]
        if target_dir:
            include_paths.append(f"-I{os.path.join(task_dir, target_dir)}")

        # Add -DUNITY_INCLUDE_DOUBLE to enable double precision assertions
        c_flags = ["-DUNITY_INCLUDE_DOUBLE"]

        cmd_compile_target = ["gcc", "-c", "-fprofile-arcs", "-ftest-coverage"] + c_flags + [target_file_rel] + include_paths
        proc_t = await asyncio.create_subprocess_exec(*cmd_compile_target, cwd=task_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout_t, stderr_t = await proc_t.communicate()
        
        if proc_t.returncode != 0:
             # Persist compile error to task metadata for later export
             compile_err_msg = f"Target compilation failed:\n{stderr_t.decode(errors='replace')}\n{stdout_t.decode(errors='replace')}"
             metadata["error"] = {"compile_error": compile_err_msg, "stage": "target_compile"}
             with open(os.path.join(task_dir, "metadata.json"), "w") as f:
                 json.dump(metadata, f, default=str)
             # Save compilation failure to cache
             try:
                 from app.services.cache_service import CacheService
                 if metadata.get("project_id") and metadata.get("source_file_path") and metadata.get("function_name"):
                     CacheService.save_function_data(
                         metadata["project_id"],
                         metadata["source_file_path"],
                         metadata["function_name"],
                         {
                             "latest_task_id": task_id,
                             "last_execution_time": str(datetime.now()),
                             "compile_success": False,
                             "passed": 0,
                             "failed": 0,
                             "ignored": 0,
                             "line_coverage": 0.0,
                             "branch_coverage": 0.0
                         }
                     )
             except Exception:
                 pass

             return ExecuteTestResponse(
                task_id=task_id,
                compile_success=False,
                execution_started=False,
                stderr=compile_err_msg
             )

        # 1b. Weaken all symbols in the target .o EXCEPT the function under test.
        # This is the correct, binary-level solution to "multiple definition" linker
        # errors when the LLM-generated test file redefines a helper from the same .c.
        # Approach:
        #   nm --defined-only -P <file>.o   → lists every symbol defined in the .o
        #   objcopy --weaken-symbol=<sym>   → demotes a symbol to WEAK so the
        #                                     strong definition in the test file wins
        # The target function itself must remain GLOBAL (strong) so gcov can trace it.
        target_obj = os.path.basename(target_file_rel).replace(".c", ".o")
        if function_name:
            try:
                nm_proc = await asyncio.create_subprocess_exec(
                    "nm", "--defined-only", "-P", target_obj,
                    cwd=task_dir,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                nm_out, _ = await nm_proc.communicate()
                # nm -P format: "<name> <type> <value> <size>"
                # We only want TEXT symbols (type T or t) that are NOT the target function
                weaken_args = []
                for line in nm_out.decode(errors='replace').splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] in ('T', 't'):
                        sym = parts[0]
                        if sym != function_name:
                            weaken_args.extend([f'--weaken-symbol={sym}'])
                if weaken_args:
                    objcopy_proc = await asyncio.create_subprocess_exec(
                        "objcopy", *weaken_args, target_obj,
                        cwd=task_dir,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    await objcopy_proc.communicate()
            except Exception as e:
                print(f"objcopy weaken step failed (non-fatal): {e}")

        # 2. Compile runner & unity
        cmd_compile_others = ["gcc", "-c", "test_runner.c", "unity.c"] + c_flags + include_paths
        proc_o = await asyncio.create_subprocess_exec(*cmd_compile_others, cwd=task_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout_o, stderr_o = await proc_o.communicate()
        
        if proc_o.returncode != 0:
             # Persist compile error to task metadata for later export
             compile_err_msg = f"Runner/Unity compilation failed:\n{stderr_o.decode(errors='replace')}\n{stdout_o.decode(errors='replace')}"
             metadata["error"] = {"compile_error": compile_err_msg, "stage": "runner_compile"}
             with open(os.path.join(task_dir, "metadata.json"), "w") as f:
                 json.dump(metadata, f, default=str)
             # Save Runner/Unity compilation failure to cache
             try:
                 from app.services.cache_service import CacheService
                 if metadata.get("project_id") and metadata.get("source_file_path") and metadata.get("function_name"):
                     CacheService.save_function_data(
                         metadata["project_id"],
                         metadata["source_file_path"],
                         metadata["function_name"],
                         {
                             "latest_task_id": task_id,
                             "last_execution_time": str(datetime.now()),
                             "compile_success": False,
                             "passed": 0,
                             "failed": 0,
                             "ignored": 0,
                             "line_coverage": 0.0,
                             "branch_coverage": 0.0
                         }
                     )
             except Exception:
                 pass

             return ExecuteTestResponse(
                task_id=task_id,
                compile_success=False,
                execution_started=False,
                stderr=compile_err_msg
             )

        # 3. Link
        # Note: gcc -c klib-master/kstring.c produces kstring.o in current dir
        # Add -lz for zlib support (klib requirement)
        target_obj = os.path.basename(target_file_rel).replace(".c", ".o")
        cmd_link = [
            "gcc", "-o", "runner", 
            "test_runner.o", "unity.o", target_obj, 
            "-lgcov", "--coverage", "-lm", "-lz"
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd_link, 
            cwd=task_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            # Persist link error to task metadata for later export
            link_err_msg = f"Linking failed:\n{stderr.decode(errors='replace')}\n{stdout.decode(errors='replace')}"
            metadata["error"] = {"compile_error": link_err_msg, "stage": "link"}
            with open(os.path.join(task_dir, "metadata.json"), "w") as f:
                json.dump(metadata, f, default=str)
            # Save linking failure to cache
            try:
                from app.services.cache_service import CacheService
                if metadata.get("project_id") and metadata.get("source_file_path") and metadata.get("function_name"):
                    CacheService.save_function_data(
                        metadata["project_id"],
                        metadata["source_file_path"],
                        metadata["function_name"],
                        {
                            "latest_task_id": task_id,
                            "last_execution_time": str(datetime.now()),
                            "compile_success": False,
                             "passed": 0,
                             "failed": 0,
                             "ignored": 0,
                             "line_coverage": 0.0,
                             "branch_coverage": 0.0
                         }
                     )
            except Exception:
                pass

            return ExecuteTestResponse(
                task_id=task_id,
                compile_success=False,
                execution_started=False,
                stderr=link_err_msg
            )
            
        # 4. Run
        proc_run = await asyncio.create_subprocess_exec(
            "./runner",
            cwd=task_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            run_stdout, run_stderr = await asyncio.wait_for(proc_run.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            try:
                proc_run.kill()
            except Exception:
                pass
            run_stdout = b""
            run_stderr = b"Execution timed out (10s limit exceeded)."
        
        # 5. Parse Results
        output = run_stdout.decode(errors='replace') + "\n" + run_stderr.decode(errors='replace')
        
        # Check for crash/assertion failure first
        is_crashed = proc_run.returncode != 0
        has_assertion_fail = "Assertion" in output and "failed" in output
        has_segfault = "Segmentation fault" in output
        
        # Official Unity output: "test_runner.c:20:test_add_basic:PASS"
        passed = output.count(":PASS")
        failed = output.count(":FAIL")
        ignored = output.count(":IGNORE")
        
        if proc_run.returncode != 0 and failed == 0:
             # Try to capture more details about the crash
             stderr_msg = run_stderr.decode(errors='replace')
             if not stderr_msg:
                 stderr_msg = f"Process terminated with signal {-proc_run.returncode}" if proc_run.returncode < 0 else f"Process exited with code {proc_run.returncode}"
             
             # Still count as failed
             failed = 1
             
             # Also append to stderr for user visibility
             run_stderr = (run_stderr.decode(errors='replace') + f"\n[System] Test runner crashed: {stderr_msg}").encode()
        
        total = passed + failed + ignored 
        
        # 6. Generate Coverage
        coverage_data = None
        try:
            # Check if .gcda files exist
            # os.listdir(task_dir)
            
            # Use gcov directly to debug/ensure generation
            # gcov source_file
            # The source file we compiled is target_file_name (e.g. sample.c)
            # It was compiled in task_dir.
            # cmd_gcov = ["gcov", target_file_name]
            # proc_gcov = await asyncio.create_subprocess_exec(*cmd_gcov, cwd=task_dir, ...)
            
            # lcov capture
            # lcov --capture --directory . --output-file coverage.info
            # IMPORTANT: we need to run lcov on the directory where gcda files are generated (task_dir)
            
            # 1. Capture all coverage in the task directory
            cmd_lcov = ["lcov", "--capture", "--directory", ".", "--output-file", "all_coverage.info", "--rc", "lcov_branch_coverage=1"]
            proc_lcov = await asyncio.create_subprocess_exec(
                *cmd_lcov,
                cwd=task_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc_lcov.communicate()
            
            # 2. Extract only the target source file to avoid interference from other files
            # target_file_rel is the relative path like 'klib-master/kmath.c'
            cmd_extract = ["lcov", "--extract", "all_coverage.info", f"*/{target_file_rel}", "--output-file", "coverage.info", "--rc", "lcov_branch_coverage=1"]
            proc_extract = await asyncio.create_subprocess_exec(
                *cmd_extract,
                cwd=task_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc_extract.communicate()
            
            # 3. If extraction failed or produced empty file, fall back to all_coverage.info
            info_path = os.path.join(task_dir, "coverage.info")
            if not os.path.exists(info_path) or os.path.getsize(info_path) < 100:
                info_path = os.path.join(task_dir, "all_coverage.info")

            # Parse the finalized coverage.info
            files_cov = RunnerService._parse_lcov_info(info_path, task_dir)
            
            # Sort functions in each file so the target function is first
            target_func_name = metadata.get("function_name")
            
            # If function_name is missing (old task), try to resolve it from function_id
            if not target_func_name and metadata.get("function_id"):
                try:
                    from app.services.parser_service import ParserService
                    import base64
                    parts = metadata["function_id"].rsplit('_', 1)
                    file_id = parts[0]
                    start_line = int(parts[1])
                    path = base64.urlsafe_b64decode(file_id).decode()
                    content = ProjectService.get_file_content(metadata["project_id"], path)
                    functions = ParserService.parse_functions(content, file_id, file_path=path, language=metadata.get("language", "c"))
                    for f in functions:
                        if f.start_line == start_line:
                            target_func_name = f.name
                            metadata["function_name"] = target_func_name
                            break
                except Exception:
                    pass

            if target_func_name and files_cov:
                for file_cov in files_cov:
                    if file_cov.functions:
                        # Find the target function if it exists in this file
                        target_idx = -1
                        for idx, func in enumerate(file_cov.functions):
                            if func.name == target_func_name:
                                target_idx = idx
                                break
                        
                        if target_idx > 0:
                            # Move it to the front
                            func = file_cov.functions.pop(target_idx)
                            file_cov.functions.insert(0, func)
                        elif target_idx == -1 and len(file_cov.functions) > 0:
                            # If target not found by name, but we have functions, 
                            # maybe it's a naming mismatch (e.g. static prefix removal).
                            # Try to find by line number if we had it, but we don't here easily.
                            # For now, just ensure the most "relevant" looking one is at front.
                            pass
            
            if files_cov:
                coverage_data = TestCoverage(scope="function", files=files_cov)
                
        except Exception as e:
            print(f"Coverage generation failed: {e}")
        
        # Save result
        metadata["result"] = {
            "passed": passed,
            "failed": failed,
            "ignored": ignored,
            "total": total,
            "stdout": output,
            "coverage": coverage_data.model_dump() if coverage_data else None
        }
        with open(os.path.join(task_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, default=str) # default=str to handle Pydantic/enums if needed

        # Save result to CacheService for persistence
        try:
             from app.services.cache_service import CacheService
             if metadata.get("project_id") and metadata.get("source_file_path") and metadata.get("function_name"):
                 # Extract line rate for the function
                 line_rate = 0.0
                 branch_rate = 0.0
                 if coverage_data and coverage_data.files:
                     # Find the target function coverage
                     for file_cov in coverage_data.files:
                         for func_cov in file_cov.functions:
                             if func_cov.name == metadata["function_name"]:
                                 line_rate = func_cov.line.rate
                                 branch_rate = func_cov.branch.rate
                                 break
                 
                 CacheService.save_function_data(
                     metadata["project_id"],
                     metadata["source_file_path"],
                     metadata["function_name"],
                     {
                         "latest_task_id": task_id,
                         "last_execution_time": str(datetime.now()),
                         "compile_success": True,
                         "passed": passed,
                         "failed": failed,
                         "ignored": ignored,
                         "line_coverage": line_rate,
                         "branch_coverage": branch_rate
                     }
                 )
        except Exception as e:
            print(f"Failed to save execution result to cache: {e}")

        # Read source code content
        source_code_content = None
        target_abs_path = os.path.join(task_dir, target_file_rel)
        if os.path.exists(target_abs_path):
            with open(target_abs_path, 'r') as f:
                source_code_content = f.read()

        # Backfill start_line/end_line if missing (logic copied from get_result)
        start_line = metadata.get("start_line")
        end_line = metadata.get("end_line")
        
        if (start_line is None or end_line is None) and metadata.get("function_id") and source_code_content:
            try:
                from app.services.parser_service import ParserService
                import base64
                parts = metadata["function_id"].rsplit('_', 1)
                file_id = parts[0]
                expected_start = int(parts[1])
                
                functions = ParserService.parse_functions(
                    source_code_content,
                    file_id,
                    file_path=metadata.get("source_file_path"),
                    language=metadata.get("language", "c"),
                )
                found = False
                for f in functions:
                    if f.start_line == expected_start:
                        start_line = f.start_line
                        end_line = f.end_line
                        found = True
                        break
                
                if not found and metadata.get("function_name"):
                    for f in functions:
                        if (f.qualified_name or f.name) == metadata["function_name"]:
                            start_line = f.start_line
                            end_line = f.end_line
                            found = True
                            break
                
                if found:
                    metadata["start_line"] = start_line
                    metadata["end_line"] = end_line
                    # Update metadata on disk
                    with open(os.path.join(task_dir, "metadata.json"), "w") as f:
                        json.dump(metadata, f, default=str)
            except Exception as e:
                pass

        return ExecuteTestResponse(
            task_id=task_id,
            compile_success=True,
            execution_started=True,
            language=metadata.get("language", "c"),
            test_framework=metadata.get("test_framework", "unity"),
            install_success=True,
            test_result=TestResultDetail(passed=passed, failed=failed, total=total),
            coverage=coverage_data,
            stdout=output,
            stderr=run_stderr.decode(),
            source_code=source_code_content,
            function_start_line=start_line,
            function_end_line=end_line
        )

    @staticmethod
    def get_result(task_id: str) -> Optional[ExecuteTestResponse]:
        task_dir = os.path.join(TASKS_DIR, task_id)
        meta_path = os.path.join(task_dir, "metadata.json")
        if not os.path.exists(meta_path):
            return None
        with open(meta_path, "r") as f:
            meta = json.load(f)
            
        if "result" not in meta:
             return ExecuteTestResponse(
                task_id=task_id,
                compile_success=True, # Assuming it got this far? 
                execution_started=False
            )
            
        res = meta["result"]
        # Deserialize coverage if exists
        cov_obj = None
        if res.get("coverage"):
            cov_obj = TestCoverage(**res["coverage"])
            
        # Read source code content
        source_code_content = None
        if meta.get("source_file_path"):
            target_abs_path = os.path.join(task_dir, meta["source_file_path"])
            if os.path.exists(target_abs_path):
                with open(target_abs_path, 'r') as f:
                    source_code_content = f.read()

        # If start_line or end_line is missing, try to backfill using ParserService
        start_line = meta.get("start_line")
        end_line = meta.get("end_line")
        
        if (start_line is None or end_line is None) and meta.get("function_id") and source_code_content:
            try:
                from app.services.parser_service import ParserService
                import base64
                parts = meta["function_id"].rsplit('_', 1)
                file_id = parts[0]
                expected_start = int(parts[1])
                
                functions = ParserService.parse_functions(source_code_content, file_id, file_path=meta.get("source_file_path"), language=meta.get("language", "c"))
                found = False
                # 1. Try to match by start_line
                for f in functions:
                    if f.start_line == expected_start:
                        start_line = f.start_line
                        end_line = f.end_line
                        found = True
                        break
                
                # 2. If not found by line, try by function_name if available
                if not found and meta.get("function_name"):
                    for f in functions:
                        if (f.qualified_name or f.name) == meta["function_name"]:
                            start_line = f.start_line
                            end_line = f.end_line
                            found = True
                            break
                
                if found:
                    # Optionally update metadata on disk so we don't parse again
                    meta["start_line"] = start_line
                    meta["end_line"] = end_line
                    try:
                        with open(meta_path, "w") as f_meta:
                            json.dump(meta, f_meta, default=str)
                    except:
                        pass
            except Exception as e:
                pass

        return ExecuteTestResponse(
            task_id=task_id,
            compile_success=True,
            execution_started=True,
            language=meta.get("language", "c"),
            test_framework=meta.get("test_framework", "unity"),
            install_success=True,
            test_result=TestResultDetail(passed=res["passed"], failed=res["failed"], total=res["total"]),
            coverage=cov_obj,
            stdout=res.get("stdout", ""),
            stderr="",
            source_code=source_code_content,
            function_start_line=start_line,
            function_end_line=end_line
        )
