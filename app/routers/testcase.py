from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
import base64
import json
import asyncio
import re
import os
from typing import List, Optional
from app.models.testcase import (
    GenerateTestRequest, GenerateTestResponse,
    GenerateIntentRequest, GenerateIntentResponse,
    AnnotateTestRequest,
    ExecuteTestRequest, ExecuteTestResponse,
    ExportTestRequest, ExportTestResponse,
)
from app.services.project_service import ProjectService
from app.services.parser_service import ParserService
from app.services.llm_service import LLMService
from app.services.runner_service import RunnerService, TASKS_DIR
from app.services.cache_service import CacheService
from app.services.upstream_service import UpstreamService

router = APIRouter(prefix="/api/testcase", tags=["testcase"])


def _get_cache_key(func) -> str:
    return getattr(func, "qualified_name", None) or func.name


def _resolve_target_function(project_id: str, function_id: str):
    try:
        parts = function_id.rsplit('_', 1)
        file_id = parts[0]
        start_line = int(parts[1])
        path = base64.urlsafe_b64decode(file_id).decode()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid function_id")

    language = ProjectService.get_project_language(project_id)
    content = ProjectService.get_file_content(project_id, path)
    functions = ParserService.parse_functions(content, file_id, file_path=path, language=language)
    target_func = next((f for f in functions if f.start_line == start_line), None)
    if not target_func:
        raise HTTPException(status_code=404, detail="Function not found")
    return path, content, target_func, language


def _strip_weak_mocks(code: str) -> str:
    """
    后端兜底：删除 LLM 在测试文件里写的 __attribute__((weak)) mock 函数体。
    这些 mock 不应该出现在测试文件里——后端用 objcopy --weaken-symbol 处理符号冲突。
    LLM 写这些既浪费 token 又会造成 multiple definition 错误（strong vs weak 竞争）。

    策略：扫描每个 __attribute__((weak)) 开头的函数定义，删除整个函数体。
    同时删除相关的 mock 控制变量声明（static int mock_xxx = ...）。
    """
    lines = code.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 检测 __attribute__((weak)) 函数定义行（可能在同一行或前一行）
        stripped = line.strip()
        is_weak_func = (
            '__attribute__((weak))' in stripped and
            re.search(r'[a-zA-Z_]\w*\s*\(', stripped) and
            '{' in stripped  # 同行有开花括号，是函数定义不是声明
        )
        # 也处理 weak 属性在函数头但花括号在下一行的情况
        if not is_weak_func and '__attribute__((weak))' in stripped:
            # 向后看几行找花括号
            for lookahead in range(1, 4):
                if i + lookahead < len(lines) and '{' in lines[i + lookahead]:
                    is_weak_func = True
                    break

        if is_weak_func:
            # 跳过整个函数体
            brace_count = 0
            opened = False
            while i < len(lines):
                brace_count += lines[i].count('{') - lines[i].count('}')
                if '{' in lines[i]:
                    opened = True
                if opened and brace_count <= 0:
                    i += 1
                    break
                i += 1
            # 跳过紧随其后的空行
            while i < len(lines) and lines[i].strip() == '':
                i += 1
            continue

        # 删除 mock 控制变量声明（static int/const mock_xxx = ...）
        if re.match(r'^\s*static\s+.*\bmock_\w+\s*=', line):
            i += 1
            continue

        result.append(line)
        i += 1

    return '\n'.join(result)


def _fix_missing_headers(code: str) -> str:
    """
    后端兜底：如果生成的代码里用了 va_list / va_start / va_end / va_arg
    但没有 #include <stdarg.h>，则自动注入。
    同理检测其他常见缺失头文件。
    """
    HEADER_TRIGGERS = {
        "<stdarg.h>": re.compile(r'\b(va_list|va_start|va_end|va_arg)\b'),
        "<string.h>": re.compile(r'\b(memcpy|memset|memmove|strcmp|strncmp|strlen|strcpy|strncpy|strstr|strchr)\b'),
        "<stdlib.h>": re.compile(r'\b(malloc|calloc|realloc|free|atoi|atof|atol|exit|abort)\b'),
        "<stddef.h>": re.compile(r'\bptrdiff_t\b'),
    }
    # Find the position of the last #include line so we can insert after it
    lines = code.split('\n')
    last_include_idx = -1
    for idx, line in enumerate(lines):
        if line.strip().startswith('#include'):
            last_include_idx = idx

    to_inject = []
    for header, pattern in HEADER_TRIGGERS.items():
        already_present = f'#include {header}' in code or f'#include "{header[1:-1]}"' in code
        if not already_present and pattern.search(code):
            to_inject.append(f'#include {header}')

    if not to_inject:
        return code

    insert_at = last_include_idx + 1 if last_include_idx >= 0 else 0
    lines[insert_at:insert_at] = to_inject
    return '\n'.join(lines)


def _strip_ignore_tests(code: str) -> str:
    """
    移除生成的测试代码中所有包含 TEST_IGNORE_MESSAGE 的测试函数，
    同时删除 main() 中对应的 RUN_TEST(...) 调用行。
    保留其余代码结构不变。
    """
    lines = code.split('\n')

    # --- 第一步：识别需要删除的测试函数名 ---
    ignore_func_names = set()
    for line in lines:
        if 'TEST_IGNORE_MESSAGE' in line:
            # 向上找最近的 void test_xxx( 函数头
            idx = lines.index(line)
            for j in range(idx, -1, -1):
                m = re.match(r'^\s*void\s+(test_\w+)\s*\(', lines[j])
                if m:
                    ignore_func_names.add(m.group(1))
                    break

    if not ignore_func_names:
        return code

    # --- 第二步：删除这些函数的完整函数体 ---
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 检查是否是需要删除的函数头
        m = re.match(r'^\s*void\s+(test_\w+)\s*\(', line)
        if m and m.group(1) in ignore_func_names:
            # 跳过整个函数体（扫描到配对的 }）
            brace_count = 0
            opened = False
            while i < len(lines):
                brace_count += lines[i].count('{') - lines[i].count('}')
                if '{' in lines[i]:
                    opened = True
                if opened and brace_count <= 0:
                    i += 1
                    break
                i += 1
            continue
        # 删除 RUN_TEST 调用
        run_test_match = re.match(r'^\s*RUN_TEST\(\s*(\w+)\s*\)', line)
        if run_test_match and run_test_match.group(1) in ignore_func_names:
            i += 1
            continue
        result.append(line)
        i += 1

    return '\n'.join(result)

@router.get("/history")
async def get_testcase_history(project_id: str, function_id: str):
    path, _, target_func, _ = _resolve_target_function(project_id, function_id)
    data = CacheService.get_function_data(project_id, path, _get_cache_key(target_func))
    
    # If we have a latest_task_id, we might want to fetch the code from there if not in cache
    test_code = data.get("test_code")
    if data.get("latest_task_id") and not test_code:
        test_code = RunnerService.get_task_code(data["latest_task_id"])
        if test_code:
            data["test_code"] = test_code

    return data

@router.post("/intent/stream")
async def generate_intent_stream(request: GenerateIntentRequest):
    path, content, target_func, language = _resolve_target_function(request.project_id, request.function_id)
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)
    
    async def stream_generator():
        full_intent = ""
        async for chunk in LLMService.generate_function_intent_stream(source_code, context_str, language=language):
            full_intent += chunk
            yield chunk
        
        # Save intent to cache
        CacheService.save_function_data(
            request.project_id, 
            path, 
            _get_cache_key(target_func), 
            {"intent": full_intent}
        )

    return StreamingResponse(stream_generator(), media_type="text/plain")

@router.post("/generate/stream")
async def generate_testcase_stream(request: GenerateTestRequest):
    path, content, target_func, language = _resolve_target_function(request.project_id, request.function_id)
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    code_graph = ParserService.generate_code_graph(source_code, language=language)
    
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)
    
    prior_test_code = None
    if request.failed_task_id:
        prior_test_code = RunnerService.get_task_code(request.failed_task_id)
        print(f"Failed task id: {request.failed_task_id}")
        print(prior_test_code)

    # 查询上游需求上下文（文档审查 + 需求追溯）
    requirement_context = UpstreamService.get_requirement_context(
        request.project_id,
        target_func.name,
        path,
        signature=getattr(target_func, "signature", None),
        strict_rank1=True,
        qualified_name=getattr(target_func, "qualified_name", None),
    )

    async def stream_generator():
        full_test_code = ""
        async for chunk in LLMService.generate_test_case_stream(
            language=language,
            project_context=context_str,
            function_code=source_code,
            code_graph=code_graph,
            file_code=content,
            test_framework=request.test_framework,
            failure_context=request.failure_context,
            function_intent=request.function_intent,
            prior_test_code=prior_test_code,
            requirement_context=requirement_context,
        ):
            full_test_code += chunk
            # Send chunk as JSON to distinguish from task_id later
            yield json.dumps({"type": "content", "content": chunk}) + "\n"
        
        # After stream finishes, create task
        # Clean markdown code blocks using regex
        clean_code = full_test_code
        clean_code = re.sub(r'```\w*', '', clean_code)
        clean_code = clean_code.strip()
        if language == "c":
            clean_code = _strip_ignore_tests(clean_code)
            clean_code = _strip_weak_mocks(clean_code)
            clean_code = _fix_missing_headers(clean_code)

        task_id = RunnerService.create_task(
            project_id=request.project_id,
            function_id=request.function_id,
            function_name=_get_cache_key(target_func),
            test_code=clean_code,
            source_file_path=path,
            start_line=target_func.start_line,
            end_line=target_func.end_line,
            language=language,
            test_framework=request.test_framework or ("pytest" if language == "python" else "unity"),
        )
        
        # Save task_id and test_code to cache
        CacheService.save_function_data(
            request.project_id,
            path,
            _get_cache_key(target_func),
            {
                "latest_task_id": task_id,
                "test_code": clean_code
            }
        )
        
        yield json.dumps({"type": "task_id", "task_id": task_id}) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")

@router.post("/intent", response_model=GenerateIntentResponse)
async def generate_intent(request: GenerateIntentRequest):
    path, content, target_func, language = _resolve_target_function(request.project_id, request.function_id)
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)
    
    intent = await LLMService.generate_function_intent(source_code, context_str, language=language)
    
    # Save intent to cache
    CacheService.save_function_data(
        request.project_id, 
        path, 
        _get_cache_key(target_func), 
        {"intent": intent}
    )
    
    return GenerateIntentResponse(intent=intent)

@router.post("/generate", response_model=GenerateTestResponse)
async def generate_testcase(request: GenerateTestRequest):
    path, content, target_func, language = _resolve_target_function(request.project_id, request.function_id)
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    code_graph = ParserService.generate_code_graph(source_code, language=language)
    
    # 2. Build Context
    # Include all header files in the project as context
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)

    # 查询上游需求上下文（文档审查 + 需求追溯）
    requirement_context = UpstreamService.get_requirement_context(
        request.project_id,
        target_func.name,
        path,
        signature=getattr(target_func, "signature", None),
        strict_rank1=True,
        qualified_name=getattr(target_func, "qualified_name", None),
    )

    # 3. Call LLM
    test_code = await LLMService.generate_test_case(
        language=language,
        project_context=context_str,
        function_code=source_code,
        code_graph=code_graph,
        file_code=content,
        test_framework=request.test_framework,
        failure_context=request.failure_context,
        requirement_context=requirement_context,
    )
    
    # 4. Create Task
    # Clean markdown code blocks using regex
    # Remove ```c, ```cpp, ``` etc. and closing ```
    test_code = re.sub(r'```\w*', '', test_code)
    test_code = test_code.strip()
    if language == "c":
        test_code = _strip_ignore_tests(test_code)
        test_code = _strip_weak_mocks(test_code)
        test_code = _fix_missing_headers(test_code)

    task_id = RunnerService.create_task(
        project_id=request.project_id,
        function_id=request.function_id,
        function_name=_get_cache_key(target_func),
        test_code=test_code,
        source_file_path=path,
        start_line=target_func.start_line,
        end_line=target_func.end_line,
        language=language,
        test_framework=request.test_framework or ("pytest" if language == "python" else "unity"),
    )
    
    # Save task_id and test_code to cache
    CacheService.save_function_data(
        request.project_id,
        path,
        _get_cache_key(target_func),
        {
            "latest_task_id": task_id,
            "test_code": test_code
        }
    )
    
    return GenerateTestResponse(
        task_id=task_id,
        test_code=test_code,
        status="generated",
        language=language,
        test_framework=request.test_framework or ("pytest" if language == "python" else "unity"),
    )

@router.post("/annotate/stream")
async def annotate_testcase_stream(request: AnnotateTestRequest):
    """
    第二步（流式）：基于设计文档，给已生成的测试代码在每条断言下插入中文注释。
    支持 C (Unity) 和 Python (pytest) 两种语言。
    """
    # 1. 获取函数信息（用于查找设计文档）
    path, content, target_func, language = _resolve_target_function(request.project_id, request.function_id)

    # 2. 查找设计文档（Python 也支持 qualified_name 匹配）
    design_doc = ProjectService.get_function_design_doc(
        request.project_id,
        target_func.name,
        qualified_name=getattr(target_func, "qualified_name", None)
    )
    if not design_doc:
        raise HTTPException(status_code=404, detail="No design document found for this function")

    # 3. 获取第一步已生成的测试代码
    test_code = RunnerService.get_task_code(request.task_id)
    if not test_code:
        raise HTTPException(status_code=404, detail="Task code not found")

    # 4. 提取被测函数源代码（供 LLM 做缺陷检测）
    func_lines = content.split('\n')[target_func.start_line - 1 : target_func.end_line]
    source_code = "\n".join(func_lines)

    # Determine test file name based on language
    test_file_name = "test_generated.py" if language == "python" else "test_runner.c"

    async def stream_generator():
        annotated_code = ""
        async for chunk in LLMService.annotate_with_design_doc_stream(
            test_code, design_doc, source_code, language=language
        ):
            annotated_code += chunk
            yield json.dumps({"type": "content", "content": chunk}) + "\n"

        # 清理 markdown 代码块标记
        clean_code = re.sub(r'```\w*', '', annotated_code).strip()

        # 用带注释的代码覆盖 task 文件
        task_dir = os.path.join(TASKS_DIR, request.task_id)
        code_path = os.path.join(task_dir, test_file_name)
        if os.path.exists(task_dir):
            with open(code_path, "w") as f:
                f.write(clean_code)

        # 更新 cache
        CacheService.save_function_data(
            request.project_id, path, _get_cache_key(target_func),
            {"test_code": clean_code}
        )

        yield json.dumps({"type": "done", "task_id": request.task_id}) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")


@router.post("/annotate", response_model=GenerateTestResponse)
async def annotate_testcase(request: AnnotateTestRequest):
    """
    第二步（非流式）：基于设计文档，给已生成的测试代码在每条断言下插入中文注释。
    支持 C (Unity) 和 Python (pytest) 两种语言。
    """
    path, content, target_func, language = _resolve_target_function(request.project_id, request.function_id)

    design_doc = ProjectService.get_function_design_doc(
        request.project_id,
        target_func.name,
        qualified_name=getattr(target_func, "qualified_name", None)
    )
    if not design_doc:
        raise HTTPException(status_code=404, detail="No design document found for this function")

    test_code = RunnerService.get_task_code(request.task_id)
    if not test_code:
        raise HTTPException(status_code=404, detail="Task code not found")

    # 提取被测函数源代码（供 LLM 做缺陷检测）
    func_lines = content.split('\n')[target_func.start_line - 1 : target_func.end_line]
    source_code = "\n".join(func_lines)

    annotated_code = await LLMService.annotate_with_design_doc(
        test_code, design_doc, source_code, language=language
    )
    clean_code = re.sub(r'```\w*', '', annotated_code).strip()

    # Determine test file name based on language
    test_file_name = "test_generated.py" if language == "python" else "test_runner.c"

    # 覆盖 task 文件
    task_dir = os.path.join(TASKS_DIR, request.task_id)
    code_path = os.path.join(task_dir, test_file_name)
    if os.path.exists(task_dir):
        with open(code_path, "w") as f:
            f.write(clean_code)

    CacheService.save_function_data(
        request.project_id, path, _get_cache_key(target_func),
        {"test_code": clean_code}
    )

    return GenerateTestResponse(
        task_id=request.task_id,
        test_code=clean_code,
        status="annotated",
        language=language,
        test_framework="pytest" if language == "python" else "unity",
    )


@router.post("/execute", response_model=ExecuteTestResponse)
async def execute_testcase(request: ExecuteTestRequest):
    # This might take time, but the API doc implies a synchronous return of "execution_started"
    # then polling result.
    # However, Python `await` will block until done if we just call it.
    # To support "execution_started" immediately, we need BackgroundTasks.
    # But the return type has `compile_success` which implies we at least wait for compilation?
    # The doc says "Returns ... execution_started: true".
    # And then "Get Test Result".
    # So `execute` should trigger it.
    
    # We will await it for simplicity in this prototype. 
    # If it's slow, we can make it background.
    result = await RunnerService.execute_task(request.task_id)
    return result

@router.get("/{task_id}/result", response_model=ExecuteTestResponse)
async def get_test_result(task_id: str):
    result = RunnerService.get_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@router.post("/export", response_model=ExportTestResponse)
async def export_test_document(request: ExportTestRequest):
    """
    导出测试用例文档（Markdown 或 HTML 格式）。
    包含函数基本信息、设计文档、语义意图、测试代码、执行结果和覆盖率。
    """
    task_id = request.task_id
    result = RunnerService.get_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")

    # 获取任务元数据
    metadata = RunnerService.get_task_metadata(task_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Task metadata not found")

    project_id = metadata.get("project_id", "")
    function_id = metadata.get("function_id", "")
    language = metadata.get("language") or result.language or "c"
    function_name = metadata.get("function_name", "")
    source_file = metadata.get("source_file_path", "")
    start_line = metadata.get("start_line", 1)
    end_line = metadata.get("end_line", 1)

    # 获取测试代码（优先取 cache 中带注释的版本）
    test_code = metadata.get("test_code") or RunnerService.get_task_code(task_id) or ""

    # 获取缓存数据（intent、设计文档注释版本）
    cache_data = {}
    if project_id and function_id:
        try:
            path, content, target_func, _ = _resolve_target_function(project_id, function_id)
            cache_data = CacheService.get_function_data(project_id, path, _get_cache_key(target_func))
            source_code = "\n".join(content.split('\n')[start_line - 1 : end_line])
            function_signature = target_func.signature
            qualified_name = getattr(target_func, "qualified_name", None)
            class_name = getattr(target_func, "class_name", None)
        except Exception:
            source_code = ""
            function_signature = function_name
            qualified_name = None
            class_name = None
    else:
        source_code = ""
        function_signature = function_name
        qualified_name = None
        class_name = None

    # 优先使用 cache 中的测试代码（可能已含注释）
    test_code = cache_data.get("test_code") or test_code
    function_intent = cache_data.get("intent") or ""

    # 获取设计文档
    design_doc = None
    if project_id and function_name:
        try:
            design_doc = ProjectService.get_function_design_doc(
                project_id, function_name, qualified_name=qualified_name
            )
        except Exception:
            pass

    # 构建测试结果摘要
    test_result_summary = None
    if result.test_result:
        test_result_summary = {
            "passed": result.test_result.passed,
            "failed": result.test_result.failed,
            "total": result.test_result.total,
        }

    # 构建覆盖率摘要
    coverage_summary = None
    if result.coverage and result.coverage.files:
        coverage_summary = {
            "files": [
                {
                    "file": fcov.file,
                    "line": {
                        "covered": fcov.line.covered,
                        "total": fcov.line.total,
                        "rate": fcov.line.rate,
                    },
                    "function": {
                        "covered": fcov.function.covered,
                        "total": fcov.function.total,
                        "rate": fcov.function.rate,
                    },
                    "branch": {
                        "covered": fcov.branch.covered,
                        "total": fcov.branch.total,
                        "rate": fcov.branch.rate,
                    },
                }
                for fcov in result.coverage.files
            ]
        }

    # 生成文档
    content = LLMService.generate_export_document(
        language=language,
        function_name=function_name,
        function_signature=function_signature,
        qualified_name=qualified_name,
        class_name=class_name,
        source_file=source_file,
        source_code=source_code,
        function_intent=function_intent,
        test_code=test_code,
        design_doc=design_doc,
        test_result=test_result_summary,
        coverage=coverage_summary,
        format=request.format,
    )

    ext_map = {"html": "html", "docx": "docx", "markdown": "md"}
    ext = ext_map.get(request.format, "md")
    safe_name = re.sub(r'[^\w\-.]', '_', function_name)
    filename = f"test_doc_{safe_name}_{task_id[:8]}.{ext}"

    return ExportTestResponse(
        content=content,
        format=request.format,
        filename=filename,
    )
