import os
import httpx
import re
import json
import asyncio
from typing import Dict, Any

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-placeholder")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

class LLMService:
    @staticmethod
    async def generate_test_case_stream(
        project_context: str, 
        function_code: str, 
        code_graph: Dict[str, Any],
        file_code: str = "",
        test_framework: str = "unity",
        failure_context: str = None,
        function_intent: str = None,
        prior_test_code: str = None
    ):
        failure_info = ""
        if failure_context:
            failure_info = f"""
### FAILURE CONTEXT (Fix required):
The previous test attempt failed. Please analyze the failure information below and generate a corrected unit test.
"""
            if prior_test_code:
                failure_info += f"""
Previous (Failed) Test Code:
```c
{prior_test_code}
```
"""
            failure_info += f"""
Failure Details:
{failure_context}
"""

        intent_info = ""
        if function_intent:
            intent_info = f"""
### FUNCTION INTENT:
The user (or system) has provided the following description of what the function is supposed to do. Use this to guide your test case generation, ensuring you test the intended behavior and edge cases mentioned.
Intent:
{function_intent}
"""

        prompt = f"""
You are an expert C developer. Generate a unit test file using the {test_framework} framework for the following C function.

{intent_info}
{failure_info}

Target Function:
```c
{function_code}
```

Full File Content (for context, macros, and static dependencies):
```c
{file_code}
```

Code Graph Info:
{json.dumps(code_graph, indent=2)}

Project Context (Header/Structure info):
{project_context}

Requirements:
1. Include "unity.h".
2. **DO NOT** include the source ".c" file (e.g., do not use #include "filename.c"). The backend will handle the linking of source files.
3. If a header file for the target function is provided in the "Project Context", include it. Otherwise, declare the function prototype directly in the test file.
4. **IMPORTANT**: Even if the target function is `static` in the source code, do **NOT** use the `static` keyword when declaring its prototype in the test file. The backend automatically removes `static` from the source to make it testable.
5. **CRITICAL**: DO NOT redefine, mock, or provide your own implementation for any functions that already exist in the provided "Target Function" code or "Project Context". Assume all internal project dependencies will be correctly linked.
6. Write `setUp` and `tearDown` functions.
7. Write at least 2-3 comprehensive test cases (test_Function_Scenario).
8. Output ONLY the C code for the test file. Wrap it in a ```c block.
9. Ensure the code compiles with standard Unity setup and includes necessary standard headers like <math.h> or <float.h> if the function uses them.
"""
        if os.getenv("LLM_DEBUG_PROMPT") == "1":
            print(f"DEBUG: LLM Prompt:\n{prompt}")
        if DEEPSEEK_API_KEY == "sk-placeholder":
            mock_code = LLMService._generate_mock_test(function_code)
            for char in mock_code:
                yield char
                await asyncio.sleep(0.001)
            return

        payload = {
            "model": "deepseek-chat", 
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that generates C unit tests."},
                {"role": "user", "content": prompt}
            ],
            "stream": True,
            "temperature": 0.2
        }

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("POST", DEEPSEEK_API_URL, json=payload, headers=headers, timeout=60.0) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        if line == "data: [DONE]":
                            break
                        
                        try:
                            chunk = json.loads(line[6:])
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                if "content" in delta:
                                    yield delta["content"]
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                print(f"LLM Stream Call failed: {e}")
                yield f"/* Error generating test case: {e} */"

    @staticmethod
    async def generate_function_intent_stream(
        function_code: str,
        project_context: str = ""
    ):
        prompt = f"""
You are an expert C developer. Analyze the following C function and describe its validation intention following ISO/IEC/IEEE 29119.

Target Function:
```c
{function_code}
```

Project Context:
{project_context}

Requirements for the output format:
1. Use a three-part structure with the following headers:
   #Objective: (Mandatory) Describe the main goal of the function and what it tests/validates.
   #Preconditions: (Optional) Describe any state or setup required before the function logic starts.
   #Expected Results: (Optional) List the specific outcomes, side effects, or return values expected.
2. Be concise and technical.
3. Output ONLY the formatted intent description text.
"""

        if DEEPSEEK_API_KEY == "sk-placeholder":
            mock_intent = "#Objective: Mock intent description for testing purposes.\n#Expected Results: 1. Success."
            for char in mock_intent:
                yield char
                await asyncio.sleep(0.001)
            return

        payload = {
            "model": "deepseek-chat", 
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that explains code logic."},
                {"role": "user", "content": prompt}
            ],
            "stream": True,
            "temperature": 0.2
        }
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("POST", DEEPSEEK_API_URL, json=payload, headers=headers, timeout=60.0) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        if line == "data: [DONE]":
                            break
                        
                        try:
                            chunk = json.loads(line[6:])
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                if "content" in delta:
                                    yield delta["content"]
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                print(f"LLM Stream Call failed: {e}")
                yield f"Error generating intent: {e}"

    @staticmethod
    async def generate_test_case(
        project_context: str, 
        function_code: str, 
        code_graph: Dict[str, Any],
        file_code: str = "",
        test_framework: str = "unity",
        failure_context: str = None,
        function_intent: str = None,
        prior_test_code: str = None
    ) -> str:
        
        failure_info = ""
        if failure_context:
            failure_info = f"""
### FAILURE CONTEXT (Fix required):
The previous test attempt failed. Please analyze the failure information below and generate a corrected unit test.
"""
            if prior_test_code:
                failure_info += f"""
Previous (Failed) Test Code:
```c
{prior_test_code}
```
"""
            failure_info += f"""
Failure Details:
{failure_context}
"""

        intent_info = ""
        if function_intent:
            intent_info = f"""
### FUNCTION INTENT:
The user (or system) has provided the following description of what the function is supposed to do. Use this to guide your test case generation, ensuring you test the intended behavior and edge cases mentioned.
Intent:
{function_intent}
"""

        prompt = f"""
You are an expert C developer. Generate a unit test file using the {test_framework} framework for the following C function.

{intent_info}
{failure_info}

Target Function:
```c
{function_code}
```

Full File Content (for context, macros, and static dependencies):
```c
{file_code}
```

Code Graph Info:
{json.dumps(code_graph, indent=2)}

Project Context (Header/Structure info):
{project_context}

Requirements:
1. Include "unity.h".
2. **DO NOT** include the source ".c" file (e.g., do not use #include "filename.c"). The backend will handle the linking of source files.
3. If a header file for the target function is provided in the "Project Context", include it. Otherwise, declare the function prototype directly in the test file.
4. **IMPORTANT**: Even if the target function is `static` in the source code, do **NOT** use the `static` keyword when declaring its prototype in the test file. The backend automatically removes `static` from the source to make it testable.
5. **CRITICAL**: DO NOT redefine, mock, or provide your own implementation for any functions that already exist in the provided "Target Function" code or "Project Context". Assume all internal project dependencies will be correctly linked.
6. Write `setUp` and `tearDown` functions.
7. Write at least 2-3 comprehensive test cases (test_Function_Scenario).
8. Output ONLY the C code for the test file. Wrap it in a ```c block.
9. Ensure the code compiles with standard Unity setup and includes necessary standard headers like <math.h> or <float.h> if the function uses them.
"""
        if os.getenv("LLM_DEBUG_PROMPT") == "1":
            print(f"DEBUG: LLM Prompt:\n{prompt}")
        if DEEPSEEK_API_KEY == "sk-placeholder":
            return LLMService._generate_mock_test(function_code)

        payload = {
            "model": "deepseek-chat", 
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that generates C unit tests."},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "temperature": 0.2
        }

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=60.0)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return LLMService._extract_code(content)
            except Exception as e:
                print(f"LLM Call failed: {e}")
                return LLMService._generate_mock_test(function_code)

    @staticmethod
    async def generate_function_intent(
        function_code: str,
        project_context: str = ""
    ) -> str:
        prompt = f"""
You are an expert C developer. Analyze the following C function and describe its validation intention following ISO/IEC/IEEE 29119.

Target Function:
```c
{function_code}
```

Project Context:
{project_context}

Requirements for the output format:
1. Use a three-part structure with the following headers:
   #Objective: (Mandatory) Describe the main goal of the function and what it tests/validates.
   #Preconditions: (Optional) Describe any state or setup required before the function logic starts.
   #Expected Results: (Optional) List the specific outcomes, side effects, or return values expected.
2. Be concise and technical.
3. Output ONLY the formatted intent description text.
"""

        if DEEPSEEK_API_KEY == "sk-placeholder":
             return "#Objective: Mock intent description for testing purposes.\n#Expected Results: 1. Success."

        payload = {
            "model": "deepseek-chat", 
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that explains code logic."},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "temperature": 0.2
        }
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=60.0)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return content
            except Exception as e:
                print(f"LLM Call failed: {e}")
                return "Error generating intent."

    @staticmethod
    def _extract_code(text: str) -> str:
        match = re.search(r'```c\n(.*?)\n```', text, re.DOTALL)
        if match:
            return match.group(1)
        # Try without language specifier
        match = re.search(r'```\n(.*?)\n```', text, re.DOTALL)
        if match:
            return match.group(1)
        return text

    @staticmethod
    def _generate_mock_test(function_code: str) -> str:
        # Generate a dummy test based on function name
        func_name_match = re.search(r'([a-zA-Z0-9_]+)\s*\(', function_code)
        func_name = func_name_match.group(1) if func_name_match else "unknown"
        
        return f"""
#include "unity.h"
#include <math.h>
#include <float.h>

// Declare prototype without static (even if static in source)
// The backend handles removing static from the source file.
// double {func_name}(...);

void setUp(void) {{}}
void tearDown(void) {{}}

void test_{func_name}_basic(void) {{
    // TODO: Implement actual test
    TEST_IGNORE_MESSAGE("Mock test generated without API key");
}}

int main(void) {{
    UNITY_BEGIN();
    RUN_TEST(test_{func_name}_basic);
    return UNITY_END();
}}
"""
