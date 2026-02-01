# Scout Agent Workflow & Failure Analysis

## Complete Workflow Diagram

```
User Input: "Can you find me 100 lbs of coffee at $0.55/lb? I need it fast."
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. SUPERVISOR NODE (graph.py:122-159)                      │
│    - Analyzes user message                                  │
│    - Detects: quantity (100 lbs) + price ($0.55/lb)        │
│    - Routes to: NodeStates.ORDERS                           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ORDERS NODE (graph.py:363-477)                           │
│    - First iteration: No tool results yet                   │
│    - LLM decides to call: scout_then_decide()              │
│    - Tool call created and sent to ToolNode                │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. TOOL NODE (graph.py:91)                                  │
│    - Executes: scout_then_decide(prompt, prefer_farm=None) │
│    - Calls: tools.py:634-704                                │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. SCOUT_THEN_DECIDE (tools.py:634-704)                    │
│    - Checks: SCOUT_ENABLED (should be True)                 │
│    - Calls: scout_probe_farms(prompt, timeout=2.0)         │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. SCOUT_PROBE_FARMS (tools.py:343-375)                    │
│    - Creates 3 parallel tasks:                              │
│      • _probe_single_farm("brazil", 2.0s)                  │
│      • _probe_single_farm("colombia", 2.0s)                │
│      • _probe_single_farm("vietnam", 2.0s)                 │
│    - Waits for all with asyncio.gather()                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. _PROBE_SINGLE_FARM (tools.py:238-340)                    │
│    For each farm (Brazil, Colombia, Vietnam):               │
│    │                                                         │
│    ├─ Get AgentCard (tools.py:251)                         │
│    ├─ Create A2A client (tools.py:262-266)                │
│    │   • agent_topic = A2AProtocol.create_agent_topic(card)│
│    │   • transport = NATS (from config)                    │
│    │                                                         │
│    ├─ Send message with timeout (tools.py:279-283)         │
│    │   • asyncio.wait_for(client.send_message(), 2.0s)    │
│    │                                                         │
│    └─ Handle response (tools.py:287-322)                    │
│       • SUCCESS → FarmProbeResult(status="ok")             │
│       • TIMEOUT → FarmProbeResult(status="timeout")         │
│       • ERROR → FarmProbeResult(status="error")             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. RETURN TO SCOUT_THEN_DECIDE                              │
│    - Formats results (tools.py:672-686)                     │
│    - Returns summary string like:                           │
│      "Brazil: ✓ Available - [response]                      │
│       Colombia: ⏱ No response (exceeded 2s limit)           │
│       Vietnam: ✗ Issue - [error]"                          │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. BACK TO ORDERS NODE (graph.py:395-411)                   │
│    - Receives ToolMessage with scout_then_decide result     │
│    - Checks: Does result contain "✓ Available"?            │
│      • YES → Mark as SUCCESS                                │
│      • NO → Mark as PARTIAL_FAILURE (any_tool_failed=True) │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 9. LLM GENERATES RESPONSE (graph.py:432-477)                 │
│    - Receives context with tool results                     │
│    - If any_tool_failed=True:                              │
│      • Instruction says: "Acknowledge failure"              │
│      • LLM generates generic error message                 │
│    - If all succeeded:                                     │
│      • LLM uses summary to pick best farm                  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
User sees: "Unfortunately, I was unable to gather responses..."
```

## The Problem: Where It's Failing

### **Failure Point 1: Farm Communication (tools.py:262-283)**

**Location**: `_probe_single_farm()` function

**What happens**:
```python
# Line 262-266: Create A2A client
client = await factory.create_client(
    "A2A",
    agent_topic=A2AProtocol.create_agent_topic(card),  # e.g., "agent/brazil-coffee-farm-agent"
    transport=transport,  # NATS transport
)

# Line 279-283: Send message with 2-second timeout
response = await asyncio.wait_for(
    client.send_message(request),
    timeout=timeout_sec  # 2.0 seconds
)
```

**Possible failures**:
1. **NATS Connection Issue**: Can't connect to `nats://localhost:4222`
2. **Farm Not Subscribed**: Farm not listening on the correct topic
3. **Timeout**: Farm takes > 2 seconds to respond
4. **A2A Protocol Error**: Message format or routing issue

### **Failure Point 2: All Farms Fail (tools.py:401-411)**

**Location**: Orders node failure detection

**What happens**:
```python
# Line 401-411: Check if Scout Agent succeeded
if tool_msg.name == "scout_then_decide":
    has_success = "✓ Available" in result_str
    if has_success:
        # SUCCESS - at least one farm responded
    else:
        # FAILURE - ALL farms failed
        any_tool_failed = True
        tool_results_summary.append(f"PARTIAL_FAILURE...")
```

**Problem**: If ALL farms timeout or error, `has_success = False`, so `any_tool_failed = True`

### **Failure Point 3: LLM Generates Generic Error (graph.py:442-450)**

**Location**: Orders node prompt template

**What happens**:
```python
# Line 442-450: Instructions when tool fails
2. **If ANY tool call result indicates a FAILURE:**
    *   Acknowledge the failure to the user
    *   Inform the user that the request could not be completed
    *   DO NOT attempt to call the same or any other tool again
    *   Your response MUST NOT contain any tool calls.
```

**Problem**: LLM sees `PARTIAL_FAILURE` and follows instructions to "acknowledge failure" without trying again or using partial information.

## Root Cause Analysis

### **Why Farms Might Not Respond**

1. **NATS Not Running**: Check if NATS server is accessible
2. **Farms Not Connected**: Farms might not be subscribed to their topics
3. **Topic Mismatch**: Agent topic doesn't match what farms are listening to
4. **2-Second Timeout Too Short**: Farms might need more time to process
5. **Network Issues**: Docker networking or firewall blocking

### **Why LLM Gives Generic Error**

The prompt instructions (line 442-450) tell the LLM:
- "If ANY tool call result indicates a FAILURE"
- "DO NOT attempt to call the same or any other tool again"
- "Acknowledge the failure"

So even if Scout Agent returns:
```
Brazil: ⏱ No response (exceeded 2s limit)
Colombia: ✗ Issue - Connection refused
Vietnam: ✗ Issue - Timeout
```

The LLM sees `PARTIAL_FAILURE` and generates a generic error instead of:
- Explaining which farms had issues
- Suggesting to try again
- Using any partial information

## Solutions

### **Immediate Fix: Improve LLM Instructions**

Update the prompt to handle Scout Agent partial results better:

```python
# In graph.py:432-477, update instructions:
4. **If scout_then_decide returned results (even with some failures):**
    *   Review the summary - it shows which farms responded, timed out, or had issues
    *   If ANY farm shows "✓ Available", use that information to help the user
    *   If ALL farms failed, explain the specific issues (timeout, connection, etc.)
    *   Suggest alternatives or retry options
```

### **Long-term Fix: Better Error Handling**

1. **Increase timeout** for initial testing (5 seconds instead of 2)
2. **Add retry logic** for transient failures
3. **Better logging** to see actual error messages
4. **Health checks** to verify farms are reachable before probing
