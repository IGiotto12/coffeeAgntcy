# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Market Agent - Manages competitive bidding between farms
"""
import logging
import asyncio
from typing import List, Optional
from uuid import uuid4

from agents.market.models import Bid, RequestForQuote, AuctionResult, BidStatus
from agents.market.protocol import format_rfq_message, parse_bid_from_farm_response
from agents.supervisors.auction.graph.tools import get_farm_card
from agents.supervisors.auction.graph.shared import get_factory
from agntcy_app_sdk.semantic.a2a.protocol import A2AProtocol
from a2a.types import SendMessageRequest, MessageSendParams, Message, Part, TextPart, Role
from config.config import DEFAULT_MESSAGE_TRANSPORT, TRANSPORT_SERVER_ENDPOINT

logger = logging.getLogger("lungo.market.agent")


class MarketAgent:
    """Manages competitive bidding and auction processes"""
    
    def __init__(self):
        self.factory = get_factory()
        self.transport = self.factory.create_transport(
            DEFAULT_MESSAGE_TRANSPORT,
            endpoint=TRANSPORT_SERVER_ENDPOINT,
            name="default/default/market_agent"
        )
    
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
                    logger.info(
                        f"Round {round_num} best bid: {best_bid.farm_name} @ "
                        f"${best_bid.price_per_lb:.2f}/lb"
                    )
            
            # If we have a good bid and it's not the first round, we can stop
            if best_bid and round_num > 1:
                # Check if improvement is significant (e.g., >5% better)
                if round_num == 2 and valid_bids:
                    improvement = (
                        (best_bid.price_per_lb - round_best.price_per_lb) 
                        / best_bid.price_per_lb
                    )
                    if improvement < 0.05:  # Less than 5% improvement
                        logger.info(f"Stopping early: minimal improvement in round {round_num}")
                        break
        
        if not best_bid:
            # Provide helpful error message with details about what happened
            farms_attempted = len([b for b in all_bids if b])
            farms_with_valid_bids = len([b for b in all_bids if b and self._is_valid_bid(b, rfq)])
            error_msg = (
                f"No valid bids received from any farm. "
                f"Attempted {farms_attempted} farms, received {len(all_bids)} responses, "
                f"but none met the RFQ requirements (quantity={rfq.quantity}, "
                f"max_price={rfq.max_price_per_lb}, max_delivery={rfq.max_delivery_days})."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Create auction result
        result = AuctionResult(
            winner=best_bid,
            all_bids=all_bids,
            selection_criteria=self._get_selection_criteria(rfq),
            total_value=best_bid.price_per_lb * quantity
        )
        
        logger.info(
            f"Auction complete. Winner: {result.winner.farm_name} @ "
            f"${result.winner.price_per_lb:.2f}/lb"
        )
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
                    # Pass RFQ message as original_prompt to help extract quantity/price if not in response
                    bid = parse_bid_from_farm_response(farm_name, response_text, original_prompt=rfq_message)
                    return bid
            
            return None
            
        except asyncio.TimeoutError:
            logger.warning(f"Bid request from {farm_name} timed out")
            return None
        except ValueError as ve:
            # Parsing error - log the issue but don't crash the auction
            logger.warning(f"Could not parse bid from {farm_name}: {ve}")
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
        """Select the best bid based on multiple criteria, including performance metrics"""
        if not bids:
            raise ValueError("No bids to evaluate")
        
        # Get performance metrics for farms (if available)
        performance_weights = self._get_performance_weights()
        
        # Scoring function: lower price = better, faster delivery = better, higher quality = better
        scored_bids = []
        for bid in bids:
            # Normalize scores (lower is better for price and delivery, higher is better for quality)
            max_price = max(b.price_per_lb for b in bids)
            max_delivery = max(b.delivery_days for b in bids)
            
            price_score = bid.price_per_lb / max_price if max_price > 0 else 1.0
            delivery_score = bid.delivery_days / max_delivery if max_delivery > 0 else 1.0
            quality_score = 1.0 - bid.quality_score  # Invert so lower is better (for consistency)
            
            # Get performance weight for this farm (higher = better performance = lower penalty)
            farm_key = bid.farm_name.lower()
            perf_weight = performance_weights.get(farm_key, 1.0)  # Default 1.0 if no data
            # Invert: higher performance = lower score (better)
            performance_score = 1.0 / perf_weight if perf_weight > 0 else 1.0
            
            # Weighted combination with performance consideration
            # Performance weight: 15% (reduces from price/delivery/quality)
            total_score = (
                0.40 * price_score +        # 40% weight on price (reduced from 50%)
                0.25 * delivery_score +      # 25% weight on delivery (reduced from 30%)
                0.20 * quality_score +       # 20% weight on quality (same)
                0.15 * performance_score     # 15% weight on performance (NEW!)
            )
            
            scored_bids.append((total_score, bid))
            logger.debug(
                f"Bid scored for {bid.farm_name}: total={total_score:.3f}, "
                f"price={price_score:.3f}, delivery={delivery_score:.3f}, "
                f"quality={quality_score:.3f}, performance={performance_score:.3f} "
                f"(perf_weight={perf_weight:.2f})"
            )
        
        # Return bid with lowest total score
        scored_bids.sort(key=lambda x: x[0])
        best_bid = scored_bids[0][1]
        logger.info(
            f"Selected best bid: {best_bid.farm_name} with score {scored_bids[0][0]:.3f} "
            f"(performance weight: {performance_weights.get(best_bid.farm_name.lower(), 1.0):.2f})"
        )
        return best_bid
    
    def _get_performance_weights(self) -> dict[str, float]:
        """
        Get performance-based weights for farms.
        Higher value = better performance = should be preferred.
        Returns dict mapping farm name to performance score (1.0 = average, >1.0 = better, <1.0 = worse)
        """
        try:
            from services.performance_analyzer import get_performance_analyzer
            analyzer = get_performance_analyzer()
            metrics = analyzer.get_farm_performance(use_realtime=True)
            
            weights = {}
            for farm, metric in metrics.items():
                # Calculate performance score based on:
                # - Success rate (higher = better)
                # - Stability score (higher = better)
                # - Response time (lower = better, so invert)
                # Normalize to 0.5-2.0 range (0.5 = worst, 1.0 = average, 2.0 = best)
                
                success_factor = metric.success_rate  # 0.0 to 1.0
                stability_factor = metric.stability_score  # 0.0 to 1.0
                
                # Response time factor: lower is better
                # Normalize: 0.5s = 2.0, 5.0s = 0.5
                avg_rt = metric.avg_response_time
                if avg_rt > 0:
                    response_factor = max(0.5, min(2.0, 2.5 / (avg_rt + 0.5)))
                else:
                    response_factor = 1.0
                
                # Combined performance score
                perf_score = (success_factor * 0.4 + stability_factor * 0.3 + response_factor * 0.3)
                # Scale to 0.5-2.0 range
                perf_score = 0.5 + (perf_score * 1.5)
                
                weights[farm.lower()] = perf_score
                logger.debug(
                    f"Performance weight for {farm}: {perf_score:.2f} "
                    f"(success={success_factor:.2f}, stability={stability_factor:.2f}, "
                    f"response={response_factor:.2f})"
                )
            
            return weights
        except Exception as e:
            logger.debug(f"Could not get performance weights: {e}. Using default weights.")
            return {}  # Return empty dict, will use default 1.0
    
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
        criteria = [
            "Price (40%)", 
            "Delivery Time (25%)", 
            "Quality (20%)",
            "Performance (15%)"  # NEW: includes response time, success rate, stability
        ]
        if rfq.max_price_per_lb:
            criteria.append(f"Max price: ${rfq.max_price_per_lb:.2f}/lb")
        if rfq.max_delivery_days:
            criteria.append(f"Max delivery: {rfq.max_delivery_days} days")
        return ", ".join(criteria)
