#!/usr/bin/env bash
#
# 从 testcase-gen.tar 加载镜像并启动容器
# 参考: DOCKER.md 第 2 节 "数据持久化运行（推荐）"
#

set -euo pipefail

# ---- 配置 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAR_FILE="${SCRIPT_DIR}/testcase-gen.tar"
IMAGE="testcase-gen:latest"
CONTAINER="testcase-gen-test"
HOST_PORT="8007"
CONTAINER_PORT="8000"
WORKSPACE_DIR="${SCRIPT_DIR}/workspaces"

# ---- 前置检查 ----
if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 docker 命令，请先安装 Docker。" >&2
  exit 1
fi

if [[ ! -f "${TAR_FILE}" ]]; then
  echo "[ERROR] 镜像 tar 文件不存在: ${TAR_FILE}" >&2
  exit 1
fi

# ---- 加载镜像 ----
echo "[INFO] 从 ${TAR_FILE} 加载镜像..."
LOAD_OUTPUT="$(docker load -i "${TAR_FILE}")"
echo "${LOAD_OUTPUT}"

# 若 tar 中镜像名与 IMAGE 不一致，尝试从 docker load 输出中识别并打 tag
LOADED_IMAGE="$(echo "${LOAD_OUTPUT}" | sed -n 's/^Loaded image: //p' | head -n1)"
if [[ -n "${LOADED_IMAGE}" && "${LOADED_IMAGE}" != "${IMAGE}" ]]; then
  echo "[INFO] 将 ${LOADED_IMAGE} 重新标记为 ${IMAGE}"
  docker tag "${LOADED_IMAGE}" "${IMAGE}"
fi

# ---- 清理旧容器 ----
if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo "[INFO] 移除已存在的同名容器: ${CONTAINER}"
  docker rm -f "${CONTAINER}" >/dev/null
fi

# ---- 准备挂载目录 ----
mkdir -p "${WORKSPACE_DIR}"

# ---- 启动容器 ----
echo "[INFO] 启动容器 ${CONTAINER} (${HOST_PORT} -> ${CONTAINER_PORT})..."
docker run -d \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  --name "${CONTAINER}" \
  -v "${WORKSPACE_DIR}:/app/workspaces" \
  "${IMAGE}"

# ---- 状态提示 ----
echo
echo "[OK] 容器已启动。访问入口："
echo "  - 前端页面 : http://localhost:${HOST_PORT}/index.html"
echo "  - API 健康 : http://localhost:${HOST_PORT}/api/health"
echo "  - API 文档 : http://localhost:${HOST_PORT}/docs"
echo
echo "查看日志: docker logs -f ${CONTAINER}"
echo "停止容器: docker stop ${CONTAINER} && docker rm ${CONTAINER}"
