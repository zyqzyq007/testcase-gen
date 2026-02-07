# Docker 构建与运行说明

本文档说明当前项目的 Docker 构建与运行方式（生产模式，前端静态化 + 单端口）。

## 构建镜像

在项目根目录执行：

```bash
docker build -t c-test-platform:prod .
```

## 运行容器（单容器）

```bash
docker run -d --name c-test-platform \
  -p 8000:8000 \
  -v $(pwd)/workspaces:/app/workspaces \
  c-test-platform:prod
```

访问方式：
- 前端：`http://<host>:8000`
- 后端 API：`http://<host>:8000/api/...`

## 使用 Docker Compose

```bash
docker compose up -d --build
```

默认会把 `./workspaces` 挂载到容器内的 `/app/workspaces` 用于持久化上传文件和测试结果。

## 生产模式说明

当前 Dockerfile 使用多阶段构建：
- **前端阶段**：构建 `Frontend/dist` 静态资源
- **运行阶段**：仅保留 Python 运行时、Joern 及依赖，并在 FastAPI 中挂载静态目录

启动后只对外暴露 **8000** 端口：
- FastAPI API 服务
- 前端静态页面

## 常见问题

### 1. 前端无法访问
确认容器端口映射 `-p 8000:8000` 是否生效，并使用 `http://<host>:8000` 访问。

### 2. 持久化数据不生效
确认运行时已挂载 `./workspaces:/app/workspaces`。
