# 生成真实追踪数据 - 简单指南

## 🎯 目标

运行一些 prompts 来生成追踪数据，这样 Performance Analyzer 就能读取真实数据而不是 mock 数据。

## 🚀 快速步骤

### 1. 确保服务运行

```bash
# 确保 Auction Supervisor 在运行
# 如果使用 Docker Compose
docker compose up auction-supervisor

# 或者直接运行
cd coffeeAGNTCY/coffee_agents/lungo
python -m agents.supervisors.auction.main
```

### 2. 运行数据生成脚本

```bash
cd coffeeAGNTCY/coffee_agents/lungo
python generate_trace_data.py
```

这个脚本会：
- 运行 10 个 prompts（触发 scout agent）
- 每个 prompt 会探测所有农场
- 生成追踪数据写入 ClickHouse
- 等待 2 秒再运行下一个（避免过载）

### 3. 等待数据写入

运行完脚本后，等待 10-20 秒让追踪数据写入 ClickHouse。

### 4. 测试 Performance Analyzer

```bash
# 方式 1: 运行测试脚本
python test_performance_analyzer.py

# 方式 2: 访问 API
curl http://localhost:8000/agent/performance-metrics?force_refresh=true
```

## 📊 预期结果

如果成功：
- 你会看到真实的响应时间数据
- 每个农场会有不同的性能指标
- 数据基于实际运行结果

如果还是 mock 数据：
- 检查 ClickHouse 连接（可能需要安装 `clickhouse-connect`）
- 检查是否有追踪数据（在 Grafana 中查看）
- 确认追踪数据中有 farm 相关信息

## 💡 提示

- 运行 10 次 prompt 通常足够生成基础数据
- 如果数据不够，可以再运行几次
- 数据会累积（24小时内的数据都会被分析）
