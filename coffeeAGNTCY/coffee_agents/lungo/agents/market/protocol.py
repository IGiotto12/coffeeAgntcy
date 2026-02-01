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
        f"REQUEST FOR QUOTATION (RFQ) #{rfq.rfq_id[:8]}",
        "",
        "I need to place an order for coffee. Please provide your best bid with the following information:",
        "",
        f"**Quantity needed:** {rfq.quantity} lbs",
    ]
    if rfq.max_price_per_lb:
        parts.append(f"**Maximum price:** ${rfq.max_price_per_lb:.2f} per lb")
    if rfq.max_delivery_days:
        parts.append(f"**Maximum delivery time:** {rfq.max_delivery_days} days")
    if rfq.quality_requirement:
        parts.append(f"**Minimum quality score:** {rfq.quality_requirement:.2f}")
    parts.append(f"**Bidding round:** {rfq.round_number}")
    parts.append("")
    parts.append("**REQUIRED RESPONSE FORMAT:**")
    parts.append("Please respond EXACTLY in this format:")
    parts.append("'We can supply [NUMBER] lbs at $[NUMBER]/lb, delivery in [NUMBER] days, quality score [NUMBER between 0.0 and 1.0].'")
    parts.append("")
    parts.append("**Example:** 'We can supply 200 lbs at $0.48/lb, delivery in 5 days, quality score 0.95.'")
    parts.append("")
    parts.append("Please provide your best competitive bid now.")
    return "\n".join(parts)


def parse_bid_from_farm_response(farm_name: str, response_text: str, original_prompt: str = None) -> "Bid":
    """
    Parse farm response into a Bid object with improved pattern matching.
    
    If the response doesn't contain bid information (e.g., it's an order confirmation),
    try to extract information from the original prompt.
    
    Args:
        farm_name: Name of the farm
        response_text: The farm's response text
        original_prompt: Optional original prompt that may contain quantity/price information
    """
    from agents.market.models import Bid
    import re
    
    # Normalize response text for easier parsing
    response_lower = response_text.lower()
    
    # Try to extract quantity and price from original prompt if provided
    prompt_quantity = None
    prompt_price = None
    if original_prompt:
        prompt_lower = original_prompt.lower()
        # Extract quantity from prompt
        qty_match = re.search(r'(\d+)\s*(?:lbs?|pounds?)', prompt_lower)
        if qty_match:
            try:
                prompt_quantity = int(qty_match.group(1))
            except (ValueError, IndexError):
                pass
        # Extract price from prompt
        price_match = re.search(r'\$(\d+\.?\d*)', prompt_lower)
        if price_match:
            try:
                prompt_price = float(price_match.group(1))
            except (ValueError, IndexError):
                pass
    
    # Extract price - look for $X.XX per lb or /lb (improved patterns)
    price_patterns = [
        r'\$(\d+\.?\d*)\s*(?:per\s*lb|/lb|per\s*pound)',  # $0.48/lb or $0.48 per lb
        r'at\s+\$(\d+\.?\d*)',  # at $0.48
        r'price[:\s]*\$?(\d+\.?\d*)',  # price: 0.48 or price $0.48
        r'order.*?\$(\d+\.?\d*)',  # order at $0.48
        r'\$(\d+\.?\d*)',  # Just $0.48 (fallback)
    ]
    price = None
    for pattern in price_patterns:
        price_match = re.search(pattern, response_text, re.IGNORECASE)
        if price_match:
            try:
                price = float(price_match.group(1))
                # Sanity check: price should be reasonable (0.01 to 10.0)
                if 0.01 <= price <= 10.0:
                    break
            except (ValueError, IndexError):
                continue
    
    # If price not found in response, use from prompt
    if price is None and prompt_price:
        price = prompt_price
    
    # Extract quantity - look for X lbs or pounds (improved patterns)
    qty_patterns = [
        r'supply\s+(\d+)\s*(?:lbs?|pounds?)',  # supply 200 lbs
        r'(\d+)\s*(?:lbs?|pounds?)\s*(?:at|for|of)',  # 200 lbs at
        r'quantity[:\s]*(\d+)',  # quantity: 200
        r'order.*?(\d+)\s*(?:lbs?|pounds?)',  # order for 200 lbs
        r'(\d+)\s*(?:lbs?|pounds?)',  # 200 lbs (fallback)
    ]
    quantity = None
    for pattern in qty_patterns:
        qty_match = re.search(pattern, response_text, re.IGNORECASE)
        if qty_match:
            try:
                quantity = int(qty_match.group(1))
                # Sanity check: quantity should be reasonable (1 to 10000)
                if 1 <= quantity <= 10000:
                    break
            except (ValueError, IndexError):
                continue
    
    # If quantity not found in response, use from prompt
    if quantity is None and prompt_quantity:
        quantity = prompt_quantity
    
    # Extract delivery time - look for X days (improved patterns)
    delivery_patterns = [
        r'delivery\s+in\s+(\d+)\s*(?:days?|day)',  # delivery in 5 days
        r'in\s+(\d+)\s*(?:days?|day)\s*(?:delivery|for)',  # in 5 days
        r'delivery[:\s]*(\d+)',  # delivery: 5
        r'(\d+)\s*(?:days?|day)\s*(?:delivery|for)',  # 5 days (fallback)
        r'(\d+)\s*(?:business\s*)?days?',  # 2 business days or 2 days
        r'estimated.*?(\d+)\s*(?:days?|day)',  # estimated 2 days
    ]
    delivery_days = None
    for pattern in delivery_patterns:
        delivery_match = re.search(pattern, response_text, re.IGNORECASE)
        if delivery_match:
            try:
                delivery_days = int(delivery_match.group(1))
                # Sanity check: delivery should be reasonable (1 to 365 days)
                if 1 <= delivery_days <= 365:
                    break
            except (ValueError, IndexError):
                continue
    
    # If delivery not found, use default (5 days) for order confirmations
    if delivery_days is None:
        # Check if this looks like an order confirmation (has Order ID or Tracking Number)
        if "order id" in response_lower or "tracking" in response_lower or "order" in response_lower:
            delivery_days = 5  # Default delivery time for confirmed orders
    
    # Extract quality score (optional, default 1.0)
    quality_patterns = [
        r'quality\s+score[:\s]*(\d+\.?\d*)',  # quality score: 0.95
        r'score[:\s]*(\d+\.?\d*)',  # score: 0.95
        r'quality[:\s]*(\d+\.?\d*)',  # quality: 0.95
    ]
    quality = 1.0  # Default
    for pattern in quality_patterns:
        quality_match = re.search(pattern, response_text, re.IGNORECASE)
        if quality_match:
            try:
                quality = float(quality_match.group(1))
                # Normalize to 0-1 range
                if quality > 1.0:
                    if quality <= 10.0:
                        quality = quality / 10.0
                    else:
                        quality = 1.0
                elif quality < 0.0:
                    quality = 0.0
                break
            except (ValueError, IndexError):
                continue
    
    # If we still don't have all required fields, try to extract from the exact format
    if not all([price, quantity, delivery_days]):
        # Try to match the exact format: "We can supply [qty] lbs at $[price]/lb, delivery in [days] days, quality score [score]."
        exact_format = re.search(
            r'we\s+can\s+supply\s+(\d+)\s+lbs?\s+at\s+\$(\d+\.?\d*)/lb[,\s]+delivery\s+in\s+(\d+)\s+days?[,\s]+quality\s+score\s+(\d+\.?\d*)',
            response_lower
        )
        if exact_format:
            quantity = int(exact_format.group(1))
            price = float(exact_format.group(2))
            delivery_days = int(exact_format.group(3))
            quality = float(exact_format.group(4))
            if quality > 1.0:
                quality = quality / 10.0 if quality <= 10.0 else 1.0
    
    # Final validation with fallbacks
    # If we have an order confirmation but missing some fields, use defaults
    is_order_confirmation = "order id" in response_lower or "tracking" in response_lower
    
    if not quantity:
        if prompt_quantity:
            quantity = prompt_quantity
        else:
            raise ValueError(
                f"Could not parse quantity from {farm_name}. "
                f"Response: {response_text[:200]}"
            )
    
    if not price:
        if prompt_price:
            price = prompt_price
        elif is_order_confirmation:
            # For order confirmations without price, use a default based on market average
            price = 0.50  # Default market price
        else:
            raise ValueError(
                f"Could not parse price from {farm_name}. "
                f"Response: {response_text[:200]}"
            )
    
    if not delivery_days:
        delivery_days = 5  # Default delivery time
    
    return Bid(
        farm_name=farm_name,
        price_per_lb=price,
        quantity_available=quantity,
        delivery_days=delivery_days,
        quality_score=quality,
        message=response_text
    )
