# 项目概览

HalloTickets 是基于 Android 真机的本地自动化系统，当前仅发布和维护 `Mobile` 路线。

## 当前结论

- `Mobile`：当前主推路线
如果你的目标是现在把流程跑通，直接从 `mobile/` 开始。

## 当前主推方案

### Mobile 端 — UIAutomator2 Android 自动化 (`mobile/`)

- **状态**: 主推
- **技术栈**: Python + UIAutomator2 (u2 直连)
- **原理**: 通过 u2 直连 Android 真机/模拟器操作大麦 APP，无需额外服务进程
- **登录**: APP 保持登录态
- **特点**: 坐标级点击优化（~30-60ms/操作）、支持真机、最接近真实购票链路、可根据 `item_url` 自动搜索并进入目标演出
- **适合**: 想按 README 直接上手的新用户

## 构建与运行

### Mobile 端（推荐）

```bash
poetry install
./mobile/scripts/start_ticket_grabbing.sh --yes
```

## 测试

```bash
poetry run test              # 运行测试
poetry run pytest --cov      # 带覆盖率
poetry run pytest -k "name"  # 按名称运行单个测试
poetry run pytest -m unit    # 按标记运行
```
