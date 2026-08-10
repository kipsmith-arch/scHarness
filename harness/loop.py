"""Generic agent loop (LangGraph) — domain agnostic (loop_design.md).

The loop does four things:
    1. load a skill (system_prompt + tool_schemas + tool_runtime)
    2. run the LLM <-> tool loop (function calling; messages only grow)
    3. record the full conversation (conversation.jsonl)
    4. provide a generic notebook (write_note / retrieve_notes, builtin)

The loop never hardcodes domain knowledge. base_prompt + notebook tools are
loop-owned; the skill contributes only system_prompt + tools.
"""

from __future__ import annotations

import json
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from .dispatcher import dispatch

# ---------------------------------------------------------------------------
# loop-owned prompt + notebook tools (rag_design.md §2 / §5)
# ---------------------------------------------------------------------------

LOOP_BASE_PROMPT = """你是运行在通用 agent loop 中的助手。你可以调用工具完成用户任务;工具结果会以 JSON 返回,请基于结果继续推理,直到任务完成。

你有一个跨会话的"笔记本"(持久化记忆)工具,用于记录与复用经验:
- 记录(write_note):完成重要判断、发现可复用经验或教训、改主意时,用一句话记下"什么情况 → 做了什么 → 结果/理由"。反直觉案例(结果与直觉相反但正确的)尤其值得记。
- 查询(retrieve_notes):开始新任务、做重要判断前、遇到异常/未知情形、准备重试或调参前,先查历史笔记,看是否有可借鉴的处理方式。
- 笔记可能来自过去的会话(含失败会话),仅供参考,必须结合当前数据独立判断,不要盲从。

规则:
- 每次只做当前需要的一步:需要更多信息就调用工具,信息足够就给出最终回答;不要无谓地反复调用工具。
- 工具返回 {"status":"error",...} 时,根据错误信息决定是否重试或换一种方式;不要原样重复同一失败调用。
- 最终回答用中文,直接面向用户,包含关键结论。"""

NOTEBOOK_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "write_note",
            "description": (
                "在跨会话笔记本中写一条笔记(经验/判断/教训)。笔记持久化,"
                "后续会话可用 retrieve_notes 检索到。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "笔记正文。建议句式:'什么情况 → 做了什么 → 结果/理由',"
                            "便于日后检索与复用。"
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选标签,便于按类检索,如数据集名、事件类型、器官等。",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_notes",
            "description": (
                "按语义相似度检索历史笔记。做重要判断前、遇到异常/未知情况、"
                "开始新任务时建议查询。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索意图,用当前情境描述(指标形态/遇到的问题/正要做的决定)。",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选,限定在带这些标签的笔记里检索。",
                    },
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
]

NOTEBOOK_TOOL_RUNTIME = {
    "write_note": {"type": "builtin", "name": "write_note"},
    "retrieve_notes": {"type": "builtin", "name": "retrieve_notes"},
}


# ---------------------------------------------------------------------------
# LangGraph state + nodes (loop_design.md §5.1 / §5.2)
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    base_prompt: str            # loop generic prompt (with notebook guidance)
    system_prompt: str          # skill system_prompt
    tool_schemas: list[dict]    # skill tools + notebook tools merged
    tool_runtime: dict          # skill tools + notebook tools merged
    project_dir: str
    notes_path: str             # notebook path (RAG_NOTES_DIR or <project>/notes.jsonl)
    session_id: str


def _call_model(state: AgentState, llm) -> dict:
    """Invoke the LLM with the accumulated messages and the tool schemas."""
    response = llm.bind_tools(state["tool_schemas"]).invoke(state["messages"])
    return {"messages": [response]}


def _should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


def _call_tools(state: AgentState) -> dict:
    """Execute every tool call of the last assistant message."""
    results = []
    last = state["messages"][-1]
    for tool_call in last.tool_calls:
        name = tool_call["name"]
        spec = state["tool_runtime"].get(name)
        if spec is None:
            result = {"status": "error", "error": f"unknown tool: {name}"}
        else:
            try:
                result = dispatch(spec, tool_call["args"], state)
            except Exception as exc:  # last-resort guard; dispatcher already catches most
                result = {"status": "error", "error": f"{name} raised: {exc}"}
        results.append(
            ToolMessage(content=json.dumps(result, ensure_ascii=False), tool_call_id=tool_call["id"])
        )
    return {"messages": results}


def build_workflow(llm):
    """Compile the LangGraph StateGraph (agent -> tools -> ... -> END).

    Args:
        llm: any LangChain chat model supporting bind_tools().

    Returns:
        Compiled graph, invoked with an AgentState dict.
    """
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", lambda state: _call_model(state, llm))
    workflow.add_node("tools", _call_tools)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")
    return workflow.compile()
