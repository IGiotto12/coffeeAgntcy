# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Market negotiation protocol definitions"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.market.models import RequestForQuote, Bid

# Protocol message types
RFQ_MESSAGE_TYPE = "RFQ"
BID_MESSAGE_TYPE = "BID"
BID_ACCEPTED_MESSAGE_TYPE = "BID_ACCEPTED"
BID_REJECTED_MESSAGE_TYPE = "BID_REJECTED"
AUCTION_COMPLETE_MESSAGE_TYPE = "AUCTION_COMPLETE"


def format_rfq_message(rfq: "RequestForQuote") -> str:
    """Format RFQ as a message to send to farms"""
    parts = [
        f"RFQ #{rfq.rfq_id[:8]}",
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
    parts.append("Format: 'We can supply [quantity] lbs at $[price]/lb, delivery in [days] days, quality score [score].'")
    return "\n".join(parts)


def parse_bid_from_farm_response(farm_name: str, response_text: str) -> "Bid":
    """Parse farm response into a Bid object"""
    from agents.market.models import Bid
    
    # Extract price - look for $X.XX per lb or /lb
    price_patterns = [
        r'\$?(\d+\.?\d*)\s*(?:per\s*lb|/lb|per\s*pound)',
        r'price[:\s]+\$?(\d+\.?\d*)',
        r'at\s+\$?(\d+\.?\d*)',
    ]
    price = None
    for pattern in price_patterns:
        price_match = re.search(pattern, response_text, re.IGNORECASE)
        if price_match:
            price = float(price_match.group(1))
            break
    
    # Extract quantity - look for X lbs or pounds
    qty_patterns = [
        r'(\d+)\s*(?:lbs?|pounds?)',
        r'quantity[:\s]+(\d+)',
        r'supply\s+(\d+)',
    ]
    quantity = None
    for pattern in qty_patterns:
        qty_match = re.search(pattern, response_text, re.IGNORECASE)
        if qty_match:
            quantity = int(qty_match.group(1))
            break
    
    # Extract delivery time - look for X days
    delivery_patterns = [
        r'(\d+)\s*(?:days?|day)',
        r'delivery[:\s]+(\d+)',
        r'in\s+(\d+)\s+days?',
    ]
    delivery_days = None
    for pattern in delivery_patterns:
        delivery_match = re.search(pattern, response_text, re.IGNORECASE)
        if delivery_match:
            delivery_days = int(delivery_match.group(1))
            break
    
    # Extract quality score (optional)
    quality_patterns = [
        r'quality[:\s]+(\d+\.?\d*)',
        r'quality\s+score[:\s]+(\d+\.?\d*)',
        r'score[:\s]+(\d+\.?\d*)',
    ]
    quality = 1.0  # Default
    for pattern in quality_patterns:
        quality_match = re.search(pattern, response_text, re.IGNORECASE)
        if quality_match:
            quality = float(quality_match.group(1))
            # Normalize to 0-1 range if > 1
            if quality > 1.0:
                quality = quality / 10.0 if quality <= 10.0 else 1.0
            break
    
    if not all([price, quantity, delivery_days]):
        raise ValueError(
            f"Could not parse complete bid from {farm_name}. "
            f"Found: price={price}, quantity={quantity}, delivery={delivery_days}. "
            f"Response: {response_text[:200]}"
        )
    
    return Bid(
        farm_name=farm_name,
        price_per_lb=price,
        quantity_available=quantity,
        delivery_days=delivery_days,
        quality_score=quality,
        message=response_text
    )
