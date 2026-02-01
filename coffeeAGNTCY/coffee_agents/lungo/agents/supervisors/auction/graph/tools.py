# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import logging
import asyncio
import time
from typing import Any, Union, Literal, NoReturn, List
from uuid import uuid4
from pydantic import BaseModel, ValidationError
from dataclasses import dataclass

from a2a.types import (
    AgentCard,
    SendMessageRequest,
    MessageSendParams,
    Message,
    Part,
    TextPart,
    Role,
)
from langchain_core.tools import tool, ToolException
from langchain_core.messages import AnyMessage, ToolMessage
from agntcy_app_sdk.semantic.a2a.protocol import A2AProtocol
from ioa_observe.sdk.decorators import tool as ioa_tool_decorator


from agents.farms.brazil.card import AGENT_CARD as brazil_agent_card
from agents.farms.colombia.card import AGENT_CARD as colombia_agent_card
from agents.farms.vietnam.card import AGENT_CARD as vietnam_agent_card
from agents.supervisors.auction.graph.models import (
    InventoryArgs,
    CreateOrderArgs,
    MarketAuctionArgs,
)
from agents.supervisors.auction.graph.shared import get_factory
from config.config import (
    DEFAULT_MESSAGE_TRANSPORT, 
    TRANSPORT_SERVER_ENDPOINT, 
    FARM_BROADCAST_TOPIC,
    IDENTITY_API_KEY,
    IDENTITY_API_SERVER_URL,
    SCOUT_PROBE_TIMEOUT_SEC,
    SCOUT_INITIAL_TIMEOUT_SEC,
    SCOUT_RETRY_TIMEOUT_SEC,
    SCOUT_MIN_AVAILABLE_FARMS,
    SCOUT_ENABLED,
    DYNAMIC_TIMEOUT_ENABLED,
    PERFORMANCE_ANALYZER_ENABLED,
)
from services.identity_service import IdentityService
from services.identity_service_impl import IdentityServiceImpl


logger = logging.getLogger("lungo.supervisor.tools")

# Global factory and transport instances
factory = get_factory()
transport = factory.create_transport(
    DEFAULT_MESSAGE_TRANSPORT,
    endpoint=TRANSPORT_SERVER_ENDPOINT,
    name="default/default/exchange_graph"
)


class A2AAgentError(ToolException):
    """Custom exception for errors related to A2A agent communication or status."""
    pass


@dataclass
class FarmProbeResult:
    """Result from probing a single farm."""
    farm_name: str
    can_fulfill: bool
    price_or_message: str
    status: Literal["ok", "timeout", "error"]
    error_message: str = ""


@dataclass
class ScoutSummary:
    """Summary of scout probe results from all farms."""
    results: List[FarmProbeResult]
    elapsed_sec: float = 0.0


def tools_or_next(tools_node: str, end_node: str = "__end__"):
  """
  Returns a conditional function for LangGraph to determine the next node 
  based on whether the last message contains tool calls.

  If the message includes tool calls, the workflow proceeds to the `tools_node`.
  If the message is a ToolMessage or has no tool calls, the workflow proceeds to `end_node`.

  Args:
    tools_node (str): The name of the node to route to if tool calls are detected.
    end_node (str, optional): The fallback node if no tool calls are found. Defaults to '__end__'.

  Returns:
    Callable: A function compatible with LangGraph conditional edge handling.
  """

  def custom_tools_condition_fn(
    state: Union[list[AnyMessage], dict[str, Any], BaseModel],
    messages_key: str = "messages",
  ) -> Literal[tools_node, end_node]: # type: ignore

    if isinstance(state, list):
      ai_message = state[-1]
    elif isinstance(state, dict) and (messages := state.get(messages_key, [])):
      ai_message = messages[-1]
    elif messages := getattr(state, messages_key, []):
      ai_message = messages[-1]
    else:
      raise ValueError(f"No messages found in input state to tool_edge: {state}")
    
    if isinstance(ai_message, ToolMessage):
        logger.debug("Last message is a ToolMessage, returning end_node: %s", end_node)
        return end_node

    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
      logger.debug("Last message has tool calls, returning tools_node: %s", tools_node)
      return tools_node
    
    logger.debug("Last message has no tool calls, returning end_node: %s", end_node)
    return end_node

  return custom_tools_condition_fn

def get_farm_card(farm: str) -> AgentCard | None:
    """
    Maps a farm name string to its corresponding AgentCard.

    Args:
        farm (str): The name of the farm (e.g., "Brazil", "Colombia", "Vietnam").

    Returns:
        AgentCard | None: The matching AgentCard if found, otherwise None.
    """
    farm = farm.strip().lower()
    if 'brazil' in farm.lower():
        return brazil_agent_card
    elif 'colombia' in farm.lower():
        return colombia_agent_card
    elif 'vietnam' in farm.lower():
        return vietnam_agent_card
    else:
        logger.error(f"Unknown farm name: {farm}. Expected one of 'brazil', 'colombia', or 'vietnam'.")
        return None

def verify_farm_identity(identity_service: IdentityService, farm_name: str):
    """
    Verifies the identity of a farm by matching the farm name with the app name,
    retrieving the badge, and verifying it.

    Args:
        identity_service (IdentityServiceImpl): The identity service implementation.
        farm_name (str): The name of the farm to verify.

    Raises:
        A2AAgentError: If the app is not found or verification fails.
    """
    try:
        all_apps = identity_service.get_all_apps()
        matched_app = next((app for app in all_apps.apps if app.name.lower() == farm_name.lower()), None)

        if not matched_app:
            err_msg = f"No matching identity app service found, this farm does not have identity service enabled."
            logger.error(err_msg)
            raise A2AAgentError(err_msg)


        badge = identity_service.get_badge_for_app(matched_app.id)
        success = identity_service.verify_badges(badge)

        if success.get("status") is not True:
            raise A2AAgentError(f"Failed to verify badge.")

        logger.info(f"Verification successful for farm '{farm_name}'.")
    except Exception as e:
        raise A2AAgentError(e) # Re-raise as our custom exception

# node utility for streaming
async def get_farm_yield_inventory(prompt: str, farm: str) -> str:
    """
    Fetch yield inventory from a specific farm.

    Args:
        prompt (str): The prompt to send to the farm to retrieve their yields
        farm (str): The farm to send the request to

    Returns:
        str: current yield amount

    Raises:
        A2AAgentError: If there's an issue with farm identification, communication, or the farm agent returns an error.
        ValueError: For invalid input arguments.
    """
    logger.info("entering get_farm_yield_inventory tool with prompt: %s, farm: %s", prompt, farm)
    if not farm:
        raise ValueError("No farm was provided. Please provide a farm to get the yield from.")
    
    card = get_farm_card(farm)
    if card is None:
        raise A2AAgentError(f"Farm '{farm}' not recognized. Available farms "
                             f"are: {brazil_agent_card.name}, {colombia_agent_card.name}, {vietnam_agent_card.name}.")
    
    try:
        client = await factory.create_client(
            "A2A",
            agent_topic=A2AProtocol.create_agent_topic(card),
            transport=transport,
        )

        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(
                message=Message(
                    messageId=str(uuid4()),
                    role=Role.user,
                    parts=[Part(TextPart(text=prompt))],
                ),
            )
        )

        response = await client.send_message(request)
        logger.info(f"Response received from A2A agent: {response}")
        if response.root.result and response.root.result.parts:
            part = response.root.result.parts[0].root
            if hasattr(part, "text"):
                return part.text.strip()
            else:
                raise A2AAgentError(f"Farm '{farm}' returned a result without text content.")
        elif response.root.error:
                logger.error(f"A2A error from farm '{farm}': {response.root.error.message}")
                raise A2AAgentError(f"Error from farm '{farm}': {response.root.error.message}")
        else:
            logger.error(f"Unknown response type from farm '{farm}'.")
            raise A2AAgentError(f"Unknown response type from farm '{farm}'.")
    except Exception as e: # Catch any underlying communication or client creation errors
        logger.error(f"Failed to communicate with farm '{farm}': {e}")
        raise A2AAgentError(f"Failed to communicate with farm '{farm}'. Details: {e}")


async def _probe_single_farm(prompt: str, farm: str, timeout_sec: float) -> FarmProbeResult:
    """
    Probe a single farm with timeout.
    
    Args:
        prompt: The prompt to send to the farm
        farm: Farm name (brazil, colombia, vietnam)
        timeout_sec: Maximum time to wait for response
        
    Returns:
        FarmProbeResult with status and response data
    """
    farm_name = farm.title()
    card = get_farm_card(farm)
    if card is None:
        return FarmProbeResult(
            farm_name=farm_name,
            can_fulfill=False,
            price_or_message="",
            status="error",
            error_message=f"Farm '{farm}' not recognized"
        )
    
    try:
        client = await factory.create_client(
            "A2A",
            agent_topic=A2AProtocol.create_agent_topic(card),
            transport=transport,
        )

        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(
                message=Message(
                    messageId=str(uuid4()),
                    role=Role.user,
                    parts=[Part(TextPart(text=prompt))],
                ),
            )
        )

        # Record start time for performance tracking
        request_start_time = time.time()
        
        # Use asyncio.wait_for to enforce timeout
        try:
            response = await asyncio.wait_for(
                client.send_message(request),
                timeout=timeout_sec
            )
            
            # Calculate response time
            response_time = time.time() - request_start_time
            
            # Record successful request for real-time tracking
            try:
                from services.realtime_performance_tracker import get_realtime_tracker
                tracker = get_realtime_tracker()
                tracker.record_request(farm, response_time, success=True)
                logger.debug(f"Recorded successful request for {farm_name}: {response_time:.2f}s")
            except Exception as e:
                logger.debug(f"Failed to record request: {e}")
            
            logger.info(f"Scout received response from {farm_name} in {response_time:.2f}s: {response}")
            
            if response.root.result and response.root.result.parts:
                part = response.root.result.parts[0].root
                if hasattr(part, "text"):
                    response_text = part.text.strip()
                    # Simple parsing: check if response indicates can fulfill
                    can_fulfill = "yes" in response_text.lower() or "can" in response_text.lower() or "available" in response_text.lower()
                    return FarmProbeResult(
                        farm_name=farm_name,
                        can_fulfill=can_fulfill,
                        price_or_message=response_text,
                        status="ok"
                    )
                else:
                    return FarmProbeResult(
                        farm_name=farm_name,
                        can_fulfill=False,
                        price_or_message="",
                        status="error",
                        error_message="Response without text content"
                    )
            elif response.root.error:
                return FarmProbeResult(
                    farm_name=farm_name,
                    can_fulfill=False,
                    price_or_message="",
                    status="error",
                    error_message=response.root.error.message or "A2A error"
                )
            else:
                return FarmProbeResult(
                    farm_name=farm_name,
                    can_fulfill=False,
                    price_or_message="",
                    status="error",
                    error_message="Unknown response type"
                )
        except asyncio.TimeoutError:
            # Record timeout for real-time tracking
            response_time = time.time() - request_start_time
            try:
                from services.realtime_performance_tracker import get_realtime_tracker
                tracker = get_realtime_tracker()
                tracker.record_request(farm, response_time, success=False)
                logger.debug(f"Recorded timeout for {farm_name}: {response_time:.2f}s")
            except Exception as e:
                logger.debug(f"Failed to record timeout: {e}")
            
            logger.warning(f"Scout probe for {farm_name} timed out after {timeout_sec}s - farm did not respond in time")
            return FarmProbeResult(
                farm_name=farm_name,
                can_fulfill=False,
                price_or_message="",
                status="timeout",
                error_message=f"Timeout: Farm did not respond within {timeout_sec} seconds"
            )
    except ValidationError as ve:
        # Pydantic validation error - often indicates authorization failure
        # The A2A client receives "unauthorized" string but tries to parse as SendMessageResponse
        error_str = str(ve)
        
        # Check if this is an authorization error by looking at the validation error details
        # Authorization errors often show "input_value='unauthorized'" in the error
        if "unauthorized" in error_str.lower() or any("unauthorized" in str(err).lower() for err in ve.errors()):
            error_type = "Authorization Error"
            logger.error(f"Scout probe authorization error for {farm_name} (Pydantic validation): {ve}")
            return FarmProbeResult(
                farm_name=farm_name,
                can_fulfill=False,
                price_or_message="",
                status="error",
                error_message=f"{error_type}: Authorization failed - missing TBAC policy for '{farm_name}' farm. Check Identity Service policies."
            )
        else:
            # Other validation errors
            error_type = "Validation Error"
            logger.error(f"Scout probe validation error for {farm_name}: {ve}")
            return FarmProbeResult(
                farm_name=farm_name,
                can_fulfill=False,
                price_or_message="",
                status="error",
                error_message=f"{error_type}: Invalid response format from farm"
            )
    except Exception as e:
        # Record exception for real-time tracking
        response_time = time.time() - request_start_time if 'request_start_time' in locals() else timeout_sec
        try:
            from services.realtime_performance_tracker import get_realtime_tracker
            tracker = get_realtime_tracker()
            tracker.record_request(farm, response_time, success=False)
            logger.debug(f"Recorded exception for {farm_name}: {response_time:.2f}s")
        except Exception as track_err:
            logger.debug(f"Failed to record exception: {track_err}")
        
        error_str = str(e)
        error_type = "Unknown"
        
        # Categorize the error type for better debugging
        # Check for authorization/authentication errors first (most specific)
        if "unauthorized" in error_str.lower() or "authorization" in error_str.lower() or "authentication" in error_str.lower():
            error_type = "Authorization Error"
            logger.error(f"Scout probe authorization error for {farm_name}: {e}")
            # Provide helpful message for authorization errors
            return FarmProbeResult(
                farm_name=farm_name,
                can_fulfill=False,
                price_or_message="",
                status="error",
                error_message=f"{error_type}: Authorization failed - missing TBAC policy for '{farm_name}' farm. Check Identity Service policies."
            )
        elif "connection" in error_str.lower() or "connect" in error_str.lower():
            error_type = "Connection Error"
            logger.error(f"Scout probe connection error for {farm_name}: {e}")
        elif "refused" in error_str.lower():
            error_type = "Connection Refused"
            logger.error(f"Scout probe connection refused for {farm_name}: {e}")
        elif "timeout" in error_str.lower():
            error_type = "Timeout"
            logger.error(f"Scout probe timeout for {farm_name}: {e}")
        elif "not found" in error_str.lower() or "unknown" in error_str.lower():
            error_type = "Not Found"
            logger.error(f"Scout probe not found for {farm_name}: {e}")
        else:
            error_type = "Access Error"
            logger.error(f"Scout probe access error for {farm_name}: {e}")
        
        return FarmProbeResult(
            farm_name=farm_name,
            can_fulfill=False,
            price_or_message="",
            status="error",
            error_message=f"{error_type}: {error_str[:200]}"  # Truncate long error messages
        )


async def scout_probe_farms(prompt: str, timeout_sec: float = SCOUT_PROBE_TIMEOUT_SEC) -> ScoutSummary:
    """
    Probe all farms (Brazil, Colombia, Vietnam) in parallel with timeout.
    Uses dynamic timeouts based on historical performance if enabled.
    
    Args:
        prompt: The prompt to send to all farms
        timeout_sec: Base timeout (used if dynamic timeout is disabled or unavailable)
        
    Returns:
        ScoutSummary with results from all farms
    """
    import time
    start_time = time.time()
    
    # Get dynamic timeouts from Performance Analyzer if enabled
    farm_timeouts = {}
    if DYNAMIC_TIMEOUT_ENABLED and PERFORMANCE_ANALYZER_ENABLED:
        try:
            from services.performance_analyzer import get_performance_analyzer
            analyzer = get_performance_analyzer()
            performance_data = analyzer.get_farm_performance()
            
            for farm in ['brazil', 'colombia', 'vietnam']:
                if farm in performance_data:
                    recommended = performance_data[farm].recommended_timeout
                    farm_timeouts[farm] = recommended
                    logger.info(f"Scout: Using dynamic timeout for {farm}: {recommended}s (from performance analyzer)")
                else:
                    farm_timeouts[farm] = timeout_sec
                    logger.debug(f"Scout: No performance data for {farm}, using base timeout: {timeout_sec}s")
        except Exception as e:
            logger.warning(f"Scout: Failed to get dynamic timeouts from Performance Analyzer: {e}. Using base timeout.")
            # Fall back to base timeout for all farms
            for farm in ['brazil', 'colombia', 'vietnam']:
                farm_timeouts[farm] = timeout_sec
    else:
        # Use same timeout for all farms
        for farm in ['brazil', 'colombia', 'vietnam']:
            farm_timeouts[farm] = timeout_sec
    
    # Log timeout configuration
    timeout_summary = ", ".join([f"{farm}: {farm_timeouts[farm]}s" for farm in ['brazil', 'colombia', 'vietnam']])
    logger.info(f"Scout: Starting parallel probe of all farms with timeouts: {timeout_summary}")
    logger.info(f"Scout: Prompt: {prompt}")
    logger.info(f"Scout: Transport: {DEFAULT_MESSAGE_TRANSPORT}, Endpoint: {TRANSPORT_SERVER_ENDPOINT}")
    
    # Probe all farms in parallel with individual timeouts
    tasks = [
        _probe_single_farm(prompt, "brazil", farm_timeouts["brazil"]),
        _probe_single_farm(prompt, "colombia", farm_timeouts["colombia"]),
        _probe_single_farm(prompt, "vietnam", farm_timeouts["vietnam"]),
    ]
    
    # Use return_exceptions=True to handle individual farm failures gracefully
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Convert exceptions to error results
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            farm_name = ["Brazil", "Colombia", "Vietnam"][i]
            error_str = str(result)
            error_type = "Exception"
            
            # Categorize exception type
            if isinstance(result, asyncio.TimeoutError):
                error_type = "Timeout Exception"
            elif "connection" in error_str.lower():
                error_type = "Connection Exception"
            else:
                error_type = "Access Exception"
            
            logger.error(f"Scout: {error_type} for {farm_name}: {result}")
            processed_results.append(FarmProbeResult(
                farm_name=farm_name,
                can_fulfill=False,
                price_or_message="",
                status="error",
                error_message=f"{error_type}: {error_str[:200]}"  # Truncate long error messages
            ))
        else:
            processed_results.append(result)
    
    elapsed = time.time() - start_time
    
    # Log summary of results
    success_count = sum(1 for r in processed_results if r.status == "ok")
    timeout_count = sum(1 for r in processed_results if r.status == "timeout")
    error_count = sum(1 for r in processed_results if r.status == "error")
    
    logger.info(f"Scout: Probe completed in {elapsed:.2f}s - Success: {success_count}, Timeout: {timeout_count}, Error: {error_count}")
    
    return ScoutSummary(
        results=processed_results,
        elapsed_sec=elapsed
    )

# node utility for streaming
async def get_all_farms_yield_inventory(prompt: str) -> str:
    """
    Broadcasts a prompt to all farms and aggregates their inventory responses.

    Args:
        prompt (str): The prompt to broadcast to all farm agents.

    Returns:
        str: A summary string containing yield information from all farms.
    """
    logger.info("entering get_all_farms_yield_inventory tool with prompt: %s", prompt)

    request = SendMessageRequest(
        id=str(uuid4()),
        params=MessageSendParams(
            message=Message(
                messageId=str(uuid4()),
                role=Role.user,
                parts=[Part(TextPart(text=prompt))],
            ),
        )
    )

    if DEFAULT_MESSAGE_TRANSPORT == "SLIM":
        client_handshake_topic = A2AProtocol.create_agent_topic(get_farm_card("brazil"))
    else:
        # using NATS 
        client_handshake_topic = FARM_BROADCAST_TOPIC

    try:
        # create an A2A client, retrieving an A2A card from agent_topic
        client = await factory.create_client(
            "A2A",
            agent_topic=client_handshake_topic,
            transport=transport,
        )

        # create a list of recipients to include in the broadcast
        recipients = [A2AProtocol.create_agent_topic(get_farm_card(farm)) for farm in ['brazil', 'colombia', 'vietnam']]
        # create a broadcast message and collect responses
        responses = await client.broadcast_message(request, broadcast_topic=FARM_BROADCAST_TOPIC, recipients=recipients)

        logger.info(f"got {len(responses)} responses back from farms")

        farm_yields = ""
        for response in responses:
            # we want a dict for farm name -> yield, the farm_name will be in the response metadata
            if response.root.result and response.root.result.parts:
                part = response.root.result.parts[0].root
                if hasattr(response.root.result, "metadata"):
                    farm_name = response.root.result.metadata.get("name", "Unknown Farm")
                else:
                    farm_name = "Unknown Farm"

                farm_yields += f"{farm_name} : {part.text.strip()}\n"
            elif response.root.error:
                err_msg = f"A2A error from farm: {response.root.error.message}"
                logger.error(err_msg)
                raise A2AAgentError(err_msg)
            else:
                err_msg = f"Unknown response type from farm"
                logger.error(err_msg)
                raise A2AAgentError(err_msg)

        logger.info(f"Farm yields: {farm_yields}")
        return farm_yields.strip()
    except Exception as e: # Catch any underlying communication or client creation errors
        logger.error(f"Failed to communicate with all farms during broadcast: {e}")
        raise A2AAgentError(f"Failed to communicate with all farms. Details: {e}")

# node utility for streaming
async def get_all_farms_yield_inventory_streaming(prompt: str):
    """
    Broadcasts a prompt to all farms and streams their inventory responses as they arrive.

    Args:
        prompt (str): The prompt to broadcast to all farm agents.

    Yields:
        str: Yield information from each farm as it becomes available.
    """
    logger.info("entering get_all_farms_yield_inventory_streaming tool with prompt: %s", prompt)

    request = SendMessageRequest(
        id=str(uuid4()),
        params=MessageSendParams(
            message=Message(
                messageId=str(uuid4()),
                role=Role.user,
                parts=[Part(TextPart(text=prompt))],
            ),
        )
    )

    if DEFAULT_MESSAGE_TRANSPORT == "SLIM":
        client_handshake_topic = A2AProtocol.create_agent_topic(get_farm_card("brazil"))
    else:
        # using NATS
        client_handshake_topic = FARM_BROADCAST_TOPIC

    try:
        # create an A2A client, retrieving an A2A card from agent_topic
        client = await factory.create_client(
            "A2A",
            agent_topic=client_handshake_topic,
            transport=transport,
        )

        # create a list of recipients to include in the broadcast
        farm_names = ['brazil', 'colombia', 'vietnam']
        recipients = [A2AProtocol.create_agent_topic(get_farm_card(farm)) for farm in farm_names]
        logger.info(f"Broadcasting to {len(recipients)} farms: {', '.join(farm_names)}")

        # Get the async generator for streaming responses
        response_stream = client.broadcast_message_streaming(
            request,
            broadcast_topic=FARM_BROADCAST_TOPIC,
            recipients=recipients
        )

        # Track which farms responded
        responded_farms = set()
        errors = []
        
        # Process responses as they arrive
        async for response in response_stream:
            try:
                if response.root.result and response.root.result.parts:
                    part = response.root.result.parts[0].root
                    farm_name = "Unknown Farm"
                    if hasattr(response.root.result, "metadata"):
                        farm_name = response.root.result.metadata.get("name", "Unknown Farm")

                    if farm_name == "None":
                        # received error from farm agent
                        errors.append(part.text.strip())
                    else:
                        responded_farms.add(farm_name)
                        logger.info(f"Received response from {farm_name} ({len(responded_farms)}/{len(recipients)})")
                        yield f"{farm_name} : {part.text.strip()}\n"
                elif response.root.error:
                    err_msg = f"A2A error from farm: {response.root.error.message}"
                    logger.error(err_msg)
                    yield f"Error from farm: {response.root.error.message}\n"
                else:
                    err_msg = "Unknown response type from farm"
                    logger.error(err_msg)
                    yield f"Error: Unknown response format from farm\n"
            except Exception as e:
                logger.error(f"Error processing farm response: {e}")
                yield f"Error processing farm response: {str(e)}\n"
        
        # Check for missing responses and report them
        if len(responded_farms) < len(recipients):
            # Determine which farms didn't respond by checking farm names
            expected_farms = {"Brazil Coffee Farm", "Colombia Coffee Farm", "Vietnam Coffee Farm"}
            missing_farms = expected_farms - responded_farms
            
            if missing_farms:
                missing_list = ", ".join(sorted(missing_farms))
                logger.warning(f"Broadcast completed with partial responses: {len(responded_farms)}/{len(recipients)} farms responded. Missing: {missing_list}")

                response = f"No response from {missing_list}. These farms may be unavailable or slow to respond."
                if len(errors) != 0:
                    readable_errors = "\n".join(errors)
                    response += f" Errors encountered from farms:\n{readable_errors}\n"

                yield response


    except Exception as e:
        error_msg = f"Failed to communicate with farms during broadcast: {e}"
        logger.error(error_msg)
        # Check if it's a timeout-related error
        if "timeout" in str(e).lower():
            yield f"Error: Broadcast timed out. Some farms may be slow to respond or unavailable. {str(e)}\n"
        else:
            yield f"Error: {error_msg}\n"

@tool(args_schema=CreateOrderArgs)
@ioa_tool_decorator(name="create_order")
async def create_order(farm: str, quantity: int, price: float) -> str:
    """
    Sends a request to create a coffee order with a specific farm.

    Args:
        farm (str): The target farm for the order.
        quantity (int): Quantity of coffee to order.
        price (float): Proposed price per unit.

    Returns:
        str: Confirmation message or error string from the farm agent.

    Raises:
        A2AAgentError: If there's an issue with farm identification, identity verification, communication, or the farm agent returns an error.
        ValueError: For invalid input arguments.
    """

    farm = farm.strip().lower()

    logger.info(f"Creating order with price: {price}, quantity: {quantity}")
    if price <= 0 or quantity <= 0:
        raise ValueError("Price and quantity must be greater than zero.")
    
    if not farm:
        raise ValueError("No farm was provided, please provide a farm to create an order.")
    
    card = get_farm_card(farm)
    if card is None:
        raise ValueError(f"Farm '{farm}' not recognized. Available farms are: {brazil_agent_card.name}, {colombia_agent_card.name}, {vietnam_agent_card.name}.")

    logger.info(f"Using farm card: {card.name} for order creation")
    identity_service = IdentityServiceImpl(api_key=IDENTITY_API_KEY, base_url=IDENTITY_API_SERVER_URL)
    try:
        verify_farm_identity(identity_service, card.name)
    except Exception as e:
        # log the error and re-raise the exception
        raise A2AAgentError(f"Identity verification failed for farm '{farm}'. Details: {e}")

    try:
        client = await factory.create_client(
            "A2A",
            agent_topic=A2AProtocol.create_agent_topic(card),
            transport=transport,
        )

        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(
                message=Message(
                    messageId=str(uuid4()),
                    role=Role.user,
                    parts=[Part(TextPart(text=f"Create an order with price {price} and quantity {quantity}"))],
                ),
            )
        )

        response = await client.send_message(request)
        logger.info(f"Response received from A2A agent: {response}")

        if response.root.result and response.root.result.parts:
            part = response.root.result.parts[0].root
            if hasattr(part, "text"):
                return part.text.strip()
            else:
                raise A2AAgentError(f"Farm '{farm}' returned a result without text content for order creation.")
        elif response.root.error:
            logger.error(f"A2A error: {response.root.error.message}")
            raise A2AAgentError(f"Error from order agent for farm '{farm}': {response.root.error.message}")
        else:
            logger.error("Unknown response type")
            raise A2AAgentError("Unknown response type from order agent")
    except Exception as e: # Catch any underlying communication or client creation errors
        logger.error(f"Failed to communicate with order agent for farm '{farm}': {e}")
        raise A2AAgentError(f"Failed to communicate with order agent for farm '{farm}'. Details: {e}")

def check_result_quality(summary: ScoutSummary) -> tuple[bool, int]:
    """
    Check if the scout result is usable (has at least minimum required available farms).
    
    Args:
        summary: ScoutSummary with results from all farms
        
    Returns:
        tuple: (is_usable, available_count) - True if at least SCOUT_MIN_AVAILABLE_FARMS farms responded successfully
    """
    available_count = sum(1 for result in summary.results if result.status == "ok")
    is_usable = available_count >= SCOUT_MIN_AVAILABLE_FARMS
    return (is_usable, available_count)

@tool
@ioa_tool_decorator(name="scout_then_decide")
async def scout_then_decide(prompt: str, prefer_farm: str | None = None, timeout_sec: float | None = None) -> str:
    """
    Scout probes all farms in parallel with timeout, then returns a summary for decision-making.
    This tool optimizes response time by not waiting for slow farms.
    
    Args:
        prompt: The prompt/question to send to all farms (e.g., "Can you supply 50 lb at $0.50/lb?")
        prefer_farm: Optional preferred farm name (e.g., "colombia") if user specified one
        timeout_sec: Optional timeout in seconds. If not provided, uses SCOUT_PROBE_TIMEOUT_SEC.
                    Use smaller timeout (e.g., 2.0) for fast initial response, larger (e.g., 5.0) for retry.
        
    Returns:
        str: A formatted summary string with results from all farms, suitable for LLM decision-making.
             Includes quality indicator: "QUALITY: USABLE" if at least 2 farms responded, "QUALITY: NEEDS_RETRY" otherwise.
    """
    if not SCOUT_ENABLED:
        logger.warning("Scout is disabled, falling back to regular broadcast")
        return await get_all_farms_yield_inventory(prompt)
    
    # Use provided timeout or default
    actual_timeout = timeout_sec if timeout_sec is not None else SCOUT_PROBE_TIMEOUT_SEC
    
    logger.info(f"Scout probing farms with prompt: {prompt}, prefer_farm: {prefer_farm}, timeout: {actual_timeout}s")
    
    try:
        summary = await scout_probe_farms(prompt, actual_timeout)
        
        # Check result quality
        is_usable, available_count = check_result_quality(summary)
        
        # Format summary for LLM with clear error categorization
        summary_lines = []
        for result in summary.results:
            if result.status == "ok":
                status_str = "✓ Available"
                summary_lines.append(f"{result.farm_name}: {status_str} - {result.price_or_message}")
            elif result.status == "timeout":
                summary_lines.append(f"{result.farm_name}: ⏱ TIMEOUT - No response within {actual_timeout}s (farm may be slow or overloaded)")
            else:
                # Categorize error type for better debugging
                error_msg = result.error_message or "Communication failed"
                if "Authorization" in error_msg or "unauthorized" in error_msg.lower() or "TBAC policy" in error_msg:
                    # Show clear message about missing policies
                    if "TBAC policy" in error_msg:
                        summary_lines.append(f"{result.farm_name}: 🔒 AUTHORIZATION ERROR - {error_msg}")
                    else:
                        summary_lines.append(f"{result.farm_name}: 🔒 AUTHORIZATION ERROR - {error_msg} (check Identity Service TBAC policies)")
                elif "Timeout" in error_msg or "timeout" in error_msg.lower():
                    summary_lines.append(f"{result.farm_name}: ⏱ TIMEOUT - {error_msg}")
                elif "Connection" in error_msg or "Refused" in error_msg or "connection" in error_msg.lower():
                    summary_lines.append(f"{result.farm_name}: ✗ CONNECTION ERROR - {error_msg} (check NATS/farm service)")
                elif "Not Found" in error_msg or "not found" in error_msg.lower():
                    summary_lines.append(f"{result.farm_name}: ✗ NOT FOUND - {error_msg} (check farm registration)")
                else:
                    summary_lines.append(f"{result.farm_name}: ✗ ACCESS ERROR - {error_msg}")
        
        formatted_summary = "\n".join(summary_lines)
        
        if prefer_farm:
            formatted_summary += f"\n\nNote: User preferred {prefer_farm.title()} farm. Use it if status is 'ok', otherwise suggest alternatives."
        
        # Add quality indicator for UI to decide if retry is needed
        quality_status = "USABLE" if is_usable else "NEEDS_RETRY"
        formatted_summary += f"\n\nQUALITY: {quality_status} (Available: {available_count}/{len(summary.results)}, Required: {SCOUT_MIN_AVAILABLE_FARMS}, Timeout: {actual_timeout}s)"
        
        logger.info(f"Scout summary: {formatted_summary} (Quality: {quality_status}, Available: {available_count})")
        return formatted_summary
        
    except Exception as e:
        logger.error(f"Scout probe failed: {e}")
        raise A2AAgentError(f"Scout probe failed: {str(e)}")


@tool
@ioa_tool_decorator(name="scout_then_market_analyze_then_decide")
async def scout_then_market_analyze_then_decide(
    prompt: str, 
    prefer_farm: str | None = None, 
    timeout_sec: float | None = None
) -> str:
    """
    Scout probes all farms, then Market Agent analyzes the responses with scoring criteria,
    and returns a comprehensive summary with market analysis for decision-making.
    
    This tool automatically retries with increasing timeouts (2s -> 5s -> 10s -> 15s...) 
    until a USABLE result is obtained (at least 2 farms respond).
    
    Args:
        prompt: The prompt/question to send to all farms (e.g., "I want to order 75 lbs at $0.48/lb. Check all farms and pick the best one.")
        prefer_farm: Optional preferred farm name (e.g., "colombia") if user specified one
        timeout_sec: Optional initial timeout in seconds. If not provided, starts with SCOUT_INITIAL_TIMEOUT_SEC (2s).
        
    Returns:
        str: A formatted summary with:
        1. Scout results from all farms
        2. Market Agent analysis with scoring breakdown (only if USABLE)
        3. Recommended farm based on market criteria (only if USABLE)
    """
    if not SCOUT_ENABLED:
        logger.warning("Scout is disabled, falling back to regular broadcast")
        return await get_all_farms_yield_inventory(prompt)
    
    # Start with initial timeout (2s) or provided timeout
    current_timeout = timeout_sec if timeout_sec is not None else SCOUT_INITIAL_TIMEOUT_SEC
    max_retries = 5  # Limit retries to prevent infinite loops
    retry_count = 0
    
    logger.info(f"Scout+Market: Probing farms with prompt: {prompt}, initial timeout: {current_timeout}s")
    
    try:
        # Auto-retry loop: keep trying with increasing timeout until we get USABLE result
        while retry_count < max_retries:
            # Step 1: Scout probes all farms
            summary = await scout_probe_farms(prompt, current_timeout)
            is_usable, available_count = check_result_quality(summary)
            
            # If result is USABLE, break and proceed to Market Agent analysis
            if is_usable:
                logger.info(f"Scout+Market: Got USABLE result on attempt {retry_count + 1} with timeout {current_timeout}s ({available_count} farms responded)")
                break
            
            # If not USABLE, increase timeout and retry
            retry_count += 1
            if retry_count < max_retries:
                # Increase timeout: 2s -> 5s -> 10s -> 15s -> 20s
                if current_timeout == SCOUT_INITIAL_TIMEOUT_SEC:
                    current_timeout = SCOUT_RETRY_TIMEOUT_SEC  # 2s -> 5s
                else:
                    current_timeout += 5.0  # Add 5 seconds each time: 5s -> 10s -> 15s -> 20s
                logger.info(f"Scout+Market: Result not USABLE ({available_count} farms responded, need {SCOUT_MIN_AVAILABLE_FARMS}). Retrying with timeout {current_timeout}s (attempt {retry_count + 1}/{max_retries})")
            else:
                logger.warning(f"Scout+Market: Max retries reached. Result still not USABLE ({available_count} farms responded)")
        
        # Now proceed with Market Agent analysis (only if USABLE)
        
        # Step 2: Extract bids from successful responses for Market Agent analysis
        from agents.market.models import Bid, RequestForQuote
        from agents.market.protocol import parse_bid_from_farm_response
        from agents.market.agent import MarketAgent
        from services.performance_analyzer import get_performance_analyzer
        import re
        
        successful_bids: list[Bid] = []
        scout_summary_lines = []
        
        # Extract quantity from prompt if possible
        qty_match = re.search(r'(\d+)\s*(?:lbs?|pounds?)', prompt, re.IGNORECASE)
        quantity_needed = int(qty_match.group(1)) if qty_match else 100  # Default to 100 if not found
        
        # Extract price from prompt if possible
        price_match = re.search(r'\$(\d+\.?\d*)', prompt, re.IGNORECASE)
        max_price = float(price_match.group(1)) if price_match else None
        
        for result in summary.results:
            if result.status == "ok":
                try:
                    # Try to parse bid from farm response, passing original prompt for fallback extraction
                    bid = parse_bid_from_farm_response(result.farm_name, result.price_or_message, original_prompt=prompt)
                    successful_bids.append(bid)
                    scout_summary_lines.append(f"{result.farm_name}: ✓ Available - {result.price_or_message}")
                except (ValueError, Exception) as e:
                    # If parsing fails, still show the response but mark as unparseable
                    logger.warning(f"Could not parse bid from {result.farm_name}: {e}")
                    scout_summary_lines.append(f"{result.farm_name}: ✓ Available - {result.price_or_message} (Could not extract bid details for market analysis)")
            elif result.status == "timeout":
                scout_summary_lines.append(f"{result.farm_name}: ⏱ TIMEOUT - No response within {current_timeout}s (farm may be slow or overloaded)")
            else:
                error_msg = result.error_message or "Communication failed"
                if "Authorization" in error_msg or "unauthorized" in error_msg.lower():
                    scout_summary_lines.append(f"{result.farm_name}: 🔒 AUTHORIZATION ERROR - {error_msg}")
                elif "Timeout" in error_msg or "timeout" in error_msg.lower():
                    scout_summary_lines.append(f"{result.farm_name}: ⏱ TIMEOUT - {error_msg}")
                elif "Connection" in error_msg or "Refused" in error_msg:
                    scout_summary_lines.append(f"{result.farm_name}: ✗ CONNECTION ERROR - {error_msg}")
                else:
                    scout_summary_lines.append(f"{result.farm_name}: ✗ ACCESS ERROR - {error_msg}")
        
        # Step 3: Market Agent analysis ONLY if result is USABLE
        market_analysis = ""
        recommended_farm = None
        
        # Only perform Market Agent analysis if we have USABLE results (at least 2 farms responded)
        if is_usable and successful_bids:
            try:
                market_agent = MarketAgent()
                
                # Create RFQ from prompt
                rfq = RequestForQuote(
                    rfq_id="scout-market-analysis",
                    quantity=quantity_needed,
                    max_price_per_lb=max_price,
                    max_delivery_days=None,
                    quality_requirement=None
                )
                
                # Analyze bids using Market Agent's scoring logic
                valid_bids = [b for b in successful_bids if market_agent._is_valid_bid(b, rfq)]
                
                if valid_bids:
                    # Get performance metrics for scoring
                    performance_analyzer = get_performance_analyzer()
                    performance_metrics = performance_analyzer.get_farm_performance(use_realtime=True)
                    
                    # Calculate performance weights (inverse of response time, normalized)
                    performance_weights = {}
                    for farm_key in ["brazil", "colombia", "vietnam"]:
                        perf = performance_metrics.get(farm_key)
                        if perf and perf.avg_response_time:
                            # Lower response time = better performance = higher weight
                            # Use inverse: 1 / (response_time + 0.1) to avoid division by zero
                            performance_weights[farm_key] = 1.0 / (perf.avg_response_time + 0.1)
                        else:
                            performance_weights[farm_key] = 1.0  # Default
                    
                    # Score bids using Market Agent logic
                    scored_bids = []
                    for bid in valid_bids:
                        max_price_bid = max(b.price_per_lb for b in valid_bids)
                        max_delivery_bid = max(b.delivery_days for b in valid_bids)
                        
                        price_score = bid.price_per_lb / max_price_bid if max_price_bid > 0 else 1.0
                        delivery_score = bid.delivery_days / max_delivery_bid if max_delivery_bid > 0 else 1.0
                        quality_score = 1.0 - bid.quality_score
                        
                        farm_key = bid.farm_name.lower()
                        perf_weight = performance_weights.get(farm_key, 1.0)
                        performance_score = 1.0 / perf_weight if perf_weight > 0 else 1.0
                        
                        total_score = (
                            0.40 * price_score +
                            0.25 * delivery_score +
                            0.20 * quality_score +
                            0.15 * performance_score
                        )
                        
                        scored_bids.append((total_score, bid))
                    
                    # Sort by score (lower is better)
                    scored_bids.sort(key=lambda x: x[0])
                    best_score, best_bid = scored_bids[0]
                    recommended_farm = best_bid.farm_name
                    
                    # Format market analysis
                    market_analysis_lines = [
                        "\n--- 📊 Market Agent Analysis ---",
                        f"Analyzed {len(valid_bids)} valid bid(s) from {len(successful_bids)} available farm(s).",
                        "",
                        "**Scoring Criteria:**",
                        "- Price: 40% weight (lower is better)",
                        "- Delivery Time: 25% weight (faster is better)",
                        "- Quality Score: 20% weight (higher is better)",
                        "- Performance Metrics: 15% weight (response time, success rate, stability)",
                        "",
                        "**Bid Analysis:**"
                    ]
                    
                    for score, bid in scored_bids:
                        farm_key = bid.farm_name.lower()
                        perf = performance_metrics.get(farm_key)
                        perf_info = ""
                        if perf:
                            perf_info = f" | Performance: {perf.avg_response_time:.2f}s avg, {perf.success_rate*100:.0f}% success"
                        
                        is_best = "🏆" if bid.farm_name == recommended_farm else "  "
                        market_analysis_lines.append(
                            f"{is_best} **{bid.farm_name}**: Score {score:.3f} | "
                            f"${bid.price_per_lb:.2f}/lb | {bid.delivery_days} days | "
                            f"Quality {bid.quality_score:.2f}{perf_info}"
                        )
                    
                    market_analysis_lines.append("")
                    market_analysis_lines.append(f"**Recommended:** {recommended_farm} (lowest total score)")
                    market_analysis = "\n".join(market_analysis_lines)
                    
            except Exception as e:
                logger.warning(f"Market Agent analysis failed: {e}")
                market_analysis = "\n--- 📊 Market Agent Analysis ---\nMarket analysis unavailable. Using Scout results only.\n"
        elif not is_usable:
            # Result is not usable - don't show Market Agent analysis, just prompt for retry
            market_analysis = ""
            logger.info(f"Result quality is NEEDS_RETRY ({available_count}/{len(summary.results)} farms). Skipping Market Agent analysis. User should retry with longer timeout.")
        
        # Step 4: Combine Scout and Market analysis
        scout_summary = "\n".join(scout_summary_lines)
        
        if prefer_farm:
            scout_summary += f"\n\nNote: User preferred {prefer_farm.title()} farm."
        
        # Only return result if USABLE (after all retries)
        if is_usable:
            quality_indicator = f"\n\n**QUALITY:** USABLE (Available: {available_count}/{len(summary.results)}, Required: {SCOUT_MIN_AVAILABLE_FARMS}, Final Timeout: {current_timeout}s, Attempts: {retry_count + 1})"
            
            # Final combined summary
            final_summary = f"{scout_summary}{market_analysis}{quality_indicator}"
            
            # Add Market Agent recommendation if available
            if recommended_farm:
                final_summary += f"\n\n**Market Agent Recommendation:** Based on competitive analysis, {recommended_farm} offers the best overall value considering price, delivery time, quality, and performance metrics."
            
            logger.info(f"Scout+Market summary completed. Recommended: {recommended_farm}, Quality: USABLE")
            return final_summary
        else:
            # Even after all retries, result is not USABLE - return what we have but indicate it's not sufficient
            quality_indicator = f"\n\n**QUALITY:** NOT USABLE (Available: {available_count}/{len(summary.results)}, Required: {SCOUT_MIN_AVAILABLE_FARMS}, Final Timeout: {current_timeout}s, Attempts: {retry_count + 1})"
            final_summary = f"{scout_summary}{quality_indicator}\n\n⚠️ **Insufficient Responses:** Could not get enough farm responses even after {retry_count + 1} attempts with increasing timeouts. Please check farm services."
            logger.warning(f"Scout+Market: Could not get USABLE result after {retry_count + 1} attempts")
            return final_summary
        
    except Exception as e:
        logger.error(f"Scout+Market analysis failed: {e}")
        # Return a summary indicating all farms failed, but format it so it's not treated as a complete failure
        # This allows the LLM to still process the information
        error_summary = (
            f"Brazil: ✗ Communication issue - {str(e)[:100]}\n"
            f"Colombia: ✗ Communication issue - {str(e)[:100]}\n"
            f"Vietnam: ✗ Communication issue - {str(e)[:100]}\n\n"
            f"Note: All farms encountered communication issues. This may indicate a transport or network problem."
        )
        return error_summary


@tool
@ioa_tool_decorator(name="get_order_details")
async def get_order_details(order_id: str) -> str:
    """
    Get details of an order.

    Args:
    order_id (str): The ID of the order.

    Returns:
    str: Details of the order.

    Raises:
    A2AAgentError: If there's an issue with communication or the order agent returns an error.
    ValueError: For invalid input arguments.
    """
    logger.info(f"Getting details for order ID: {order_id}")
    if not order_id:
        raise ValueError("Order ID must be provided.")

    try:
        client = await factory.create_client(
            "A2A",
            agent_topic=FARM_BROADCAST_TOPIC,
            transport=transport,
        )

        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(
                message=Message(
                    messageId=str(uuid4()),
                    role=Role.user,
                    parts=[Part(TextPart(text=f"Get details for order ID {order_id}"))],
                ),
            )
        )

        response = await client.send_message(request)
        logger.info(f"Response received from A2A agent: {response}")

        if response.root.result and response.root.result.parts:
            part = response.root.result.parts[0].root
            if hasattr(part, "text"):
                return part.text.strip()
            else:
                raise A2AAgentError(f"Order agent returned a result without text content for order ID '{order_id}'.")
        elif response.root.error:
            logger.error(f"A2A error from order agent for order ID '{order_id}': {response.root.error.message}")
            raise A2AAgentError(f"Error from order agent for order ID '{order_id}': {response.root.error.message}")
        else:
            logger.error(f"Unknown response type from order agent for order ID '{order_id}'.")
            raise A2AAgentError(f"Unknown response type from order agent for order ID '{order_id}'.")
    except Exception as e: # Catch any underlying communication or client creation errors
        logger.error(f"Failed to communicate with order agent for order ID '{order_id}': {e}")
        raise A2AAgentError(f"Failed to communicate with order agent for order ID '{order_id}'. Details: {e}")


@tool(args_schema=MarketAuctionArgs)
@ioa_tool_decorator(name="conduct_market_auction")
async def conduct_market_auction(
    quantity: int,
    max_price: float = None,
    max_delivery_days: int = None,
    quality_requirement: float = None
) -> str:
    """
    Conduct a competitive multi-round auction to find the best farm based on price, delivery time, quality, and performance metrics.
    
    This tool combines Scout Agent (fast probing) with Market Agent (competitive analysis) to provide:
    - Fast response-time optimization (parallel probing with timeout)
    - Complete farm response status (available, timeout, errors)
    - Market analysis with scoring breakdown
    - Performance metrics integration
    
    The winner is selected based on:
    - Price (40% weight)
    - Delivery Time (25% weight)
    - Quality (20% weight)
    - Performance Metrics (15% weight) - includes response time, success rate, and stability
    
    Args:
        quantity: The quantity of coffee needed in pounds (required)
        max_price: Maximum acceptable price per pound (optional)
        max_delivery_days: Maximum acceptable delivery time in days (optional)
        quality_requirement: Minimum quality score (0.0 to 1.0, optional)
    
    Returns:
        str: A formatted summary including:
        1. Scout Agent results (farm response status, timeouts, errors)
        2. Market Agent analysis (scoring breakdown, recommendations)
        3. Quality indicator (for retry decision)
        4. Winner and all bids
    """
    logger.info(
        f"Starting market auction with Scout: quantity={quantity}, max_price={max_price}, "
        f"max_delivery_days={max_delivery_days}, quality_requirement={quality_requirement}"
    )
    
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    
    try:
        # Step 1: Use Scout Agent to probe all farms first (for response-time optimization)
        # Build prompt for Scout Agent
        prompt_parts = [f"I want to order {quantity} lbs"]
        if max_price:
            prompt_parts.append(f"at ${max_price:.2f}/lb")
        if max_delivery_days:
            prompt_parts.append(f"with delivery in {max_delivery_days} days")
        prompt_parts.append("Check all farms and pick the best one.")
        scout_prompt = " ".join(prompt_parts)
        
        # Auto-retry with increasing timeout until USABLE result
        current_timeout = SCOUT_INITIAL_TIMEOUT_SEC  # Start with 2s
        max_retries = 5
        retry_count = 0
        
        logger.info(f"Scout: Probing farms with prompt: {scout_prompt}, initial timeout: {current_timeout}s")
        
        while retry_count < max_retries:
            summary = await scout_probe_farms(scout_prompt, current_timeout)
            is_usable, available_count = check_result_quality(summary)
            
            # If result is USABLE, break and proceed to Market Agent analysis
            if is_usable:
                logger.info(f"Market Auction: Got USABLE result on attempt {retry_count + 1} with timeout {current_timeout}s ({available_count} farms responded)")
                break
            
            # If not USABLE, increase timeout and retry
            retry_count += 1
            if retry_count < max_retries:
                # Increase timeout: 2s -> 5s -> 10s -> 15s -> 20s
                if current_timeout == SCOUT_INITIAL_TIMEOUT_SEC:
                    current_timeout = SCOUT_RETRY_TIMEOUT_SEC  # 2s -> 5s
                else:
                    current_timeout += 5.0  # Add 5 seconds each time: 5s -> 10s -> 15s -> 20s
                logger.info(f"Market Auction: Result not USABLE ({available_count} farms responded, need {SCOUT_MIN_AVAILABLE_FARMS}). Retrying with timeout {current_timeout}s (attempt {retry_count + 1}/{max_retries})")
            else:
                logger.warning(f"Market Auction: Max retries reached. Result still not USABLE ({available_count} farms responded)")
        
        scout_timeout = current_timeout  # Use final timeout for display
        
        # Step 2: Extract bids from successful responses for Market Agent
        from agents.market.models import Bid, RequestForQuote
        from agents.market.protocol import parse_bid_from_farm_response
        from agents.market.agent import MarketAgent
        from services.performance_analyzer import get_performance_analyzer
        import re
        
        successful_bids: list[Bid] = []
        scout_summary_lines = []
        
        for result in summary.results:
            if result.status == "ok":
                try:
                    # Try to parse bid from farm response, passing original prompt
                    bid = parse_bid_from_farm_response(result.farm_name, result.price_or_message, original_prompt=scout_prompt)
                    successful_bids.append(bid)
                    scout_summary_lines.append(f"{result.farm_name}: ✓ Available - {result.price_or_message}")
                except (ValueError, Exception) as e:
                    logger.warning(f"Could not parse bid from {result.farm_name}: {e}")
                    scout_summary_lines.append(f"{result.farm_name}: ✓ Available - {result.price_or_message} (Could not extract bid details)")
            elif result.status == "timeout":
                scout_summary_lines.append(f"{result.farm_name}: ⏱ TIMEOUT - No response within {scout_timeout}s (farm may be slow or overloaded)")
            else:
                error_msg = result.error_message or "Communication failed"
                if "Authorization" in error_msg or "unauthorized" in error_msg.lower():
                    scout_summary_lines.append(f"{result.farm_name}: 🔒 AUTHORIZATION ERROR - {error_msg}")
                elif "Timeout" in error_msg or "timeout" in error_msg.lower():
                    scout_summary_lines.append(f"{result.farm_name}: ⏱ TIMEOUT - {error_msg}")
                elif "Connection" in error_msg or "Refused" in error_msg:
                    scout_summary_lines.append(f"{result.farm_name}: ✗ CONNECTION ERROR - {error_msg}")
                else:
                    scout_summary_lines.append(f"{result.farm_name}: ✗ ACCESS ERROR - {error_msg}")
        
        # Step 3: Market Agent analysis ONLY if result is USABLE
        market_analysis = ""
        auction_result = None
        
        # Only perform Market Agent analysis if we have USABLE results (at least 2 farms responded)
        if is_usable and successful_bids:
            try:
                market_agent = MarketAgent()
                
                # Create RFQ
                rfq = RequestForQuote(
                    rfq_id="scout-market-auction",
                    quantity=quantity,
                    max_price_per_lb=max_price,
                    max_delivery_days=max_delivery_days,
                    quality_requirement=quality_requirement
                )
                
                # Filter valid bids
                valid_bids = [b for b in successful_bids if market_agent._is_valid_bid(b, rfq)]
                
                if valid_bids:
                    # Get performance metrics
                    performance_analyzer = get_performance_analyzer()
                    performance_metrics = performance_analyzer.get_farm_performance(use_realtime=True)
                    
                    # Calculate performance weights
                    performance_weights = {}
                    for farm_key in ["brazil", "colombia", "vietnam"]:
                        perf = performance_metrics.get(farm_key)
                        if perf and perf.avg_response_time:
                            performance_weights[farm_key] = 1.0 / (perf.avg_response_time + 0.1)
                        else:
                            performance_weights[farm_key] = 1.0
                    
                    # Score bids
                    scored_bids = []
                    for bid in valid_bids:
                        max_price_bid = max(b.price_per_lb for b in valid_bids)
                        max_delivery_bid = max(b.delivery_days for b in valid_bids)
                        
                        price_score = bid.price_per_lb / max_price_bid if max_price_bid > 0 else 1.0
                        delivery_score = bid.delivery_days / max_delivery_bid if max_delivery_bid > 0 else 1.0
                        quality_score = 1.0 - bid.quality_score
                        
                        farm_key = bid.farm_name.lower()
                        perf_weight = performance_weights.get(farm_key, 1.0)
                        performance_score = 1.0 / perf_weight if perf_weight > 0 else 1.0
                        
                        total_score = (
                            0.40 * price_score +
                            0.25 * delivery_score +
                            0.20 * quality_score +
                            0.15 * performance_score
                        )
                        
                        scored_bids.append((total_score, bid))
                    
                    # Sort by score (lower is better)
                    scored_bids.sort(key=lambda x: x[0])
                    best_score, best_bid = scored_bids[0]
                    
                    # Format market analysis
                    market_analysis_lines = [
                        "\n--- 📊 Market Agent Analysis ---",
                        f"Analyzed {len(valid_bids)} valid bid(s) from {len(successful_bids)} available farm(s).",
                        "",
                        "**Scoring Criteria:**",
                        "- Price: 40% weight (lower is better)",
                        "- Delivery Time: 25% weight (faster is better)",
                        "- Quality Score: 20% weight (higher is better)",
                        "- Performance Metrics: 15% weight (response time, success rate, stability)",
                        "",
                        "**Bid Analysis:**"
                    ]
                    
                    for score, bid in scored_bids:
                        farm_key = bid.farm_name.lower()
                        perf = performance_metrics.get(farm_key)
                        perf_info = ""
                        if perf:
                            perf_info = f" | Performance: {perf.avg_response_time:.2f}s avg, {perf.success_rate*100:.0f}% success"
                        
                        is_best = "🏆" if bid.farm_name == best_bid.farm_name else "  "
                        market_analysis_lines.append(
                            f"{is_best} **{bid.farm_name}**: Score {score:.3f} | "
                            f"${bid.price_per_lb:.2f}/lb | {bid.delivery_days} days | "
                            f"Quality {bid.quality_score:.2f}{perf_info}"
                        )
                    
                    market_analysis_lines.append("")
                    market_analysis_lines.append(f"**Winner:** {best_bid.farm_name} (lowest total score: {best_score:.3f})")
                    market_analysis_lines.append(f"**Total Value:** ${best_bid.price_per_lb * quantity:.2f}")
                    market_analysis = "\n".join(market_analysis_lines)
                    
                    # Store auction result
                    auction_result = {
                        "winner": best_bid,
                        "all_bids": [bid for _, bid in scored_bids],
                        "selection_criteria": "Price (40%), Delivery Time (25%), Quality (20%), Performance (15%)"
                    }
                    
            except Exception as e:
                logger.warning(f"Market Agent analysis failed: {e}")
                market_analysis = "\n--- 📊 Market Agent Analysis ---\nMarket analysis unavailable. Using Scout results only.\n"
        elif not is_usable:
            # Result is not usable - don't show Market Agent analysis, just prompt for retry
            market_analysis = ""
            logger.info(f"Result quality is NEEDS_RETRY ({available_count}/{len(summary.results)} farms). Skipping Market Agent analysis. User should retry with longer timeout.")
        
        # Step 4: Combine Scout and Market analysis
        scout_summary = "\n".join(scout_summary_lines)
        
        # Only return result if USABLE (after all retries)
        if is_usable:
            quality_indicator = f"\n\n**QUALITY:** USABLE (Available: {available_count}/{len(summary.results)}, Required: {SCOUT_MIN_AVAILABLE_FARMS}, Final Timeout: {scout_timeout}s, Attempts: {retry_count + 1})"
            
            # Final combined summary
            final_summary = f"{scout_summary}{market_analysis}{quality_indicator}"
            
            # Show auction result if available
            if auction_result:
                final_summary += f"\n\n🏆 **Auction Complete - Winner: {auction_result['winner'].farm_name}**"
                final_summary += f"\n   Price: ${auction_result['winner'].price_per_lb:.2f}/lb"
                final_summary += f"\n   Delivery: {auction_result['winner'].delivery_days} days"
                final_summary += f"\n   Quality Score: {auction_result['winner'].quality_score:.2f}"
                final_summary += f"\n   Total Value: ${auction_result['winner'].price_per_lb * quantity:.2f}"
                final_summary += f"\n\n📋 **Selection Criteria:** {auction_result['selection_criteria']}"
                final_summary += f"\n\n✅ Order can be placed with {auction_result['winner'].farm_name} for {quantity} lbs at ${auction_result['winner'].price_per_lb:.2f}/lb"
            
            logger.info(f"Market auction with Scout completed. Winner: {auction_result['winner'].farm_name if auction_result else 'None'}, Quality: USABLE")
            return final_summary
        else:
            # Even after all retries, result is not USABLE - return what we have but indicate it's not sufficient
            quality_indicator = f"\n\n**QUALITY:** NOT USABLE (Available: {available_count}/{len(summary.results)}, Required: {SCOUT_MIN_AVAILABLE_FARMS}, Final Timeout: {scout_timeout}s, Attempts: {retry_count + 1})"
            final_summary = f"{scout_summary}{quality_indicator}\n\n⚠️ **Insufficient Responses:** Could not get enough farm responses even after {retry_count + 1} attempts with increasing timeouts. Please check farm services."
            logger.warning(f"Market Auction: Could not get USABLE result after {retry_count + 1} attempts")
            return final_summary
        
    except ValueError as e:
        error_msg = f"Market auction failed: {str(e)}"
        logger.error(error_msg)
        return (
            f"❌ Market Auction Failed\n\n"
            f"{str(e)}\n\n"
            f"**Possible reasons:**\n"
            f"- Farms did not respond in the expected bid format\n"
            f"- Farms declined to participate in the auction\n"
            f"- All bids were rejected due to RFQ constraints\n\n"
            f"**Suggestions:**\n"
            f"- Try using `scout_then_decide` to check farm availability first\n"
            f"- Try placing a direct order with `create_order` tool\n"
            f"- Check if farms are running and accessible"
        )
    except Exception as e:
        error_msg = f"Error conducting market auction: {str(e)}"
        logger.error(error_msg)
        return (
            f"❌ Market Auction Error\n\n"
            f"An error occurred while conducting the auction: {str(e)}\n\n"
            f"Please try again or use an alternative method to place your order."
        )
