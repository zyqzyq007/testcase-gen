# Docker 构建与运行说明

本文档说明当前项目的 Docker 构建与运行方式。

## 1. 构建镜像

在项目根目录执行以下命令构建镜像：

```bash
docker build -t testcase-gen:latest .
```

## 2. 运行容器

使用以下命令启动容器（将容器内的 8000 端口映射到宿主机的 8001 端口）：

```bash
docker run -d -p 8001:8000 --name testcase-gen-test testcase-gen:latest
```

### 数据持久化运行（推荐）

为了防止容器重启后丢失上传的项目和生成的测试用例，建议挂载 `workspaces` 目录：

```bash
docker run -d \
  -p 8001:8000 \
  --name testcase-gen-test \
  -v $(pwd)/workspaces:/app/workspaces \
  testcase-gen:latest
```

> **参数说明**：
> * `-p 8001:8000`: 将宿主机的 **8001** 端口映射到容器的 **8000** 端口。
> * `--name testcase-gen-test`: 指定容器名称。
> * `-v ...`: 挂载本地目录以持久化数据。

## 3. 访问服务

启动成功后，可以通过以下地址访问：

- **前端页面**：[http://localhost:8001/index.html](http://localhost:8001/index.html)
- **后端 API 健康检查**：[http://localhost:8001/api/health](http://localhost:8001/api/health)
- **API 文档 (Swagger)**：[http://localhost:8001/docs](http://localhost:8001/docs)

## 4. 常用管理命令

* **查看日志**：
  ```bash
  docker logs -f testcase-gen-test
  ```

* **停止并删除容器**：
  ```bash
  docker stop testcase-gen-test && docker rm testcase-gen-test
  ```

* **进入容器终端**：
  ```bash
  docker exec -it testcase-gen-test bash
  ```

## 5. 环境验证

容器内部已集成了完整的 C 语言编译和调试环境（GCC, GDB, Valgrind, Joern 等）。如果需要验证环境是否正常，可以运行：

```bash
# 验证 GCC, Math 库 (-lm) 和 Zlib (-lz)
docker exec testcase-gen-test sh -c 'echo "#include <zlib.h>\nint main(){return 0;}" > /tmp/check.c && gcc /tmp/check.c -o /tmp/check -lz && echo "Environment OK"'
```
