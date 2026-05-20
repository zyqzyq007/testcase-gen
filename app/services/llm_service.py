import os
import re
import json
import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

# OpenAI-compatible API configuration.
#
# Works with any OpenAI-compatible endpoint: OpenAI, DeepSeek, Aliyun Bailian
# (DashScope private deployment), Ollama, vLLM, etc. — just point
# OPENAI_BASE_URL at the desired service.
#
# DEEPSEEK_API_KEY is accepted as a fallback so existing .env files keep
# working without modification.
#
# Held as a module-level dict (not constants) so /api/config/llm can mutate
# the live configuration without restarting the process.
def _read_env_config() -> Dict[str, Any]:
    return {
        "api_key": (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or "sk-placeholder"
        ),
        "base_url": (
            os.getenv("OPENAI_BASE_URL")
            or os.getenv("OPENAI_API_BASE")
            or "https://api.deepseek.com"
        ),
        "model": os.getenv("OPENAI_MODEL", "deepseek-chat"),
        "timeout": float(os.getenv("OPENAI_TIMEOUT", "60")),
    }


_config: Dict[str, Any] = _read_env_config()


def get_config() -> Dict[str, Any]:
    """Return a copy of the current live LLM config."""
    return dict(_config)


def update_config(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Update live LLM config in-place. Any field passed as None is left unchanged.
    Returns the resulting config snapshot.
    """
    if api_key is not None:
        _config["api_key"] = api_key
    if base_url is not None:
        _config["base_url"] = base_url
    if model is not None:
        _config["model"] = model
    if timeout is not None:
        _config["timeout"] = float(timeout)
    return get_config()


def _is_mock_mode() -> bool:
    key = _config.get("api_key")
    return not key or key == "sk-placeholder"


# Heuristic detection of "thinking / reasoning" model variants. These models
# emit `delta.reasoning_content` first and only switch to `delta.content` once
# they finish reasoning, so the normal max_tokens budget gets eaten by the
# thought process before any actual answer is produced.
_REASONING_KEYWORDS = (
    "reasoner",
    "reasoning",
    "thinking",
    "flash",      # deepseek-v4-flash, gemini-*-flash-thinking, etc.
    "-r1",
    "-r2",
    "deepseek-r",
    "o1-",
    "o1",
    "o3-",
    "o3",
    "o4-",
    "qwq",        # Qwen QwQ reasoning series
)


def _is_reasoning_model(model: str) -> bool:
    if not model:
        return False
    m = model.lower()
    return any(k in m for k in _REASONING_KEYWORDS)


# Floor max_tokens for reasoning models so the thought process + final answer
# both fit. Configurable via LLM_REASONING_MAX_TOKENS.
REASONING_MAX_TOKENS_FLOOR = int(os.getenv("LLM_REASONING_MAX_TOKENS", "16000"))
REASONING_TIMEOUT_FLOOR = float(os.getenv("LLM_REASONING_TIMEOUT", "300"))


def _adjust_for_reasoning(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """If the active model is a reasoning model, raise max_tokens / timeout
    floors so the thinking phase doesn't starve the answer phase."""
    model = kwargs.get("model") or _config.get("model", "")
    if not _is_reasoning_model(model):
        return kwargs
    if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
        original = kwargs["max_tokens"]
        boosted = max(int(original), REASONING_MAX_TOKENS_FLOOR)
        if boosted != original:
            kwargs["max_tokens"] = boosted
            print(
                f"LLM: reasoning model '{model}' detected; "
                f"max_tokens bumped {original} -> {boosted}",
                flush=True,
            )
    if "timeout" in kwargs and kwargs["timeout"] is not None:
        kwargs["timeout"] = max(float(kwargs["timeout"]), REASONING_TIMEOUT_FLOOR)
    else:
        # Apply the floor even when timeout wasn't explicitly passed; the
        # client default (_config["timeout"]) may also be too short.
        if _config["timeout"] < REASONING_TIMEOUT_FLOOR:
            kwargs["timeout"] = REASONING_TIMEOUT_FLOOR
    return kwargs


def _make_client() -> AsyncOpenAI:
    # For reasoning models, also raise the client-level timeout so streaming
    # connections don't get killed mid-thought.
    timeout = _config["timeout"]
    if _is_reasoning_model(_config.get("model", "")):
        timeout = max(timeout, REASONING_TIMEOUT_FLOOR)
    return AsyncOpenAI(
        api_key=_config["api_key"],
        base_url=_config["base_url"],
        timeout=timeout,
    )


class LLMService:
    @staticmethod
    def _assess_complexity(function_code: str) -> tuple:
        """
        Estimate the cyclomatic complexity of a C function by counting
        branching keywords.  Returns (prompt_instruction, max_tokens) tailored
        to the complexity level:
          LOW  (branches 0-2)  → 1 test case,   max_tokens=1200
          MED  (branches 3-6)  → 2-3 test cases, max_tokens=2000
          HIGH (branches 7+)   → 3-5 test cases, max_tokens=3500
        """
        branch_keywords = re.findall(
            r'\b(if|else\s+if|else|for|while|do|switch|case|&&|\|\||break|continue|return)\b',
            function_code
        )
        count = len(branch_keywords)

        if count <= 2:
            level = "LOW"
            max_tokens = 1200
            instruction = (
                "6. Write `setUp` and `tearDown` functions (empty bodies are fine).\n"
                "7. **Complexity: LOW** — The function has very few branches. Write exactly **1 test case** "
                "covering the normal execution path. Keep it short.\n"
                "8. Each test function body should be minimal — 1 `TEST_ASSERT_*` call is enough."
            )
        elif count <= 6:
            level = "MED"
            max_tokens = 2000
            instruction = (
                "6. Write `setUp` and `tearDown` functions (empty bodies are fine).\n"
                "7. **Complexity: MEDIUM** — The function has several branches. Write **2–3 test cases**: "
                "one for the normal path, one or two for important edge/boundary conditions. "
                "Do NOT repeat similar variants.\n"
                "8. Keep each test function body concise — prefer 1–2 `TEST_ASSERT_*` calls per test."
            )
        else:
            level = "HIGH"
            max_tokens = 3500
            instruction = (
                "6. Write `setUp` and `tearDown` functions (empty bodies are fine).\n"
                "7. **Complexity: HIGH** — The function has many branches. Write **3–5 test cases** "
                "that collectively cover: the normal path, key boundary values, and the most important "
                "error/special-case branches. Prioritize paths that differ meaningfully in behavior.\n"
                "8. Test bodies can have multiple `TEST_ASSERT_*` calls when a scenario requires verifying "
                "several side-effects, but avoid redundant assertions."
            )

        if os.getenv("LLM_DEBUG_PROMPT") == "1":
            print(f"DEBUG: function complexity = {level} (branch tokens = {count}, max_tokens = {max_tokens})")
        return instruction, max_tokens

    @staticmethod
    async def _chat_stream(
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: float = 0,
        top_p: Optional[float] = None,
        timeout: Optional[float] = None,
        tools: Optional[list] = None,
        error_template: str = "/* Error: {e} */",
    ) -> AsyncIterator[str]:
        """
        Stream chat completion content via the OpenAI SDK. Yields incremental
        delta.content strings. Compatible with any OpenAI-style endpoint.
        """
        client = _make_client()
        kwargs: Dict[str, Any] = {
            "model": _config["model"],
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if top_p is not None:
            kwargs["top_p"] = top_p
        if timeout is not None:
            kwargs["timeout"] = timeout
        if tools is not None:
            kwargs["tools"] = tools
        kwargs = _adjust_for_reasoning(kwargs)
        debug_stream = os.getenv("LLM_DEBUG_STREAM") == "1"
        try:
            stream = await client.chat.completions.create(**kwargs)
            chunk_count = 0
            content_count = 0
            reasoning_seen = False
            async for chunk in stream:
                chunk_count += 1
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if debug_stream:
                    try:
                        print(f"DEBUG chunk#{chunk_count}: {delta.model_dump()} finish={chunk.choices[0].finish_reason}", flush=True)
                    except Exception:
                        print(f"DEBUG chunk#{chunk_count}: {delta} finish={chunk.choices[0].finish_reason}", flush=True)
                # Some models (e.g. reasoning models) emit their tokens in
                # `reasoning_content` first, then switch to `content` for the
                # final answer. Track whether reasoning was seen so the user
                # gets a clear message if no content arrives.
                if delta and getattr(delta, "reasoning_content", None):
                    reasoning_seen = True
                if delta and delta.content:
                    content_count += 1
                    yield delta.content
            if debug_stream:
                print(f"DEBUG stream done: total_chunks={chunk_count} content_chunks={content_count} reasoning_seen={reasoning_seen}", flush=True)
            if content_count == 0:
                hint = (
                    " (model returned only reasoning_content — likely a reasoning model. "
                    "Increase max_tokens or switch to a non-reasoning model.)"
                    if reasoning_seen
                    else " (model returned no content; check model name / parameters)"
                )
                yield error_template.format(e="empty response" + hint)
        except Exception as e:
            print(f"LLM Stream Call failed: {e}", flush=True)
            yield error_template.format(e=e)

    @staticmethod
    async def _chat_once(
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: float = 0,
        top_p: Optional[float] = None,
        timeout: Optional[float] = None,
        tools: Optional[list] = None,
    ) -> Optional[str]:
        """
        Non-streaming chat completion. Returns the assistant message content,
        or None on failure.
        """
        client = _make_client()
        kwargs: Dict[str, Any] = {
            "model": _config["model"],
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if top_p is not None:
            kwargs["top_p"] = top_p
        if timeout is not None:
            kwargs["timeout"] = timeout
        if tools is not None:
            kwargs["tools"] = tools
        kwargs = _adjust_for_reasoning(kwargs)
        try:
            response = await client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            print(f"LLM Call failed: {e}")
            return None

    @staticmethod
    def _build_test_prompt(
        project_context: str,
        function_code: str,
        code_graph: Dict[str, Any],
        file_code: str,
        test_framework: str,
        failure_context: Optional[str],
        function_intent: Optional[str],
        prior_test_code: Optional[str],
        design_doc: Optional[dict],
    ) -> tuple:
        """Build the (prompt, max_tokens) tuple for test-case generation."""
        failure_info = ""
        if failure_context:
            failure_info = (
                "\n### FAILURE CONTEXT (Fix required):\n"
                "The previous test attempt failed. Please analyze the failure information below "
                "and generate a corrected unit test.\n"
            )
            if prior_test_code:
                failure_info += f"\nPrevious (Failed) Test Code:\n```c\n{prior_test_code}\n```\n"
            failure_info += f"\nFailure Details:\n{failure_context}\n"

        intent_info = ""
        if function_intent:
            intent_info = (
                "\n### FUNCTION INTENT:\n"
                "The user (or system) has provided the following description of what the function "
                "is supposed to do. Use this to guide your test case generation, ensuring you test "
                "the intended behavior and edge cases mentioned.\n"
                f"Intent:\n{function_intent}\n"
            )

        design_doc_info = LLMService._format_design_doc(design_doc) if design_doc else ""
        complexity_instruction, max_tokens = LLMService._assess_complexity(function_code)

        prompt = f"""
You are an expert C developer. Generate a unit test file using the {test_framework} framework for the following C function.

{intent_info}
{design_doc_info}
{failure_info}

Target Function:
```c
{function_code}
```

Full File Content (for context, macros, and static dependencies):
```c
{file_code}
```

⚠️ Functions already defined in the source file above (DO NOT redefine any of these in your test file):
{LLMService._extract_function_names_warning(file_code)}

Code Graph Info:
{json.dumps(code_graph, indent=2)}

Project Context (Header/Structure info):
{project_context}

⚠️⚠️⚠️ CONCISENESS IS MANDATORY ⚠️⚠️⚠️
Keep the generated test file as SHORT as possible. Every unnecessary line wastes the token budget and will be TRUNCATED.
- No comments, no explanations, no blank lines between assertions.
- Each test function body: ideally 1-3 lines. One call to the function under test + one TEST_ASSERT_*.
- No helper variables unless strictly required.
- setUp/tearDown MUST have empty bodies: `void setUp(void) {{}}` / `void tearDown(void) {{}}`
- main(): only UNITY_BEGIN(), RUN_TEST() calls, UNITY_END(). Nothing else.

🚨 **MUST INCLUDE `int main(void)`** — the test file is compiled as a standalone executable. Without `main()` it will fail to link. Every `test_*` you write MUST appear as a `RUN_TEST(test_name);` line inside `main()`. Skipping `main()` is NEVER acceptable, even under the conciseness rule.

Requirements:
1. Include "unity.h".
2. **DO NOT** include the source ".c" file (e.g., do not use #include "filename.c"). The backend will handle the linking of source files.
3. If a header file for the target function is provided in the "Project Context", include it. Otherwise, declare the function prototype directly in the test file.
4. **IMPORTANT**: Even if the target function is `static` in the source code, do **NOT** use the `static` keyword when declaring its prototype in the test file. The backend automatically removes `static` from the source to make it testable.
5. **CRITICAL – NO REDEFINITION, NO MOCKS, NO WEAK SYMBOLS**:
   - The **entire source file** is compiled and linked automatically. Every function in it is already available.
   - DO NOT redefine, copy-paste, or stub any function from "Full File Content" — not even with `__attribute__((weak))`. The backend uses `objcopy --weaken-symbol` at the binary level to handle symbol conflicts automatically. Writing `__attribute__((weak))` wrappers in the test file is **unnecessary and wastes token budget**.
   - DO NOT declare `extern` global variables unless they appear in a provided header.
   - The ONLY functions you may define are: `setUp`, `tearDown`, `test_*` cases, and `main()`.
   - To test different behaviors: use different input values, not mock functions.
{complexity_instruction}
9. **MANDATORY `main()`** — Always end the file with:
   ```c
   int main(void) {{
       UNITY_BEGIN();
       RUN_TEST(test_xxx);   // one line per test_* you defined above
       ...
       return UNITY_END();
   }}
   ```
   Files without `main()` will fail to link and are considered invalid output.
10. Output ONLY the C code for the test file. Wrap it in a ```c block.
11. Ensure the code compiles with standard Unity setup and includes ALL necessary standard headers:
    - `<stdarg.h>` if you use `va_list`, `va_start`, `va_end`, `va_arg`
    - `<string.h>` if you use `strcmp`, `strlen`, `memcpy` etc.
    - `<stdlib.h>` if you use `malloc`, `free` etc.
    - `<math.h>` or `<float.h>` if the function uses floating-point operations
"""
        if os.getenv("LLM_DEBUG_PROMPT") == "1":
            print(f"DEBUG: LLM Prompt:\n{prompt}")
        return prompt, max_tokens

    _TEST_SYSTEM_MSG = (
        "You are a concise C unit test generator. Produce the shortest possible "
        "valid test file. No comments, no explanations, no extra blank lines. "
        "Minimal code only."
    )

    @staticmethod
    async def generate_test_case_stream(
        project_context: str,
        function_code: str,
        code_graph: Dict[str, Any],
        file_code: str = "",
        test_framework: str = "unity",
        failure_context: str = None,
        function_intent: str = None,
        prior_test_code: str = None,
        design_doc: dict = None,
    ):
        prompt, max_tokens = LLMService._build_test_prompt(
            project_context, function_code, code_graph, file_code, test_framework,
            failure_context, function_intent, prior_test_code, design_doc,
        )

        if _is_mock_mode():
            mock_code = LLMService._generate_mock_test(function_code)
            for char in mock_code:
                yield char
                await asyncio.sleep(0.001)
            return

        messages = [
            {"role": "system", "content": LLMService._TEST_SYSTEM_MSG},
            {"role": "user", "content": prompt},
        ]
        async for chunk in LLMService._chat_stream(
            messages,
            max_tokens=max_tokens,
            temperature=0,
            top_p=0.1,
            error_template="/* Error generating test case: {e} */",
        ):
            yield chunk

    @staticmethod
    async def generate_test_case(
        project_context: str,
        function_code: str,
        code_graph: Dict[str, Any],
        file_code: str = "",
        test_framework: str = "unity",
        failure_context: str = None,
        function_intent: str = None,
        prior_test_code: str = None,
        design_doc: dict = None,
    ) -> str:
        prompt, max_tokens = LLMService._build_test_prompt(
            project_context, function_code, code_graph, file_code, test_framework,
            failure_context, function_intent, prior_test_code, design_doc,
        )

        if _is_mock_mode():
            return LLMService._generate_mock_test(function_code)

        messages = [
            {"role": "system", "content": LLMService._TEST_SYSTEM_MSG},
            {"role": "user", "content": prompt},
        ]
        content = await LLMService._chat_once(
            messages,
            max_tokens=max_tokens,
            temperature=0,
            top_p=0.1,
        )
        if content is None:
            return LLMService._generate_mock_test(function_code)
        return LLMService._extract_code(content)

    @staticmethod
    def _build_intent_prompt(function_code: str, project_context: str) -> str:
        return f"""
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

    @staticmethod
    async def generate_function_intent_stream(
        function_code: str,
        project_context: str = ""
    ):
        prompt = LLMService._build_intent_prompt(function_code, project_context)

        if _is_mock_mode():
            mock_intent = "#Objective: Mock intent description for testing purposes.\n#Expected Results: 1. Success."
            for char in mock_intent:
                yield char
                await asyncio.sleep(0.001)
            return

        messages = [
            {"role": "system", "content": "You are a helpful assistant that explains code logic."},
            {"role": "user", "content": prompt},
        ]
        async for chunk in LLMService._chat_stream(
            messages,
            temperature=0.2,
            error_template="Error generating intent: {e}",
        ):
            yield chunk

    @staticmethod
    async def generate_function_intent(
        function_code: str,
        project_context: str = ""
    ) -> str:
        prompt = LLMService._build_intent_prompt(function_code, project_context)

        if _is_mock_mode():
            return "#Objective: Mock intent description for testing purposes.\n#Expected Results: 1. Success."

        messages = [
            {"role": "system", "content": "You are a helpful assistant that explains code logic."},
            {"role": "user", "content": prompt},
        ]
        content = await LLMService._chat_once(messages, temperature=0.2)
        return content if content is not None else "Error generating intent."

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
    def _format_design_doc(design_doc: dict) -> str:
        """
        将 function_design 字典格式化为 LLM prompt 中的结构化文本段落。
        """
        if not design_doc:
            return ""

        lines = ["### Function Design Document (from project specification):"]

        basic = design_doc.get("basic_info", {})
        if basic:
            lines.append(f"- Function Name: {basic.get('function_name', '')}")
            lines.append(f"- Identifier: {basic.get('software_unit_identifier', '')}")
            lines.append(f"- Description: {basic.get('function_desc', '')}")

        io = design_doc.get("io_params", {})
        if io:
            inputs = io.get("input_params", [])
            if inputs:
                lines.append("- Input Parameters:")
                for p in inputs:
                    lines.append(f"    - {p.get('param_name', '')}: {p.get('param_desc', '')}")
            ret_vals = io.get("return_value", [])
            if ret_vals:
                lines.append("- Return Values:")
                for r in ret_vals:
                    lines.append(f"    - {r.get('value', '')}: {r.get('desc', '')}")

        algo = design_doc.get("algorithm", {})
        if algo.get("desc"):
            lines.append(f"- Algorithm: {algo['desc']}")

        flow = design_doc.get("logic_flow", {})
        steps = flow.get("flow_steps", [])
        if steps:
            lines.append("- Logic Flow Steps:")
            for step in steps:
                lines.append(f"    {step}")

        call_rel = design_doc.get("call_relation", {})
        called = call_rel.get("called_functions", [])
        if called:
            lines.append(f"- Called Functions: {', '.join(called)}")

        lines.append("")  # trailing blank line
        return "\n".join(lines)

    @staticmethod
    def _build_annotate_prompt(test_code: str, design_doc: dict, source_code: str) -> str:
        design_doc_info = LLMService._format_design_doc(design_doc)
        source_section = (
            f"\n被测函数源代码：\n```c\n{source_code}\n```\n" if source_code else ""
        )
        return f"""
你是一位 C 语言测试专家，同时也是代码审查员。下面提供了三份材料：
1. 函数设计文档（规定了函数应有的行为）
2. 被测函数的实际源代码
3. 已生成的 Unity 单元测试代码

{design_doc_info}
{source_section}
已有测试代码：
```c
{test_code}
```

你的任务分为**两个维度**：

【维度A：注释插入】
在每一条 `TEST_ASSERT_*` 断言的紧下方，插入一行中文注释，格式为：
`// [设计文档] 预期：<具体预期值或行为> | 原因：<针对该测试场景的分析——在此输入/条件下，设计文档的哪条规定（算法/流程步骤/返回值说明）决定了该输出>`

注释中"原因"要求：
- 必须结合当前测试场景（测试函数名、传入参数）进行具体分析
- 不能只写"0表示正确"这类泛泛的描述
- 若设计文档未覆盖此场景，写：`原因：设计文档未覆盖此场景，依据代码逻辑推断`

【维度B：缺陷检测】
逐一对比：**设计文档规定的行为** vs **源代码的实际实现**。
- 若发现源代码某处实现与设计文档存在偏差（如条件判断错误、返回值错误、流程顺序错误、边界处理缺失等），则：
  1. 在对应断言的注释末尾追加 ` | ⚠️ 疑似缺陷：<简要描述源代码中哪行/哪个逻辑与设计文档不符>`
  2. 若该缺陷会导致当前断言失败，在注释开头改为 `// [设计文档·缺陷] `
- 若源代码与设计文档完全一致，不需要添加缺陷标注

输出规则：
1. **不要修改任何测试逻辑**，包括断言的参数、测试函数名、setUp/tearDown 等。
2. 输出完整的、带注释的 C 代码文件，用 ```c 代码块包裹。
3. 除了插入注释，**不做任何其他修改**。
"""

    _ANNOTATE_SYSTEM_MSG = "你是一位专业的 C 语言单元测试工程师。"
    _ANNOTATE_TIMEOUT = 90.0

    @staticmethod
    def _mock_annotate(test_code: str) -> str:
        lines = test_code.split('\n')
        result = []
        for line in lines:
            result.append(line)
            if 'TEST_ASSERT' in line:
                result.append('// [设计文档] 预期：见设计文档 | 原因：Mock 模式，无 API 密钥')
        return '\n'.join(result)

    @staticmethod
    async def annotate_with_design_doc_stream(
        test_code: str,
        design_doc: dict,
        source_code: str = ""
    ):
        """
        第二步：给已生成的测试代码，基于设计文档在每条断言下插入中文注释。
        同时对比源代码与设计文档，若发现偏差则在注释中标注。
        """
        prompt = LLMService._build_annotate_prompt(test_code, design_doc, source_code)

        if os.getenv("LLM_DEBUG_PROMPT") == "1":
            print(f"DEBUG: Annotate Prompt:\n{prompt}")

        if _is_mock_mode():
            for char in LLMService._mock_annotate(test_code):
                yield char
                await asyncio.sleep(0.001)
            return

        messages = [
            {"role": "system", "content": LLMService._ANNOTATE_SYSTEM_MSG},
            {"role": "user", "content": prompt},
        ]
        async for chunk in LLMService._chat_stream(
            messages,
            temperature=0.2,
            timeout=LLMService._ANNOTATE_TIMEOUT,
            error_template="/* Error annotating test case: {e} */",
        ):
            yield chunk

    @staticmethod
    async def annotate_with_design_doc(
        test_code: str,
        design_doc: dict,
        source_code: str = ""
    ) -> str:
        """
        第二步（非流式版本）：给已生成的测试代码，基于设计文档在每条断言下插入中文注释。
        同时对比源代码与设计文档，若发现偏差则在注释中标注。
        """
        prompt = LLMService._build_annotate_prompt(test_code, design_doc, source_code)

        if os.getenv("LLM_DEBUG_PROMPT") == "1":
            print(f"DEBUG: Annotate Prompt:\n{prompt}")

        if _is_mock_mode():
            return LLMService._mock_annotate(test_code)

        messages = [
            {"role": "system", "content": LLMService._ANNOTATE_SYSTEM_MSG},
            {"role": "user", "content": prompt},
        ]
        content = await LLMService._chat_once(
            messages,
            temperature=0.2,
            timeout=LLMService._ANNOTATE_TIMEOUT,
        )
        if content is None:
            return test_code  # fallback: return unannotated code
        return LLMService._extract_code(content)

    @staticmethod
    def _extract_function_names_warning(file_code: str) -> str:
        """
        Parse top-level C function definitions from file_code and return a
        formatted warning string listing each name.  Used to tell the LLM
        exactly which symbols it must NOT redefine.
        """
        if not file_code:
            return "(source file is empty)"
        # Match function definitions: identifier followed by '(' at column 0,
        # preceded by a return-type token on the same or previous line.
        # We look for lines that start with an identifier directly (no indent)
        # and are followed by '(' — a reliable heuristic for top-level defs.
        pattern = re.compile(
            r'^(?:(?:static|inline|extern|const|volatile|unsigned|signed|long|short)\s+)*'
            r'(?:struct\s+\w+\s*\*?\s*|enum\s+\w+\s*|union\s+\w+\s*|\w+\s*\*?\s*)'
            r'(?P<fname>[a-zA-Z_]\w*)\s*\(',
            re.MULTILINE
        )
        # Keywords that should never be treated as function names
        KEYWORDS = {
            'if', 'for', 'while', 'switch', 'return', 'sizeof', 'typedef',
            'struct', 'enum', 'union', 'else', 'do', 'goto', 'case', 'default',
            'break', 'continue', 'void', 'int', 'char', 'float', 'double',
            'unsigned', 'signed', 'long', 'short', 'static', 'extern',
            'const', 'volatile', 'inline', 'register', 'auto',
        }
        names = []
        for m in pattern.finditer(file_code):
            fname = m.group('fname')
            if fname not in KEYWORDS and fname not in names:
                names.append(fname)
        if not names:
            return "(no function definitions detected)"
        return "  " + ", ".join(f"`{n}`" for n in names)

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
