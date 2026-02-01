# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0
import logging
import uuid

from pydantic import BaseModel, Field

from langchain_core.prompts import PromptTemplate
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph import MessagesState
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from ioa_observe.sdk.decorators import agent, graph

from agents.supervisors.auction.graph.tools import (
    get_farm_yield_inventory,
    get_all_farms_yield_inventory_streaming,
    create_order,
    get_order_details,
    scout_then_decide,
    scout_then_market_analyze_then_decide,
    conduct_market_auction,
    tools_or_next
)
from common.llm import get_llm
from config.config import DEFAULT_MESSAGE_TRANSPORT, SCOUT_INITIAL_TIMEOUT_SEC, SCOUT_RETRY_TIMEOUT_SEC

logger = logging.getLogger("lungo.supervisor.graph")

class NodeStates:
    SUPERVISOR = "exchange_supervisor"

    INVENTORY_SINGLE_FARM = "inventory_single_farm"
    INVENTORY_ALL_FARMS = "inventory_all_farms"

    ORDERS = "orders_broker"
    ORDERS_TOOLS = "orders_tools"

    REFLECTION = "reflection"
    GENERAL_INFO = "general"

class GraphState(MessagesState):
    """
    Represents the state of our graph, passed between nodes.
    """
    next_node: str
    full_response: str = ""

@agent(name="exchange_agent")
class ExchangeGraph:
    def __init__(self):
        self.graph = self.build_graph()

    @graph(name="exchange_graph")
    def build_graph(self) -> CompiledStateGraph:
        """
        Constructs and compiles a LangGraph instance.

        Agent Flow:

        supervisor_agent
            - converse with user and coordinate app flow

        inventory_single_farm_agent
            - get inventory for a specific farm
        
        inventory_all_farms_agent
            - broadcast to all farms and aggregate inventory

        orders_agent
            - initiate orders with a specific farm and retrieve order status

        reflection_agent
            - determine if the user's request has been satisfied or if further action is needed

        Returns:
        CompiledGraph: A fully compiled LangGraph instance ready for execution.
        """

        self.supervisor_llm = None
        self.reflection_llm = None
        self.inventory_single_farm_llm = None
        self.inventory_all_farms_llm = None
        self.orders_llm = None

        workflow = StateGraph(GraphState)

        # --- 1. Define Node States ---

        workflow.add_node(NodeStates.SUPERVISOR, self._supervisor_node)
        workflow.add_node(NodeStates.INVENTORY_SINGLE_FARM, self._inventory_single_farm_node)
        workflow.add_node(NodeStates.INVENTORY_ALL_FARMS, self._inventory_all_farms_node)
        workflow.add_node(NodeStates.ORDERS, self._orders_node)
        # Include scout_then_market_analyze_then_decide for combined Scout + Market analysis (for NATS, both streaming and non-streaming)
        # Include scout_then_decide as fallback
        # Include conduct_market_auction for competitive bidding
        orders_tools_list = [create_order, get_order_details, conduct_market_auction]
        if DEFAULT_MESSAGE_TRANSPORT == "NATS":
            orders_tools_list.append(scout_then_market_analyze_then_decide)
            orders_tools_list.append(scout_then_decide)  # Keep as fallback
        workflow.add_node(NodeStates.ORDERS_TOOLS, ToolNode(orders_tools_list))
        workflow.add_node(NodeStates.REFLECTION, self._reflection_node)
        workflow.add_node(NodeStates.GENERAL_INFO, self._general_response_node)

        # --- 2. Define the Agentic Workflow ---

        workflow.set_entry_point(NodeStates.SUPERVISOR)

        # Add conditional edges from the supervisor
        workflow.add_conditional_edges(
            NodeStates.SUPERVISOR,
            lambda state: state["next_node"],
            {
                NodeStates.INVENTORY_SINGLE_FARM: NodeStates.INVENTORY_SINGLE_FARM,
                NodeStates.INVENTORY_ALL_FARMS: NodeStates.INVENTORY_ALL_FARMS,
                NodeStates.ORDERS: NodeStates.ORDERS,
                NodeStates.GENERAL_INFO: NodeStates.GENERAL_INFO,
            },
        )

        workflow.add_edge(NodeStates.INVENTORY_SINGLE_FARM, NodeStates.REFLECTION)
        workflow.add_edge(NodeStates.INVENTORY_ALL_FARMS, NodeStates.REFLECTION)

        workflow.add_conditional_edges(NodeStates.ORDERS, tools_or_next(NodeStates.ORDERS_TOOLS, NodeStates.REFLECTION))
        workflow.add_edge(NodeStates.ORDERS_TOOLS, NodeStates.ORDERS)

        workflow.add_edge(NodeStates.GENERAL_INFO, END)
        return workflow.compile()
    
    async def _supervisor_node(self, state: GraphState) -> dict:
        """
        Determines the intent of the user's message and routes to the appropriate node.
        """
        if not self.supervisor_llm:
            self.supervisor_llm = get_llm()

        user_message = state["messages"]

        prompt = PromptTemplate(
            template="""You are a global coffee exchange agent connecting users to coffee farms in Brazil, Colombia, and Vietnam. 
            Based on the user's message, determine the appropriate action:
            - Respond with 'orders' if the message includes:
                * Quantity specifications (e.g., "50 lb", "100 kg")
                * Price or cost information (e.g., "for $X", "at Y cents per lb")
                * Purchase intent keywords (e.g., "need", "want", "buy", "order", "purchase")
            - Respond with 'inventory_single_farm' if the user asks about a SPECIFIC farm (Brazil, Colombia, or Vietnam)
            - Respond with 'inventory_all_farms' if the user asks about inventory/yield from ALL farms or doesn't specify a farm
            - Respond with 'none of the above' if the message is unrelated to coffee 'inventory' or 'orders'
            
            User message: {user_message}
            """,
            input_variables=["user_message"]
        )

        chain = prompt | self.supervisor_llm
        response = chain.invoke({"user_message": user_message})
        intent = response.content.strip().lower()

        logger.info(f"Supervisor decided: {intent}")

        if "inventory_single_farm" in intent:
            return {"next_node": NodeStates.INVENTORY_SINGLE_FARM, "messages": user_message}
        elif "inventory_all_farms" in intent:
            return {"next_node": NodeStates.INVENTORY_ALL_FARMS, "messages": user_message}
        elif "orders" in intent:
            return {"next_node": NodeStates.ORDERS, "messages": user_message}
        else:
            return {"next_node": NodeStates.GENERAL_INFO, "messages": user_message}
        
    async def _reflection_node(self, state: GraphState) -> dict:
        """
        Reflect on the conversation to determine if the user's query has been satisfied 
        or if further action is needed.
        """
        if not self.reflection_llm:
            class ShouldContinue(BaseModel):
                should_continue: bool = Field(description="Whether to continue processing the request.")
                reason: str = Field(description="Reason for decision whether to continue the request.")
            
            # create a structured output LLM for reflection (streaming=False required for structured output)
            self.reflection_llm = get_llm(streaming=False).with_structured_output(ShouldContinue, strict=True)

        sys_msg_reflection = SystemMessage(
            content="""You are an AI assistant reflecting on a conversation to determine if the user's request has been fully addressed.
            Review the entire conversation history provided.

            Decide whether the user's *original query* has been satisfied by the responses given so far. If the prompt is related to order, please ensure the farm information is included in the final response.
            For permission issues regarding creating a payment or list transaction, please include which operation failed in the final response.
            If the last message from the AI provides a conclusive answer to the user's request, or if the conversation has reached a natural conclusion, then set 'should_continue' to false.
            Do NOT continue if:
            - The last message from the AI is a final answer to the user's initial request.
            - The last message from the AI is a question that requires user input, and we are waiting for that input.
            - The conversation seems to be complete and no further action is explicitly requested or implied.
            - The conversation appears to be stuck in a loop or repeating itself (the 'is_duplicate_message' check will also help here).

            If more information is needed from the AI to fulfill the original request, or if the user has asked a follow-up question that needs an AI response, then set 'should_continue' to true.
            """,
            pretty_repr=True,
        )

        response = await self.reflection_llm.ainvoke(
          [sys_msg_reflection] + state["messages"],
          
        )
        logging.info(f"Reflection agent response: {response}")

        # Handle case where structured output returns None (can happen with streaming enabled)
        if response is None:
            logging.warning("Reflection agent returned None, defaulting to not continue")
            return {"next_node": END}

        is_duplicate_message = (
          len(state["messages"]) > 2 and state["messages"][-1].content == state["messages"][-3].content
        )
        
        should_continue = response.should_continue and not is_duplicate_message
        next_node = NodeStates.SUPERVISOR if should_continue else END

        if next_node == END and any(keyword in response.reason.lower() for keyword in ["auth", "access", "permission", "identity"]):

            err_msg = "Authentication or authorization failed. Please check your credentials and try again."
            for farm in ['colombia', 'brazil', 'vietnam']:
                if farm in state["messages"][-1].content.lower():
                    err_msg = f"The supervisor agent doesn't have permission to access the {farm.title()} farm. Please verify your access credentials and try again."
                    break

            for keyword in ["transaction", "payment"]:
                if keyword in state["messages"][-1].content.lower():
                    err_msg = f"Not authorized to perform '{keyword}' operation through the Payment MCP service. Please verify your farm credentials and try again."
                    break

            return {
                "next_node": END,
                "messages": [AIMessage(content=err_msg)],
            }

        logging.info(f"Next node: {next_node}, Reason: {response.reason}")

        # Don't add messages to state, just return the next_node decision
        return {
          "next_node": next_node,
        }

    async def _inventory_single_farm_node(self, state: GraphState) -> dict:
        """
        Handles inventory queries for a specific farm by directly calling the tool.
        """
        if not self.inventory_single_farm_llm:
            self.inventory_single_farm_llm = get_llm()

        # Get latest HumanMessage
        user_msg = next((m for m in reversed(state["messages"]) if m.type == "human"), None)
        if not user_msg:
            return {"messages": [AIMessage(content="No user message found.")]}

        user_query = user_msg.content.lower()
        logger.info(f"Processing single farm inventory query: {user_query}")

        # Determine which farm
        farm = None
        if "brazil" in user_query:
            farm = "brazil"
        elif "colombia" in user_query:
            farm = "colombia"
        elif "vietnam" in user_query:
            farm = "vietnam"

        if not farm:
            return {"messages": [AIMessage(content="Please specify which farm you'd like to query (Brazil, Colombia, or Vietnam).")]}

        try:
            # Call the function directly
            tool_result = await get_farm_yield_inventory(user_msg.content, farm)
            
            # Check for errors in the result
            if "error" in str(tool_result).lower() or "failed" in str(tool_result).lower():
                error_message = f"I encountered an issue retrieving information from the {farm.title()} farm. Please try again later."
                return {"messages": [AIMessage(content=error_message)]}

            # Use LLM to format the response
            prompt = PromptTemplate(
                template="""You are an inventory broker for a global coffee exchange company.
                The user asked about inventory from the {farm} farm.
                
                User's request: {user_message}
                
                Farm response:
                {tool_result}
                
                Please provide a clear and concise response to the user based on the farm's inventory information.
                """,
                input_variables=["farm", "user_message", "tool_result"]
            )

            chain = prompt | self.inventory_single_farm_llm
            llm_response = await chain.ainvoke({
                "farm": farm.title(),
                "user_message": user_msg.content,
                "tool_result": tool_result,
            })

            return {"messages": [AIMessage(content=llm_response.content)]}

        except Exception as e:
            logger.error(f"Error in single farm inventory node: {e}")
            error_message = f"I encountered an issue retrieving information from the {farm.title()} farm. Please try again later."
            return {"messages": [AIMessage(content=error_message)]}

    async def _inventory_all_farms_node(self, state: GraphState) -> dict:
        """
        Handles inventory queries for all farms by streaming data from multiple farm agents.
        
        Behavior:
        - Streaming mode (astream_events): Yields each chunk as it arrives from farms,
          allowing progressive display of inventory data in real-time.
        - Non-streaming mode (ainvoke): Only the final aggregated response is used,
          containing the complete inventory from all farms.
        """
        # Extract the latest user message from the conversation state
        user_msg = next((m for m in reversed(state["messages"]) if m.type == "human"), None)
        if not user_msg:
            yield {"messages": [AIMessage(content="No user message found.")]}

        logger.info(f"Processing all farms inventory query: {user_msg.content}")

        try:
            # Collect inventory data from all farms via streaming
            full_response = ""
            success_count = 0
            error_count = 0
            has_timeout_warning = False
            
            async for chunk in get_all_farms_yield_inventory_streaming(user_msg.content):
                # Yield each chunk immediately for streaming mode
                # In non-streaming mode, these intermediate yields are ignored
                yield {"messages": [AIMessage(content=chunk.strip())]}
                full_response += chunk
                
                # Track successful responses vs errors from the streaming tool
                if chunk.strip().startswith("Error"):
                    error_count += 1
                elif "timeout" in chunk.lower() or "timed out" in chunk.lower():
                    has_timeout_warning = True
                else:
                    success_count += 1
            
            # Check if we received any successful responses
            if success_count == 0:
                error_message = "No responses received from any farms. Please ensure farm agents are running and try again."
                logger.warning(error_message)
                yield {"messages": [AIMessage(content=error_message)]}
                return
            
            # Yield final aggregated response with complete inventory
            # This is what gets returned in non-streaming mode (ainvoke)
            # In streaming mode, this provides the final summary with all data
            final_content = f"Here is the current coffee yield inventory from the farms:\n\n{full_response.strip()}"
            
            # Add note if there were errors or timeout warnings
            if error_count > 0 or has_timeout_warning:
                final_content += f"\n\nNote: Some farms encountered errors or did not respond in time. Showing available inventory data."
                logger.warning(f"Partial farm responses: {success_count} successful, {error_count} errors")
            
            yield {"messages": [AIMessage(content=final_content)], "full_response": final_content}

        except Exception as e:
            logger.error(f"Error in all farms inventory node: {e}")
            error_message = f"I encountered an issue retrieving information from the farms: {str(e)}. Please ensure all farm agents are running and try again."
            yield {"messages": [AIMessage(content=error_message)]}

    async def _orders_node(self, state: GraphState) -> dict:
        """
        Handles orders-related queries using an LLM to formulate responses,
        with retry logic for tool failures.
        """
        if not self.orders_llm:
            # Include scout_then_market_analyze_then_decide for combined Scout + Market analysis (for NATS, both streaming and non-streaming)
            # Include scout_then_decide as fallback
            # Include conduct_market_auction for competitive bidding
            from agents.supervisors.auction.graph.tools import scout_then_market_analyze_then_decide
            tools = [create_order, get_order_details, conduct_market_auction]
            if DEFAULT_MESSAGE_TRANSPORT == "NATS":
                tools.append(scout_then_market_analyze_then_decide)
                tools.append(scout_then_decide)  # Keep as fallback
            self.orders_llm = get_llm().bind_tools(tools)

        # Extract the latest HumanMessage for the prompt
        user_msg = next((m for m in reversed(state["messages"]) if m.type == "human"), None)
        # Find the last AIMessage that initiated tool calls
        last_ai_message = None
        for m in reversed(state["messages"]):
            if isinstance(m, AIMessage) and m.tool_calls:
                last_ai_message = m
                break

        collected_tool_messages = []
        if last_ai_message:
            tool_call_ids = {tc.get("id") for tc in last_ai_message.tool_calls if tc.get("id")}
            for m in reversed(state["messages"]):
                if isinstance(m, ToolMessage) and m.tool_call_id in tool_call_ids:
                    collected_tool_messages.append(m)

        tool_results_summary = []
        any_tool_failed = False # Flag to track if ANY tool call failed

        auth_failure = ""
        if collected_tool_messages:
            for tool_msg in collected_tool_messages:
                result_str = str(tool_msg.content) # Convert to string for keyword checking

                # Special handling for scout_then_decide, scout_then_market_analyze_then_decide, and conduct_market_auction
                # These tools return structured summaries with farm status
                if tool_msg.name in ("scout_then_decide", "scout_then_market_analyze_then_decide", "conduct_market_auction"):
                    # Scout/Market Agent returns structured results - check if any farm succeeded
                    has_success = "✓ Available" in result_str
                    # Also check for QUALITY: USABLE indicator
                    is_usable = "QUALITY: USABLE" in result_str or "QUALITY: USABLE" in result_str.upper()
                    
                    # Check for actual failure patterns (not just keywords that might appear in successful responses)
                    # Only consider it a failure if:
                    # 1. No farms succeeded AND
                    # 2. Result explicitly indicates failure (NOT USABLE, or all farms have error/timeout status)
                    has_explicit_failure = (
                        "QUALITY: NOT USABLE" in result_str or
                        "QUALITY: NEEDS_RETRY" in result_str or
                        ("Insufficient Responses" in result_str and not has_success)
                    )
                    
                    if has_success or is_usable:
                        # At least one farm responded or quality is usable - this is a SUCCESS
                        # Note: "Could not extract bid details" is not a failure - it just means Market Agent couldn't parse, but Scout succeeded
                        tool_results_summary.append(f"SUCCESS from tool '{tool_msg.name}' (ID: {tool_msg.tool_call_id}): {result_str}")
                    elif has_explicit_failure and not has_success:
                        # Explicit failure indication and no successful farms
                        any_tool_failed = True
                        tool_results_summary.append(f"PARTIAL_FAILURE for '{tool_msg.name}' (ID: {tool_msg.tool_call_id}): All farms had issues, but here's the summary: {result_str}")
                        logger.warning(f"Scout/Market Agent: All farms failed. Result: {result_str[:200]}...")
                    else:
                        # Ambiguous case - assume success if we have any response
                        tool_results_summary.append(f"SUCCESS from tool '{tool_msg.name}' (ID: {tool_msg.tool_call_id}): {result_str}")
                else:
                    # For other tools, use the original failure detection logic
                    # Check for failure keywords in each individual tool result
                    # But exclude informational messages like "Could not extract bid details" which are warnings, not failures
                    failure_keywords = ["error", "failed", "timeout"]
                    # Exclude informational messages that are not actual failures
                    informational_messages = ["could not extract bid details", "market analysis unavailable"]
                    
                    # IMPORTANT: If result contains "✓ Available" or success indicators, it's NOT a failure
                    # even if it contains failure keywords (which might be in the farm's response text)
                    has_success_indicator = "✓ Available" in result_str or "SUCCESS" in result_str.upper()
                    
                    # Check if it's an informational message (not a failure)
                    is_informational = any(info_msg in result_str.lower() for info_msg in informational_messages)
                    
                    # Check for actual failures (but not informational messages or successful responses)
                    has_failure_keyword = any(keyword in result_str.lower() for keyword in failure_keywords)
                    
                    if has_failure_keyword and not is_informational and not has_success_indicator:
                        any_tool_failed = True
                        # Include tool name and ID for better context
                        tool_results_summary.append(f"FAILURE for '{tool_msg.name}' (ID: {tool_msg.tool_call_id}): The request could not be completed.")
                        logger.warning(f"Detected tool failure in orders node result: {result_str[:200]}...")
                        
                        if "auth" in result_str.lower():
                            auth_failure = result_str
                    else:
                        # Success or informational message (not a failure)
                        tool_results_summary.append(f"SUCCESS from tool '{tool_msg.name}' (ID: {tool_msg.tool_call_id}): {result_str}")

            context = "\n".join(tool_results_summary)
        else:
            context = "No previous tool execution context available."

        # Build prompt template with actual timeout values
        prompt_template_str = f"""You are an orders broker for a global coffee exchange company.
            Your task is to handle user requests related to placing and checking orders with coffee farms.

            User's current request: {{user_message}}

            --- Context from previous tool execution (if any) ---
            {{tool_context}}

            --- Instructions for your response ---
            1.  **Process ALL tool results provided in the context.** This includes both successful and failed attempts. If the context contains error messages related to authentication or authorization, please note them specifically.
            2.  **If ANY tool call result indicates a FAILURE:**
                *   Acknowledge the failure to the user for the specific request(s) that failed.
                *   Politely inform the user that the request could not be completed for those parts due to an issue (e.g., "The farm is currently unreachable" or "An error occurred").
                *   **IMPORTANT: Do NOT include technical error messages, stack traces, or raw tool output details directly in your response to the user.** Summarize failures concisely.
                *   **Crucially, DO NOT attempt to call the same or any other tool again for any failed part of the request.**
                *   If other tool calls were successful, present their results clearly and concisely.
                *   Your response MUST synthesize all available information (successes and failures) into a single, comprehensive message.
                *   Your response MUST NOT contain any tool calls.

            3.  **If ALL tool call results indicate SUCCESS:**
                *   Summarize the provided information clearly and concisely to the user, directly answering their request.
                *   Your response MUST NOT contain any tool calls, as the information has already been obtained.

            4.  **If there is no 'Previous tool call result' (i.e., this is the first attempt):**
                *   Determine if a tool needs to be called to answer the user's question.
                *   **For competitive bidding or auction requests:**
                    - If the user asks for "best price", "competitive bidding", "auction", "compare prices", or wants multiple farms to compete, use `conduct_market_auction`.
                    - This tool runs a multi-round auction where farms compete, and selects the winner based on price (40%), delivery (25%), quality (20%), and performance metrics (15%).
                    - Example: "I need 200 lbs. Run a competitive auction to get the best price."
                *   **For simple order requests with quantity/price:**
                    - **PREFER using `scout_then_market_analyze_then_decide`** - This combines Scout Agent (fast probing) with Market Agent (competitive analysis with scoring criteria). It provides both fast responses AND intelligent market analysis showing price, delivery, quality, and performance metrics.
                    - If `scout_then_market_analyze_then_decide` is not available, use `scout_then_decide` with timeout_sec={SCOUT_INITIAL_TIMEOUT_SEC} to get a quick initial summary from all farms.
                    - If the user asks about placing an order, use `scout_then_market_analyze_then_decide` first to get both probe results and market analysis, then use `create_order` with the recommended farm.
                *   If the user asks about checking the status of an order, use the `get_order_details` tool.
                *   If further information is needed to call a tool (e.g., missing order ID, quantity, farm), ask the user for clarification.

            5.  **Special handling for scout_then_decide and scout_then_market_analyze_then_decide results:**
                *   These tools return structured summaries showing each farm's status (Available, No response, or Issue).
                *   **IMPORTANT: For these tool results, you MUST show the user the complete summary with ALL farm responses.**
                *   The summary format is: "Farm Name: Status - Response/Message"
                *   **For scout_then_market_analyze_then_decide, the summary includes:**
                    - Scout results from all farms
                    - Market Agent analysis with scoring breakdown (if bids were successfully parsed)
                    - Recommended farm based on market criteria
                *   **IMPORTANT: If you see "(Could not extract bid details for market analysis)" in the summary:**
                    - This is NOT a failure - it just means Market Agent couldn't parse the bid format from that farm's response
                    - The Scout Agent still successfully got the response from that farm
                    - You should still proceed with the order using the available information
                    - Show the user the farm's actual response even if Market Agent couldn't parse it
                *   **Check the QUALITY indicator at the end of the summary:**
                    - If it shows "QUALITY: USABLE" - the result is good enough (at least 2 farms responded)
                    - If it shows "QUALITY: NEEDS_RETRY" - the result needs improvement (less than 2 farms responded)
                *   **Show the user the full summary** so they can see:
                    - Which farms responded and their actual responses (the full text from each farm)
                    - Market Agent analysis (if available) with scoring criteria and recommendations
                    - Which farms timed out
                    - Which farms had issues and what those issues were
                    - The quality indicator showing if retry is recommended
                *   **If the summary shows "QUALITY: NEEDS_RETRY"**, inform the user that they can retry with a longer timeout ({SCOUT_RETRY_TIMEOUT_SEC}s) to get more responses.
                *   **If the summary shows ANY farm with "✓ Available"**, recommend the best option based on the farm responses or Market Agent recommendation (if available).
                *   **If Market Agent analysis is available**, use its recommendation to help the user make a decision.
                *   **If ALL farms show "⏱ No response" or "✗ Issue"**, show the complete summary and explain what happened to each farm, then suggest:
                    - Retrying with a longer timeout ({SCOUT_RETRY_TIMEOUT_SEC}s) to wait for slower farms
                    - Trying again in a moment
                    - Checking if farm services are running
                    - Using a different approach (e.g., direct farm query)
                *   **DO NOT summarize or hide the summary - show it to the user so they can see all farm responses and market analysis.**

            Your final response should be a conclusive answer to the user's request, or a clear explanation if the request cannot be fulfilled.
            """

        prompt = PromptTemplate(
            template=prompt_template_str,
            input_variables=["user_message", "tool_context"]
        )

        chain = prompt | self.orders_llm

        llm_response = await chain.ainvoke({
            "user_message": user_msg.content if user_msg else "No specific user message.",
            "tool_context": context,
        })

        # --- Safety Net: Force non-tool-calling response if LLM ignores failure instruction ---
        if any_tool_failed and llm_response.tool_calls:
            logger.warning(
                "LLM attempted tool call despite previous tool failure(s) in orders node. "
                "Forcing a user-facing error message to prevent loop."
            )

            forced_error_message = (
                "I'm sorry, I was unable to complete your order request for all items. "
                "An issue occurred for some parts. Please try again later."
            )

            if auth_failure:
                forced_error_message = f"{auth_failure} Please try again later."

            llm_response = AIMessage(
                content=forced_error_message,
                tool_calls=[],
                name=llm_response.name,
                id=llm_response.id,
                response_metadata=llm_response.response_metadata
            )
        # --- End Safety Net ---

        return {"messages": [llm_response]}


    def _general_response_node(self, state: GraphState) -> dict:
        return {
            "next_node": END,
            "messages": [AIMessage(content="I'm not sure how to handle that. Could you please clarify?")],
        }

    async def serve(self, prompt: str) -> str:
        """
        Processes the input prompt and returns a complete response from the graph execution.
        
        This method uses LangGraph's ainvoke() to execute the entire graph synchronously,
        waiting for all nodes to complete before returning the final result. Unlike streaming_serve(),
        this method blocks until the full execution is complete and returns only the final output.

        Args:
            prompt (str): The input prompt to be processed by the graph.

        Returns:
            str: The final response content from the last AIMessage in the graph execution.

        Raises:
            ValueError: If the prompt is empty or not a string.
            RuntimeError: If no valid AIMessage is found in the graph response.
            Exception: If any error occurs during graph execution.
        """
        try:
            logger.debug(f"Received prompt: {prompt}")
            
            # Validate input prompt
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("Prompt must be a non-empty string.")
            
            # Execute the graph using ainvoke() - this runs the entire graph to completion
            # The graph will route through nodes based on the routing logic and return the final state
            result = await self.graph.ainvoke({
                "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
                ],
            }, {"configurable": {"thread_id": uuid.uuid4()}})

            # Extract messages from the final state
            # The messages list contains the full conversation history including user, AI, and tool messages
            messages = result.get("messages", [])
            if not messages:
                raise RuntimeError("No messages found in the graph response.")

            # Find the last AIMessage with non-empty content
            # We iterate in reverse to get the most recent response from the agent
            # This skips over any tool messages or empty responses
            for message in reversed(messages):
                if isinstance(message, AIMessage) and message.content.strip():
                    logger.debug(f"Valid AIMessage found: {message.content.strip()}")
                    return message.content.strip()

            # If no valid AIMessage is found, raise an error
            raise RuntimeError("No valid AIMessage found in the graph response.")
        except ValueError as ve:
            logger.error(f"ValueError in serve method: {ve}")
            raise ValueError(str(ve))
        except Exception as e:
            logger.error(f"Error in serve method: {e}")
            raise Exception(str(e))

    async def streaming_serve(self, prompt: str):
        """
        Streams the graph execution using LangGraph's astream_events API, yielding chunks as they arrive.
        
        This method leverages LangGraph's event streaming to provide real-time updates as the graph
        executes across multiple nodes. It captures intermediate outputs from each node and streams
        them back to the caller, enabling progressive data delivery for long-running operations.

        LangGraph Reference:
            - Uses `astream_events()` for streaming
            - Each event includes metadata (node name, event type) and data (chunks, messages)

        Args:
            prompt (str): The input prompt to be processed by the graph.

        Yields:
            str: Message content chunks as they arrive from nodes during graph execution.
                 Only yields AIMessage content, filtering out duplicates and reflection nodes.

        Raises:
            ValueError: If the prompt is empty or not a string.
            Exception: If any error occurs during graph execution or streaming.
        """
        try:
            logger.debug(f"Received streaming prompt: {prompt}")
            
            # Validate input prompt
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("Prompt must be a non-empty string.")

            # Construct the initial state for the LangGraph execution
            # The state follows the MessageGraph pattern with a messages list
            state = {
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
            }

            # Track seen content to prevent duplicate yields when nodes produce the same output
            seen_contents = set()
            final_response = None  # Track the final complete response
            tool_results_seen = set()  # Track tool results to stream individual farm responses
            
            # Stream events from the graph using astream_events (LangGraph v2 API)
            # This provides fine-grained control over streaming, emitting events for:
            # - Node starts/ends (on_chain_start, on_chain_end)
            # - Intermediate outputs (on_chain_stream)
            async for event in self.graph.astream_events(state, {"configurable": {"thread_id": uuid.uuid4()}}, version="v2"):
                logger.debug(f"Event: {event}")
                
                # Stream individual farm responses from tool execution (for group chat effect)
                if event["event"] == "on_tool_end":
                    tool_name = event.get("name", "")
                    data = event.get("data", {})
                    
                    # For scout tools, extract individual farm responses for streaming
                    if tool_name in ("scout_then_market_analyze_then_decide", "scout_then_decide", "conduct_market_auction"):
                        if "output" in data:
                            # Get the actual tool output - it might be a ToolMessage object or a string
                            output_obj = data["output"]
                            
                            # Extract content from ToolMessage if it's an object
                            if hasattr(output_obj, 'content'):
                                tool_output = str(output_obj.content)
                            elif isinstance(output_obj, str):
                                tool_output = output_obj
                            else:
                                tool_output = str(output_obj)
                            
                            # Clean up tool output - remove any wrapper text like "content="
                            # Extract the actual content if it's wrapped in a string representation
                            import re
                            content_match = re.search(r'content=["\']([^"\']+)["\']', tool_output)
                            if content_match:
                                tool_output = content_match.group(1)
                            
                            # Also handle escaped newlines
                            tool_output = tool_output.replace('\\n', '\n')
                            
                            logger.info(f"Extracting farm responses from tool output (length: {len(tool_output)})")
                            
                            # Split by newline and look for lines starting with farm names
                            all_matches = []
                            lines = tool_output.split('\n')
                            
                            for line in lines:
                                line = line.strip()
                                if not line:
                                    continue
                                
                                # Look for lines starting with farm names (with or without markdown)
                                for farm in ['Brazil', 'Colombia', 'Vietnam']:
                                    # Match patterns like:
                                    # "Brazil: ✓ Available - ..."
                                    # "- **Brazil**: ✓ Available - ..."
                                    # "**Brazil**: ✓ Available - ..."
                                    pattern = rf'^(?:-?\s*)?\*\*?{farm}\*\*?:?\s*([✓✗⏱🔒].*?)(?=\n\*\*?(?:Brazil|Colombia|Vietnam)|$)'
                                    match = re.search(pattern, line, re.IGNORECASE)
                                    
                                    if match or line.startswith(farm + ':') or line.startswith('**' + farm + '**:') or line.startswith('- **' + farm + '**:'):
                                        # Clean up the line - remove markdown
                                        clean_line = re.sub(r'\*\*?', '', line).strip()
                                        
                                        # Ensure it starts with farm name
                                        if not clean_line.startswith(farm + ':'):
                                            # Try to extract the farm response part
                                            farm_match = re.search(rf'{farm}:\s*([✓✗⏱🔒].*)', clean_line, re.IGNORECASE)
                                            if farm_match:
                                                clean_line = f"{farm}: {farm_match.group(1).strip()}"
                                            else:
                                                # Fallback: just use the line as-is
                                                pass
                                        
                                        if clean_line and clean_line not in tool_results_seen:
                                            tool_results_seen.add(clean_line)
                                            all_matches.append(clean_line)
                                            logger.info(f"Found farm response: {clean_line[:80]}...")
                                        break
                            
                            # Yield all individual farm responses
                            for farm_response in all_matches:
                                logger.info(f"Streaming individual farm response: {farm_response[:100]}...")
                                yield farm_response
                            
                            if not all_matches:
                                logger.warning(f"No farm responses extracted from tool output. Output preview: {tool_output[:200]}...")
                
                # Filter for "on_chain_stream" events which contain intermediate node outputs
                # These events fire when a node produces output during execution, allowing
                # us to stream results progressively rather than waiting for full completion
                if event["event"] == "on_chain_stream":
                    node_name = event.get("name", "")
                    data = event.get("data", {})
                    
                    # Extract the chunk from the event data
                    # Chunks contain partial state updates from the executing node
                    if "chunk" in data:
                        chunk = data["chunk"]
                        
                        # Check if this chunk contains messages (the primary output type)
                        if "messages" in chunk and chunk["messages"]:
                            logger.info(f"Streaming chunk from node '{node_name}': {chunk}")
                            
                            # Skip messages from the reflection node to avoid streaming internal reasoning
                            # The reflection node performs self-evaluation and shouldn't be user-facing
                            if node_name == NodeStates.REFLECTION:
                                logger.info(f"Skipping messages from reflection node")
                                continue
                            
                            # Process and yield all messages from this chunk
                            for message in chunk["messages"]:
                                # Only yield AIMessage content (responses from the agent/LLM)
                                # Filter out system messages, tool messages, and human messages
                                if isinstance(message, AIMessage) and message.content:
                                    content = message.content.strip()
                                    
                                    # Store the final response (last AIMessage from orders node)
                                    # But don't yield it here - we'll yield it at the end with proper formatting
                                    if node_name == NodeStates.ORDERS:
                                        final_response = content
                                        # Don't yield intermediate AIMessage from orders node
                                        # We'll yield the final formatted response at the end
                                        logger.info(f"Captured final response from '{node_name}', will yield at end: {content[:100]}...")
                                        continue
                                    
                                    # For other nodes, yield immediately
                                    # Deduplicate: Skip if we've already yielded this exact content
                                    if content in seen_contents:
                                        logger.info(f"Skipping duplicate content from '{node_name}': {content}")
                                        continue
                                    
                                    # Mark this content as seen and yield it to the caller
                                    seen_contents.add(content)
                                    logger.info(f"Yielding message from '{node_name}': {content}")
                                    yield message.content
                
                # Also capture final state on completion to ensure we have the complete response
                if event["event"] == "on_chain_end" and event.get("name") == "":
                    # Graph execution completed - extract final messages
                    data = event.get("data", {})
                    if "output" in data and "messages" in data["output"]:
                        for message in data["output"]["messages"]:
                            if isinstance(message, AIMessage) and message.content:
                                final_content = message.content.strip()
                                if final_content:
                                    final_response = final_content
                                    logger.info(f"Captured final response from chain end: {final_content[:100]}...")
            
            # Yield the final response at the very end (same format as non-streaming)
            # This ensures it appears after all intermediate results
            if final_response:
                # Check if we've already yielded this exact content
                if final_response not in seen_contents:
                    seen_contents.add(final_response)
                    logger.info(f"Yielding final formatted response at end (same format as non-streaming)")
                    yield final_response
                else:
                    logger.info(f"Final response already yielded, skipping duplicate")

        except ValueError as ve:
            logger.error(f"ValueError in streaming_serve method: {ve}")
            raise ValueError(str(ve))
        except Exception as e:
            logger.error(f"Error in streaming_serve method: {e}")
            raise Exception(str(e))
