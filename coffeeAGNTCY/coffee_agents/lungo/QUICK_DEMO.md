# Performance Analyzer - 快速展示

## ✅ 已完成的功能

Performance Analyzer Service 已经创建并可以展示！

## 🚀 两种展示方式

### 方式 1: 命令行测试（最简单）

```bash
cd coffeeAGNTCY/coffee_agents/lungo
python test_performance_analyzer.py
```

**输出示例：**
- 显示每个农场的性能指标
- 推荐超时时间
- 关键洞察（最快、最慢、最稳定）

### 方式 2: API Endpoint（更专业）

1. **启动 Auction Supervisor**：
   ```bash
   # 如果使用 Docker Compose
   docker compose up auction-supervisor
   
   # 或者直接运行
   python -m agents.supervisors.auction.main
   ```

2. **访问 API**：
   ```bash
   # 浏览器访问
   http://localhost:8000/agent/performance-metrics
   
   # 或使用 curl
   curl http://localhost:8000/agent/performance-metrics
   ```

3. **强制刷新数据**：
   ```bash
   curl http://localhost:8000/agent/performance-metrics?force_refresh=true
   ```

## 📊 展示要点

### 对 Hackathon 评委说：

1. **"我们实现了数据驱动的性能分析"**
   - 从 ClickHouse 追踪数据中提取性能指标
   - 自动计算响应时间分布（P50, P95, P99）
   - 计算成功率和稳定性分数

2. **"智能推荐超时时间"**
   - 每个农场有不同的推荐超时
   - 基于 P95 + 30% buffer
   - Brazil: 2.6s（快）, Vietnam: 7.8s（慢）

3. **"缓存机制优化性能"**
   - 5分钟缓存避免频繁查询
   - 可强制刷新获取最新数据

4. **"容错设计"**
   - 如果 ClickHouse 不可用，自动使用 mock 数据
   - 确保系统始终可用

## 🎯 下一步集成

这个服务为后续功能提供基础：
- ✅ **已完成**：Performance Analyzer Service
- 🔄 **下一步**：Scout Agent 使用 `recommended_timeout` 进行动态超时
- 🔄 **未来**：Market Agent 使用性能数据加权评分

## 📝 文件位置

- **服务实现**：`services/performance_analyzer.py`
- **API Endpoint**：`agents/supervisors/auction/main.py` (line ~219)
- **测试脚本**：`test_performance_analyzer.py`
- **配置**：`config/config.py` (新增配置项)

## 💡 展示技巧

1. **先运行测试脚本**：展示数据输出
2. **然后展示 API**：说明这是可集成的服务
3. **解释设计**：说明为什么这样设计（缓存、容错、智能推荐）
4. **展示未来**：说明如何集成到 Scout Agent 和 Market Agent
