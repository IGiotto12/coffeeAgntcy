# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Data models for market negotiation"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class BidStatus(str, Enum):
    """Status of a bid in the auction"""
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
    timestamp: Optional[datetime] = None
    status: BidStatus = BidStatus.PENDING
    message: str = ""  # Optional message from farm
    
    def __post_init__(self):
        if not self.bid_id:
            self.bid_id = str(uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc)


@dataclass
class RequestForQuote:
    """Request for Quote (RFQ) sent to farms"""
    rfq_id: str
    quantity: int
    max_price_per_lb: Optional[float] = None
    max_delivery_days: Optional[int] = None
    quality_requirement: Optional[float] = None
    deadline: Optional[datetime] = None
    round_number: int = 1
    
    def __post_init__(self):
        if not self.deadline:
            from datetime import timedelta
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
            self.auction_id = str(uuid4())
