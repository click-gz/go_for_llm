import asyncio
import os
import json
from typing import Any, Dict, Optional
from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI
from agent_tools import mcp as mcp_client
from fastmcp import Client
from langgraph.graph import StateGraph

# 日志配置
logger.add("writer_agent.log", rotation="1 MB")

# 加载 .env 配置
load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_API_BASE)

async def call_llm_async(prompt, tools=None):
    logger.info(f"调用Deepseek V3: {prompt}")
    import functools
    import asyncio
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt}
    ]
    try:
        if tools:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                functools.partial(
                    client.chat.completions.create,
                    model="deepseek-chat",
                    messages=messages,
                    stream=False,
                    tools=tools,
                    tool_choice="auto"
                )
            )
        else:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                functools.partial(
                    client.chat.completions.create,
                    model="deepseek-chat",
                    messages=messages,
                    stream=False
                )
            )
        content = response.choices[0].message.content
        return {"content": content}
    except Exception as e:
        logger.error(f"Deepseek V3 调用失败: {e}")
        return {"content": f"[LLM调用失败] {prompt}"}

# ===================== 1. query -> 编排DAG =====================
async def get_tools_info() -> list:
    """获取所有可用工具的元信息，供 LLM 编排用（mcp/fastmcp 规范）"""
    tool_objs = await mcp_client._list_tools()
    tools_info = []
    for tool in tool_objs:
        info = {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.parameters,  # 直接用工具自带的 JSON Schema
        }
        if hasattr(tool, "tags"):
            info["tags"] = tool.tags
        if hasattr(tool, "annotations"):
            info["annotations"] = tool.annotations
        tools_info.append(info)
        logger.info(f"[get_tools_info] 工具: {info}")
    return tools_info

async def plan_dag(query: str, tools_info: list) -> dict:
    """
    调用 LLM 进行流程编排，返回 DAG 计划(plan)
    """
    prompt = f"""
你是一个智能流程编排Agent。你可以调用如下工具，每个工具有唯一name、功能描述、参数schema：
{json.dumps(tools_info, ensure_ascii=False, indent=2)}

请根据用户需求“{query}”，输出一个结构化的任务编排JSON，要求每个节点包含如下字段：
- name: 节点名
- tool: 工具名
- desc: 节点描述
- input_map: 工具参数与上游 state 字段的映射关系，支持 from、index、transform 等
- output_field: 本节点输出写入 state 的字段名（可选）

JSON 格式如下：
{{
  "nodes": [
    {{
      "name": "节点名",
      "tool": "工具名",
      "desc": "节点描述",
      "input_map": {{
        "参数名": {{"from": "state字段", "index": 0, "transform": null}}
      }},
      "output_field": "字段名"
    }}
  ],
  "edges": [
    {{"from": "节点名", "to": "节点名"}}
  ],
  "entry": "入口节点名",
  "finish": "出口节点名"
}}
只输出JSON，不要多余解释。
"""
    logger.info(f"[plan_dag] prompt: {prompt}")
    try:
        llm_result = await call_llm_async(prompt)
        content = llm_result["content"] if llm_result else None
        logger.info(f"[plan_dag] LLM 返回: {content}")
        if not content:
            raise RuntimeError("plan_dag LLM 返回为空")
        # 去除 markdown 代码块包裹
        plan_json = content.strip().removeprefix("```json").removesuffix("```").strip()
        plan = json.loads(plan_json)
        logger.info(f"[plan_dag] 解析后 plan: {plan}")
        return plan
    except Exception as e:
        logger.error(f"[plan_dag] LLM 编排失败: {e}")
        raise RuntimeError(f"plan_dag LLM 编排失败: {e}")

# ===================== 2. DAG -> 执行结果 =====================
def build_dynamic_graph(plan: dict, client: Any) -> Any:
    """根据 plan 构建 StateGraph DAG"""
    # TODO: 实现 DAG 构建
    return None

async def execute_dag(graph: Any, init_state: dict) -> dict:
    """执行 DAG，返回最终 state"""
    # TODO: 实现 DAG 执行
    return {}
    

# ===================== 3. 结果输出 =====================
def output_result(final_state: dict) -> Any:
    """处理和输出最终结果"""
    # TODO: 实现结果输出
    return final_state.get("text", "")

# ===================== 主入口 =====================
class WritingAgent:
    def __init__(self):
        self.graph = None

    async def run(self, query: str) -> Any:
        logger.info(f"[Agent] 开始 run，query: {query}")
        # 1. 获取工具信息
        tools_info = await get_tools_info()
        logger.info(f"[Agent] 获取到 tools_info: {tools_info}")
        # 2. 编排 DAG
        plan = await plan_dag(query, tools_info)
        logger.info(f"[Agent] 编排得到 plan: {plan}")
        # 3. 构建 DAG
        async with Client(mcp_client) as client:
            self.graph = build_dynamic_graph(plan, client)
            logger.info(f"[Agent] 构建 DAG 完成")
            # 4. 初始化 state
            init_state = {"text": query, "user_input": query}
            # 5. 执行 DAG
            final_state = await execute_dag(self.graph, init_state)
            logger.info(f"[Agent] DAG 执行结束，最终 state: {final_state}")
            # 6. 输出结果
            return output_result(final_state)

if __name__ == "__main__":
    agent = WritingAgent()
    default_query = "请写一篇关于LangGraph的文章"
    user_query = default_query
    asyncio.run(agent.run(user_query))