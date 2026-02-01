# Performance Analyzer - 快速展示指南

## 🎯 功能说明

Performance Analyzer 从 ClickHouse 追踪数据中分析农场的历史性能，提供：
- 响应时间统计（平均、P50、P95、P99）
- 成功率
- 稳定性分数
- 推荐超时时间

## 🚀 快速测试

### 1. 启动服务

确保 Auction Supervisor 正在运行：
```bash
# 如果使用 Docker Compose
docker compose up auction-supervisor

# 或者直接运行
cd coffeeAGNTCY/coffee_agents/lungo
python -m agents.supervisors.auction.main
```

### 2. 访问 API Endpoint

打开浏览器或使用 curl：

```bash
# 获取性能指标（使用缓存）
curl http://localhost:8000/agent/performance-metrics

# 强制刷新数据（从 ClickHouse 重新查询）
curl http://localhost:8000/agent/performance-metrics?force_refresh=true
```

### 3. 预期响应

```json
{
  "timestamp": "2025-01-31T10:00:00.000Z",
  "cache_age_seconds": 45.2,
  "farms": {
    "brazil": {
      "farm_name": "Brazil",
      "avg_response_time": 1.2,
      "p50_response_time": 1.0,
      "p95_response_time": 2.0,
      "p99_response_time": 3.0,
      "success_rate": 0.95,
      "total_requests": 100,
      "successful_requests": 95,
      "failed_requests": 5,
      "stability_score": 0.85,
      "last_updated": "2025-01-31T10:00:00.000Z",
      "recommended_timeout": 2.6
    },
    "colombia": {...},
    "vietnam": {...}
  }
}
```

## 📊 展示要点

### 对于 Hackathon 评委：

1. **数据驱动决策**
   - 展示如何从追踪数据中提取性能指标
   - 说明每个指标的含义（P95 = 95% 的请求在这个时间内完成）

2. **智能推荐**
   - `recommended_timeout` 基于 P95 + 30% buffer
   - 不同农场有不同的推荐超时（快农场 2.6s，慢农场 7.8s）

3. **缓存机制**
   - 5分钟缓存避免频繁查询 ClickHouse
   - `cache_age_seconds` 显示数据新鲜度

4. **容错设计**
   - 如果 ClickHouse 不可用，自动使用 mock 数据
   - 确保系统始终可用

## 🔧 配置

在 `.env` 文件中可以配置：

```env
# 启用/禁用性能分析器
PERFORMANCE_ANALYZER_ENABLED=true

# 缓存时间（秒）
PERFORMANCE_CACHE_TTL=300

# ClickHouse 连接
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=9000
CLICKHOUSE_USER=admin
CLICKHOUSE_PASSWORD=admin
CLICKHOUSE_DATABASE=default
```

## 📝 下一步

这个服务为后续功能提供基础：
- **动态超时**：Scout Agent 使用 `recommended_timeout`
- **市场竞价**：Market Agent 使用 `stability_score` 和 `success_rate` 加权评分
- **实时仪表板**：前端显示这些指标

## 🐛 故障排除

### 如果看到 mock 数据：

1. 检查 ClickHouse 是否运行：
   ```bash
   docker compose ps clickhouse-server
   ```

2. 检查连接配置：
   - 确认 `CLICKHOUSE_HOST` 和 `CLICKHOUSE_PORT` 正确
   - 在 Docker Compose 中，host 应该是 `clickhouse-server`，不是 `localhost`

3. 检查追踪数据：
   - 确保有 scout probe 的追踪数据
   - 在 Grafana 中查看是否有相关 spans

### 安装依赖（如果需要）：

```bash
pip install clickhouse-connect
```
