import os
import json
import asyncio
import uuid

# Use a writable workspace under the user's home to avoid permission issues
USER_WS = os.path.join(os.path.expanduser('~'), '.local_testcase_workspaces')
os.makedirs(USER_WS, exist_ok=True)
# Ensure backend modules pick up this workspace path when imported
os.environ['UNIPORTAL_STORAGE_PATH'] = USER_WS
os.environ['LOCAL_WORKSPACES_DIR'] = USER_WS

from app.services.runner_service import RunnerService

WORKSPACES = os.path.abspath(USER_WS)
PROJECT_ID = "proj_test_py"
PROJECT_DIR = os.path.join(WORKSPACES, PROJECT_ID)
SAMPLE_REL = "sample_py/sample.py"

TEST_CODE = '''import pytest
from sample_py.sample import add, divide, Calculator


def test_add_basic():
    assert add(1, 2) == 3


def test_divide_normal():
    assert divide(6, 2) == 3


def test_divide_zero():
    with pytest.raises(ValueError):
        divide(1, 0)


def test_multiply():
    calc = Calculator()
    assert calc.multiply(3, 4) == 12


def test_is_even():
    calc = Calculator()
    assert calc.is_even(4) is True
'''

async def main():
    os.makedirs(PROJECT_DIR, exist_ok=True)
    # Copy sample.py if not exists
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'input', 'sample_py', 'sample.py'))
    dest_dir = os.path.join(PROJECT_DIR, 'sample_py')
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, 'sample.py')
    if not os.path.exists(dest):
        with open(src, 'r') as fsrc, open(dest, 'w') as fdst:
            fdst.write(fsrc.read())

    # Write minimal meta.json
    meta = {
        "project_name": "sample_py",
        "language": "python",
        "test_framework": "pytest",
        "dependency_manager": "pip",
    }
    with open(os.path.join(PROJECT_DIR, 'meta.json'), 'w') as f:
        json.dump(meta, f)

    # Ensure the module-level TASKS_DIR points to our USER_WS _tasks
    import importlib
    runner_mod = importlib.import_module('app.services.runner_service')
    runner_mod.TASKS_DIR = os.path.abspath(os.path.join(WORKSPACES, '_tasks'))
    os.makedirs(runner_mod.TASKS_DIR, exist_ok=True)

    # Create task via RunnerService.create_task
    function_id = "sample_py/sample.py_1"
    task_id = RunnerService.create_task(
        project_id=PROJECT_ID,
        function_id=function_id,
        function_name='add',
        test_code=TEST_CODE,
        source_file_path=SAMPLE_REL,
        start_line=None,
        end_line=None,
        language='python',
        test_framework='pytest'
    )

    print('Created task', task_id)

    # Execute task
    print('Executing task... (this may take a while)')
    result = await RunnerService.execute_task(task_id)
    print('Result:')
    print('compile_success:', result.compile_success)
    print('install_success:', result.install_success)
    if result.test_result:
        print('passed', result.test_result.passed, 'failed', result.test_result.failed, 'total', result.test_result.total)
    if result.coverage:
        print('coverage files:', [f.file for f in result.coverage.files])
    print('stdout:\n', result.stdout)
    print('stderr:\n', result.stderr)

if __name__ == '__main__':
    asyncio.run(main())
