# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import logging
import asyncio
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

        # Use asyncio.wait_for to enforce timeout
        response = await asyncio.wait_for(
            client.send_message(request),
            timeout=timeout_sec
        )
        
        logger.info(f"Scout received response from {farm_name}: {response}")
        
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
    
    Args:
        prompt: The prompt to send to all farms
        timeout_sec: Maximum time to wait per farm (default from config)
        
    Returns:
        ScoutSummary with results from all farms
    """
    import time
    start_time = time.time()
    
    logger.info(f"Scout: Starting parallel probe of all farms with timeout {timeout_sec}s")
    logger.info(f"Scout: Prompt: {prompt}")
    logger.info(f"Scout: Transport: {DEFAULT_MESSAGE_TRANSPORT}, Endpoint: {TRANSPORT_SERVER_ENDPOINT}")
    
    # Probe all farms in parallel
    tasks = [
        _probe_single_farm(prompt, "brazil", timeout_sec),
        _probe_single_farm(prompt, "colombia", timeout_sec),
        _probe_single_farm(prompt, "vietnam", timeout_sec),
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
