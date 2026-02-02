from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
import base64
import json
import asyncio
import re
from typing import List, Optional
from app.models.testcase import (
    GenerateTestRequest, GenerateTestResponse, 
    GenerateIntentRequest, GenerateIntentResponse,
    ExecuteTestRequest, ExecuteTestResponse
)
from app.services.project_service import ProjectService
from app.services.parser_service import ParserService
from app.services.llm_service import LLMService
from app.services.runner_service import RunnerService
from app.services.cache_service import CacheService

router = APIRouter(prefix="/api/testcase", tags=["testcase"])

@router.get("/history")
async def get_testcase_history(project_id: str, function_id: str):
    try:
        parts = function_id.rsplit('_', 1)
        file_id = parts[0]
        start_line = int(parts[1])
        path = base64.urlsafe_b64decode(file_id).decode()
    except:
        raise HTTPException(status_code=400, detail="Invalid function_id")
    
    # Need to parse function to get name for cache key
    content = ProjectService.get_file_content(project_id, path)
    functions = ParserService.parse_functions(content, file_id)
    
    target_func = None
    for f in functions:
        if f.start_line == start_line:
            target_func = f
            break
    
    if not target_func:
        # Fallback: try to find by fuzzy matching if start_line moved
        # But for history retrieval, strict match or simple fail is maybe better?
        # Actually, if we use CacheService, we rely on function_name.
        # So we should try to find function by name if line match fails?
        # For now, let's stick to strict match to be safe, or just return empty if not found.
        # Wait, if we can't parse the function, we can't know its name, so we can't query cache.
        raise HTTPException(status_code=404, detail="Function not found for history lookup")

    data = CacheService.get_function_data(project_id, path, target_func.name)
    
    # If we have a latest_task_id, we might want to fetch the code from there if not in cache
    test_code = data.get("test_code")
    if data.get("latest_task_id") and not test_code:
        test_code = RunnerService.get_task_code(data["latest_task_id"])
        if test_code:
            data["test_code"] = test_code

    return data

@router.post("/intent/stream")
async def generate_intent_stream(request: GenerateIntentRequest):
    try:
        parts = request.function_id.rsplit('_', 1)
        file_id = parts[0]
        start_line = int(parts[1])
        path = base64.urlsafe_b64decode(file_id).decode()
    except:
        raise HTTPException(status_code=400, detail="Invalid function_id")

    content = ProjectService.get_file_content(request.project_id, path)
    functions = ParserService.parse_functions(content, file_id)
    
    target_func = None
    for f in functions:
        if f.start_line == start_line:
            target_func = f
            break
            
    if not target_func:
        raise HTTPException(status_code=404, detail="Function not found")
        
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)
    
    async def stream_generator():
        full_intent = ""
        async for chunk in LLMService.generate_function_intent_stream(source_code, context_str):
            full_intent += chunk
            yield chunk
        
        # Save intent to cache
        CacheService.save_function_data(
            request.project_id, 
            path, 
            target_func.name, 
            {"intent": full_intent}
        )

    return StreamingResponse(stream_generator(), media_type="text/plain")

@router.post("/generate/stream")
async def generate_testcase_stream(request: GenerateTestRequest):
    # 1. Retrieve Function Info
    try:
        parts = request.function_id.rsplit('_', 1)
        file_id = parts[0]
        start_line = int(parts[1])
        path = base64.urlsafe_b64decode(file_id).decode()
    except:
        raise HTTPException(status_code=400, detail="Invalid function_id")
        
    content = ProjectService.get_file_content(request.project_id, path)
    functions = ParserService.parse_functions(content, file_id)
    
    target_func = None
    for f in functions:
        if f.start_line == start_line:
            target_func = f
            break
            
    if not target_func:
        raise HTTPException(status_code=404, detail="Function not found")
        
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    code_graph = ParserService.generate_code_graph(source_code)
    
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)
    
    prior_test_code = None
    if request.failed_task_id:
        prior_test_code = RunnerService.get_task_code(request.failed_task_id)
        print(f"Failed task id: {request.failed_task_id}")
        print(prior_test_code)
    async def stream_generator():
        full_test_code = ""
        async for chunk in LLMService.generate_test_case_stream(
            project_context=context_str,
            function_code=source_code,
            code_graph=code_graph,
            file_code=content,
            test_framework=request.test_framework,
            failure_context=request.failure_context,
            function_intent=request.function_intent,
            prior_test_code=prior_test_code
        ):
            full_test_code += chunk
            # Send chunk as JSON to distinguish from task_id later
            yield json.dumps({"type": "content", "content": chunk}) + "\n"
        
        # After stream finishes, create task
        # Clean markdown code blocks using regex
        clean_code = full_test_code
        # Remove ```c, ```cpp, ``` etc. and closing ```
        clean_code = re.sub(r'```\w*', '', clean_code)
        clean_code = clean_code.strip()

        task_id = RunnerService.create_task(
            project_id=request.project_id,
            function_id=request.function_id,
            function_name=target_func.name,
            test_code=clean_code,
            source_file_path=path,
            start_line=target_func.start_line,
            end_line=target_func.end_line
        )
        
        # Save task_id and test_code to cache
        CacheService.save_function_data(
            request.project_id,
            path,
            target_func.name,
            {
                "latest_task_id": task_id,
                "test_code": clean_code
            }
        )
        
        yield json.dumps({"type": "task_id", "task_id": task_id}) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")

@router.post("/intent", response_model=GenerateIntentResponse)
async def generate_intent(request: GenerateIntentRequest):
    try:
        parts = request.function_id.rsplit('_', 1)
        file_id = parts[0]
        start_line = int(parts[1])
        path = base64.urlsafe_b64decode(file_id).decode()
    except:
        raise HTTPException(status_code=400, detail="Invalid function_id")

    content = ProjectService.get_file_content(request.project_id, path)
    functions = ParserService.parse_functions(content, file_id)
    
    target_func = None
    for f in functions:
        if f.start_line == start_line:
            target_func = f
            break
            
    if not target_func:
        raise HTTPException(status_code=404, detail="Function not found")
        
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)
    
    intent = await LLMService.generate_function_intent(source_code, context_str)
    
    # Save intent to cache
    CacheService.save_function_data(
        request.project_id, 
        path, 
        target_func.name, 
        {"intent": intent}
    )
    
    return GenerateIntentResponse(intent=intent)

@router.post("/generate", response_model=GenerateTestResponse)
async def generate_testcase(request: GenerateTestRequest):
    # 1. Retrieve Function Info
    try:
        parts = request.function_id.rsplit('_', 1)
        file_id = parts[0]
        start_line = int(parts[1])
        path = base64.urlsafe_b64decode(file_id).decode()
    except:
        raise HTTPException(status_code=400, detail="Invalid function_id")
        
    content = ProjectService.get_file_content(request.project_id, path)
    functions = ParserService.parse_functions(content, file_id)
    
    target_func = None
    for f in functions:
        if f.start_line == start_line:
            target_func = f
            break
            
    if not target_func:
        raise HTTPException(status_code=404, detail="Function not found")
        
    lines = content.split('\n')
    func_lines = lines[target_func.start_line-1 : target_func.end_line]
    source_code = "\n".join(func_lines)
    code_graph = ParserService.generate_code_graph(source_code)
    
    # 2. Build Context
    # Include all header files in the project as context
    all_files = ProjectService.list_files(request.project_id)
    context_str = "Project Files:\n" + "\n".join(all_files)
    
    # Maybe add content of relevant headers? 
    # For now, just the list to help LLM know what to include.
    
    # 3. Call LLM
    test_code = await LLMService.generate_test_case(
        project_context=context_str,
        function_code=source_code,
        code_graph=code_graph,
        file_code=content,
        test_framework=request.test_framework,
        failure_context=request.failure_context
    )
    
    # 4. Create Task
    # Clean markdown code blocks using regex
    # Remove ```c, ```cpp, ``` etc. and closing ```
    test_code = re.sub(r'```\w*', '', test_code)
    test_code = test_code.strip()

    task_id = RunnerService.create_task(
        project_id=request.project_id,
        function_id=request.function_id,
        function_name=target_func.name,
        test_code=test_code,
        source_file_path=path,
        start_line=target_func.start_line,
        end_line=target_func.end_line
    )
    
    # Save task_id and test_code to cache
    CacheService.save_function_data(
        request.project_id,
        path,
        target_func.name,
        {
            "latest_task_id": task_id,
            "test_code": test_code
        }
    )
    
    return GenerateTestResponse(
        task_id=task_id,
        test_code=test_code,
        status="generated"
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
