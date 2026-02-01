# Market-Based Agent Negotiation Implementation Guide

## 📋 Overview

This document provides a detailed step-by-step guide to implement a **Market-Based Agent Negotiation** system where coffee farms compete through competitive bidding to win orders.

## 🎯 Goals

1. **Competitive Bidding**: Farms submit bids with price, quantity, and delivery time
2. **Multi-Round Negotiation**: Support multiple bidding rounds for better deals
3. **Intelligent Selection**: Choose winner based on price, quality, and delivery time
4. **Transparent Process**: Users can see all bids and selection criteria

---

## 🏗️ Architecture Design

### **Components**

```
┌─────────────────┐
│  User Request   │
│  "100 lbs @     │
│   best price"   │
└────────┬────────┘
         │
         ↓
┌─────────────────────────┐
│  Market Agent           │
│  (New Component)        │
│  - Manages Auction      │
│  - Collects Bids         │
│  - Evaluates Offers     │
│  - Selects Winner       │
└────────┬────────────────┘
         │
         ↓ Broadcast RFQ
┌────────┴────────┐
│                 │
↓                 ↓                 ↓
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Brazil   │  │ Colombia │  │ Vietnam  │
│ Farm     │  │ Farm     │  │ Farm     │
│          │  │          │  │          │
│ Submit   │  │ Submit   │  │ Submit   │
│ Bid      │  │ Bid      │  │ Bid      │
└──────────┘  └──────────┘  └──────────┘
         │                 │
         └────────┬────────┘
                  ↓
         ┌─────────────────┐
         │  Market Agent   │
         │  Evaluates &    │
         │  Selects Winner  │
         └────────┬────────┘
                  ↓
         ┌─────────────────┐
         │  Order Created  │
         │  with Winner    │
         └─────────────────┘
```

---

## 📝 Step-by-Step Implementation

### **Phase 1: Data Models & Protocol Definition**

#### Step 1.1: Create Bid Data Models

**File**: `coffeeAGNTCY/coffee_agents/lungo/agents/market/models.py` (NEW)

```python
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from enum import Enum

class BidStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"

@dataclass
class Bid:
    """Represents a bid from a farm"""
    farm_name: str
    price_per_lb: float
    quantity_available: int
    delivery_days: int
    quality_score: float = 1.0  # 0.0 to 1.0, default 1.0
    bid_id: str = ""
    timestamp: datetime = None
    status: BidStatus = BidStatus.PENDING
    message: str = ""  # Optional message from farm
    
    def __post_init__(self):
        if not self.bid_id:
            from uuid import uuid4
            self.bid_id = str(uuid4())
        if not self.timestamp:
            from datetime import datetime, timezone
            self.timestamp = datetime.now(timezone.utc)

@dataclass
class RequestForQuote:
    """Request for Quote (RFQ) sent to farms"""
    rfq_id: str
    quantity: int
    max_price_per_lb: Optional[float] = None
    max_delivery_days: Optional[int] = None
    quality_requirement: Optional[float] = None
    deadline: datetime = None
    round_number: int = 1
    
    def __post_init__(self):
        if not self.deadline:
            from datetime import datetime, timezone, timedelta
            self.deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

@dataclass
class AuctionResult:
    """Result of an auction"""
    winner: Bid
    all_bids: list[Bid]
    selection_criteria: str
    total_value: float
    auction_id: str = ""
    
    def __post_init__(self):
        if not self.auction_id:
            from uuid import uuid4
            self.auction_id = str(uuid4())
```

#### Step 1.2: Create Market Protocol

**File**: `coffeeAGNTCY/coffee_agents/lungo/agents/market/protocol.py` (NEW)

```python
"""
Market negotiation protocol definitions
"""
from typing import Literal

# Protocol message types
RFQ_MESSAGE_TYPE = "RFQ"
BID_MESSAGE_TYPE = "BID"
BID_ACCEPTED_MESSAGE_TYPE = "BID_ACCEPTED"
BID_REJECTED_MESSAGE_TYPE = "BID_REJECTED"
AUCTION_COMPLETE_MESSAGE_TYPE = "AUCTION_COMPLETE"

def format_rfq_message(rfq: "RequestForQuote") -> str:
    """Format RFQ as a message to send to farms"""
    parts = [
        f"RFQ #{rfq.rfq_id}",
        f"Quantity needed: {rfq.quantity} lbs",
    ]
    if rfq.max_price_per_lb:
        parts.append(f"Maximum price: ${rfq.max_price_per_lb:.2f}/lb")
    if rfq.max_delivery_days:
        parts.append(f"Maximum delivery time: {rfq.max_delivery_days} days")
    if rfq.quality_requirement:
        parts.append(f"Minimum quality score: {rfq.quality_requirement:.2f}")
    parts.append(f"Round: {rfq.round_number}")
    parts.append("Please submit your best bid with: price per lb, available quantity, delivery days, and quality score.")
    return "\n".join(parts)

def parse_bid_from_farm_response(farm_name: str, response_text: str) -> "Bid":
    """Parse farm response into a Bid object"""
    import re
    from agents.market.models import Bid
    
    # Extract price
    price_match = re.search(r'\$?(\d+\.?\d*)\s*(?:per\s*lb|/lb|per pound)', response_text, re.IGNORECASE)
    price = float(price_match.group(1)) if price_match else None
    
    # Extract quantity
    qty_match = re.search(r'(\d+)\s*(?:lbs?|pounds?)', response_text, re.IGNORECASE)
    quantity = int(qty_match.group(1)) if qty_match else None
    
    # Extract delivery time
    delivery_match = re.search(r'(\d+)\s*(?:days?|day)', response_text, re.IGNORECASE)
    delivery_days = int(delivery_match.group(1)) if delivery_match else None
    
    # Extract quality (if mentioned)
    quality_match = re.search(r'quality[:\s]+(\d+\.?\d*)', response_text, re.IGNORECASE)
    quality = float(quality_match.group(1)) if quality_match else 1.0
    
    if not all([price, quantity, delivery_days]):
        raise ValueError(f"Could not parse complete bid from {farm_name}: {response_text}")
    
    return Bid(
        farm_name=farm_name,
        price_per_lb=price,
        quantity_available=quantity,
        delivery_days=delivery_days,
        quality_score=quality,
        message=response_text
    )
```

---

### **Phase 2: Market Agent Implementation**

#### Step 2.1: Create Market Agent Core

**File**: `coffeeAGNTCY/coffee_agents/lungo/agents/market/agent.py` (NEW)

```python
"""
Market Agent - Manages competitive bidding between farms
"""
import logging
import asyncio
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from agents.market.models import Bid, RequestForQuote, AuctionResult, BidStatus
from agents.market.protocol import format_rfq_message, parse_bid_from_farm_response
from agents.supervisors.auction.graph.tools import get_farm_card, factory, transport
from agents.supervisors.auction.graph.shared import get_factory
from agntcy_app_sdk.semantic.a2a.protocol import A2AProtocol
from a2a.types import SendMessageRequest, MessageSendParams, Message, Part, TextPart, Role
from uuid import uuid4

logger = logging.getLogger("lungo.market.agent")

class MarketAgent:
    """Manages competitive bidding and auction processes"""
    
    def __init__(self):
        self.factory = get_factory()
        self.transport = transport
    
    async def conduct_auction(
        self,
        quantity: int,
        max_price: Optional[float] = None,
        max_delivery_days: Optional[int] = None,
        quality_requirement: Optional[float] = None,
        max_rounds: int = 2,
        round_timeout: float = 10.0
    ) -> AuctionResult:
        """
        Conduct a multi-round auction to find the best farm.
        
        Args:
            quantity: Quantity needed
            max_price: Maximum acceptable price per lb
            max_delivery_days: Maximum acceptable delivery time
            quality_requirement: Minimum quality score
            max_rounds: Maximum number of bidding rounds
            round_timeout: Timeout per round in seconds
            
        Returns:
            AuctionResult with winner and all bids
        """
        rfq = RequestForQuote(
            rfq_id=str(uuid4()),
            quantity=quantity,
            max_price_per_lb=max_price,
            max_delivery_days=max_delivery_days,
            quality_requirement=quality_requirement
        )
        
        all_bids: List[Bid] = []
        best_bid: Optional[Bid] = None
        
        for round_num in range(1, max_rounds + 1):
            rfq.round_number = round_num
            logger.info(f"Starting auction round {round_num} for {quantity} lbs")
            
            # Collect bids in this round
            round_bids = await self._collect_bids(rfq, round_timeout)
            all_bids.extend(round_bids)
            
            # Evaluate bids
            valid_bids = [b for b in round_bids if self._is_valid_bid(b, rfq)]
            
            if valid_bids:
                round_best = self._select_best_bid(valid_bids, rfq)
                if best_bid is None or self._is_better_bid(round_best, best_bid, rfq):
                    best_bid = round_best
                    logger.info(f"Round {round_num} best bid: {best_bid.farm_name} @ ${best_bid.price_per_lb:.2f}/lb")
            
            # If we have a good bid and it's not the first round, we can stop
            if best_bid and round_num > 1:
                # Check if improvement is significant (e.g., >5% better)
                if round_num == 2 and valid_bids:
                    improvement = (best_bid.price_per_lb - round_best.price_per_lb) / best_bid.price_per_lb
                    if improvement < 0.05:  # Less than 5% improvement
                        logger.info(f"Stopping early: minimal improvement in round {round_num}")
                        break
        
        if not best_bid:
            raise ValueError("No valid bids received from any farm")
        
        # Create auction result
        result = AuctionResult(
            winner=best_bid,
            all_bids=all_bids,
            selection_criteria=self._get_selection_criteria(rfq),
            total_value=best_bid.price_per_lb * quantity
        )
        
        logger.info(f"Auction complete. Winner: {result.winner.farm_name} @ ${result.winner.price_per_lb:.2f}/lb")
        return result
    
    async def _collect_bids(
        self,
        rfq: RequestForQuote,
        timeout: float
    ) -> List[Bid]:
        """Collect bids from all farms in parallel"""
        farms = ['brazil', 'colombia', 'vietnam']
        rfq_message = format_rfq_message(rfq)
        
        tasks = [
            self._request_bid_from_farm(farm, rfq_message, timeout)
            for farm in farms
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        bids = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to get bid from {farms[i]}: {result}")
            elif result:
                bids.append(result)
        
        return bids
    
    async def _request_bid_from_farm(
        self,
        farm: str,
        rfq_message: str,
        timeout: float
    ) -> Optional[Bid]:
        """Request a bid from a single farm"""
        farm_name = farm.title()
        card = get_farm_card(farm)
        if not card:
            return None
        
        try:
            client = await self.factory.create_client(
                "A2A",
                agent_topic=A2AProtocol.create_agent_topic(card),
                transport=self.transport,
            )
            
            request = SendMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(
                    message=Message(
                        messageId=str(uuid4()),
                        role=Role.user,
                        parts=[Part(TextPart(text=rfq_message))],
                    ),
                )
            )
            
            response = await asyncio.wait_for(
                client.send_message(request),
                timeout=timeout
            )
            
            if response.root.result and response.root.result.parts:
                part = response.root.result.parts[0].root
                if hasattr(part, "text"):
                    response_text = part.text.strip()
                    bid = parse_bid_from_farm_response(farm_name, response_text)
                    return bid
            
            return None
            
        except asyncio.TimeoutError:
            logger.warning(f"Bid request from {farm_name} timed out")
            return None
        except Exception as e:
            logger.error(f"Error getting bid from {farm_name}: {e}")
            return None
    
    def _is_valid_bid(self, bid: Bid, rfq: RequestForQuote) -> bool:
        """Check if a bid meets RFQ requirements"""
        if bid.quantity_available < rfq.quantity:
            return False
        if rfq.max_price_per_lb and bid.price_per_lb > rfq.max_price_per_lb:
            return False
        if rfq.max_delivery_days and bid.delivery_days > rfq.max_delivery_days:
            return False
        if rfq.quality_requirement and bid.quality_score < rfq.quality_requirement:
            return False
        return True
    
    def _select_best_bid(self, bids: List[Bid], rfq: RequestForQuote) -> Bid:
        """Select the best bid based on multiple criteria"""
        if not bids:
            raise ValueError("No bids to evaluate")
        
        # Scoring function: lower price = better, faster delivery = better, higher quality = better
        scored_bids = []
        for bid in bids:
            # Normalize scores (lower is better for price and delivery, higher is better for quality)
            price_score = bid.price_per_lb / max(b.price_per_lb for b in bids)  # 0-1, lower better
            delivery_score = bid.delivery_days / max(b.delivery_days for b in bids)  # 0-1, lower better
            quality_score = 1.0 - bid.quality_score  # Invert so lower is better (for consistency)
            
            # Weighted combination (customize weights as needed)
            total_score = (
                0.5 * price_score +      # 50% weight on price
                0.3 * delivery_score +   # 30% weight on delivery
                0.2 * quality_score       # 20% weight on quality
            )
            
            scored_bids.append((total_score, bid))
        
        # Return bid with lowest total score
        scored_bids.sort(key=lambda x: x[0])
        return scored_bids[0][1]
    
    def _is_better_bid(self, new_bid: Bid, current_best: Bid, rfq: RequestForQuote) -> bool:
        """Check if new_bid is better than current_best"""
        new_score = self._calculate_bid_score(new_bid, rfq)
        current_score = self._calculate_bid_score(current_best, rfq)
        return new_score < current_score  # Lower score is better
    
    def _calculate_bid_score(self, bid: Bid, rfq: RequestForQuote) -> float:
        """Calculate a single score for a bid (lower is better)"""
        # Normalize to 0-1 range (simplified)
        price_norm = bid.price_per_lb / 10.0  # Assuming max $10/lb
        delivery_norm = bid.delivery_days / 30.0  # Assuming max 30 days
        quality_norm = 1.0 - bid.quality_score
        
        return 0.5 * price_norm + 0.3 * delivery_norm + 0.2 * quality_norm
    
    def _get_selection_criteria(self, rfq: RequestForQuote) -> str:
        """Generate human-readable selection criteria"""
        criteria = ["Price (50%)", "Delivery Time (30%)", "Quality (20%)"]
        if rfq.max_price_per_lb:
            criteria.append(f"Max price: ${rfq.max_price_per_lb:.2f}/lb")
        if rfq.max_delivery_days:
            criteria.append(f"Max delivery: {rfq.max_delivery_days} days")
        return ", ".join(criteria)
```

---

### **Phase 3: Integration with Auction Supervisor**

#### Step 3.1: Add Market Tool to Auction Supervisor

**File**: `coffeeAGNTCY/coffee_agents/lungo/agents/supervisors/auction/graph/tools.py`

Add to imports:
```python
from agents.market.agent import MarketAgent
from agents.market.models import AuctionResult
```

Add new tool:
```python
@tool
@ioa_tool_decorator(name="conduct_market_auction")
async def conduct_market_auction(
    quantity: int,
    max_price: float | None = None,
    max_delivery_days: int | None = None,
    max_rounds: int = 2
) -> str:
    """
    Conduct a competitive auction where farms bid for an order.
    Farms compete on price, delivery time, and quality.
    
    Args:
        quantity: Quantity of coffee needed (in lbs)
        max_price: Optional maximum price per lb (if specified, only bids below this are accepted)
        max_delivery_days: Optional maximum delivery time in days
        max_rounds: Number of bidding rounds (default 2, allows farms to improve bids)
        
    Returns:
        Formatted string with auction results, winner, and all bids
    """
    market_agent = MarketAgent()
    
    try:
        result = await market_agent.conduct_auction(
            quantity=quantity,
            max_price=max_price,
            max_delivery_days=max_delivery_days,
            max_rounds=max_rounds,
            round_timeout=10.0
        )
        
        # Format result for LLM
        lines = [
            f"🏆 **Auction Complete**",
            f"",
            f"**Winner:** {result.winner.farm_name}",
            f"- Price: ${result.winner.price_per_lb:.2f}/lb",
            f"- Quantity: {result.winner.quantity_available} lbs available",
            f"- Delivery: {result.winner.delivery_days} days",
            f"- Quality Score: {result.winner.quality_score:.2f}",
            f"- Total Value: ${result.total_value:.2f}",
            f"",
            f"**Selection Criteria:** {result.selection_criteria}",
            f"",
            f"**All Bids Received:**",
        ]
        
        for bid in result.all_bids:
            status_icon = "✅" if bid == result.winner else "❌"
            lines.append(
                f"{status_icon} **{bid.farm_name}**: ${bid.price_per_lb:.2f}/lb, "
                f"{bid.quantity_available} lbs, {bid.delivery_days} days, "
                f"quality {bid.quality_score:.2f}"
            )
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Market auction failed: {e}")
        return f"Auction failed: {str(e)}"
```

#### Step 3.2: Update Graph to Use Market Auction

**File**: `coffeeAGNTCY/coffee_agents/lungo/agents/supervisors/auction/graph/graph.py`

Add to imports:
```python
from agents.supervisors.auction.graph.tools import (
    ...,
    conduct_market_auction,
)
```

Update ToolNode:
```python
# Include market auction tool
orders_tools_list = [create_order, get_order_details]
if DEFAULT_MESSAGE_TRANSPORT == "NATS":
    orders_tools_list.append(scout_then_decide)
    orders_tools_list.append(conduct_market_auction)  # Add market auction
workflow.add_node(NodeStates.ORDERS_TOOLS, ToolNode(orders_tools_list))
```

Update LLM prompt in `_orders_node`:
```python
*   **For order requests, you have two options:**
    - Use `scout_then_decide` for quick availability check (fast, 2-5s timeout)
    - Use `conduct_market_auction` for competitive bidding (slower, but gets best price through competition)
    
    **When to use market auction:**
    - User asks for "best price" or "competitive bidding"
    - Large quantities (>100 lbs) where price matters
    - User wants to see multiple options and choose
    
    **When to use scout:**
    - Quick availability check
    - User just wants to know if farms can fulfill
    - Time-sensitive requests
```

---

### **Phase 4: Extend Farm Agents for Bidding**

#### Step 4.1: Add Bidding Capability to Farm Agents

**File**: `coffeeAGNTCY/coffee_agents/lungo/agents/farms/colombia/agent.py` (and similar for other farms)

Add bidding instruction to the orders agent:
```python
orders_agent = Agent(
    name="orders_agent",
    model=LiteLlm(model=LLM_MODEL),
    description="Handles order-related queries including bidding for RFQs",
    instruction="""You handle orders and bidding for the Colombia Coffee Farm.

When you receive an RFQ (Request for Quote):
1. Analyze the requirements (quantity, max price, delivery time, quality)
2. Calculate your best competitive bid:
   - Price: Competitive but profitable (typically $0.45-$0.55/lb)
   - Quantity: Confirm you can supply the requested amount
   - Delivery: Estimate realistic delivery time (3-7 days for Colombia)
   - Quality: Colombia coffee quality score (typically 0.85-0.95)

3. Format your bid response clearly:
   "We can supply [quantity] lbs at $[price]/lb, delivery in [days] days, quality score [score]."

Example bid format:
"We can supply 100 lbs at $0.48/lb, delivery in 5 days, quality score 0.90."

For regular orders (not RFQ), handle them as usual.""",
    tools=[],
)
```

---

### **Phase 5: UI Integration**

#### Step 5.1: Add Market Auction to Suggested Prompts

**File**: `coffeeAGNTCY/coffee_agents/lungo/agents/supervisors/auction/suggested_prompts.json`

Add new category:
```json
{
  "market_auction": [
    {
      "prompt": "I need 200 lbs of coffee. Run a competitive auction to get the best price.",
      "description": "Initiates a market auction where farms compete on price, delivery, and quality"
    },
    {
      "prompt": "Find me the best deal for 500 lbs, maximum $0.50/lb, delivery within 7 days",
      "description": "Market auction with specific constraints on price and delivery time"
    },
    {
      "prompt": "I want to see competitive bids from all farms for 150 lbs",
      "description": "Multi-round auction showing all bids and selection process"
    }
  ]
}
```

#### Step 5.2: Update UI to Show Auction Results

The auction results will automatically display through the existing chat interface, showing:
- Winner selection
- All bids received
- Selection criteria
- Comparison of offers

---

## 🔧 Implementation Checklist

### **Step 1: Create Directory Structure**
```bash
mkdir -p coffeeAGNTCY/coffee_agents/lungo/agents/market
```

### **Step 2: Create Files**
- [ ] `agents/market/__init__.py`
- [ ] `agents/market/models.py` - Data models
- [ ] `agents/market/protocol.py` - Protocol definitions
- [ ] `agents/market/agent.py` - Market Agent implementation

### **Step 3: Integrate with Auction Supervisor**
- [ ] Add `conduct_market_auction` tool to `tools.py`
- [ ] Update `graph.py` to include market auction tool
- [ ] Update LLM prompts to suggest market auction

### **Step 4: Extend Farm Agents**
- [ ] Update Colombia farm agent with bidding instructions
- [ ] Update Brazil farm agent with bidding instructions
- [ ] Update Vietnam farm agent with bidding instructions

### **Step 5: Testing**
- [ ] Test single-round auction
- [ ] Test multi-round auction
- [ ] Test with price constraints
- [ ] Test with delivery constraints
- [ ] Test with no valid bids scenario

---

## 📊 Example Usage Flow

### **User Request:**
```
"I need 200 lbs of coffee. Run a competitive auction to get the best price."
```

### **System Flow:**
1. **Market Agent** creates RFQ
2. **Broadcasts RFQ** to all farms (Brazil, Colombia, Vietnam)
3. **Farms respond** with bids:
   - Brazil: $0.52/lb, 200 lbs, 6 days, quality 0.88
   - Colombia: $0.48/lb, 200 lbs, 5 days, quality 0.90
   - Vietnam: $0.50/lb, 200 lbs, 7 days, quality 0.85

4. **Round 2** (if enabled):
   - Farms can improve their bids
   - Colombia: $0.47/lb (improved)
   - Brazil: $0.51/lb (improved)

5. **Market Agent evaluates**:
   - Scores each bid (price 50%, delivery 30%, quality 20%)
   - Selects winner: **Colombia** @ $0.47/lb

6. **Result displayed**:
   - Winner announcement
   - All bids comparison
   - Selection criteria explanation

---

## 🎯 Key Features

1. **Multi-Round Bidding**: Farms can improve bids in subsequent rounds
2. **Intelligent Scoring**: Weighted combination of price, delivery, quality
3. **Constraint Handling**: Respects max price, delivery time, quality requirements
4. **Transparency**: Shows all bids and selection process
5. **Extensible**: Easy to add new criteria or change weights

---

## 🚀 Next Steps After Implementation

1. **Add Trust-Based Weighting**: Factor in farm reliability
2. **Historical Price Tracking**: Use past bids to inform decisions
3. **Automated Negotiation**: Let farms automatically improve bids
4. **Auction Types**: Support different auction formats (Dutch, English, etc.)
