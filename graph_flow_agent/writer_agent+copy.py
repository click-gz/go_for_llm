import asyncio
from langgraph.graph import StateGraph, END
from loguru import logger
import os
from dotenv import load_dotenv
from openai import OpenAI
from typing import TypedDict, List, Dict, Any, Optional
from agent_tools import mcp as mcp_client
from fastmcp import Client
import json
import functools

# 加载 .env 配置
load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")

# 日志配置
logger.add("writer_agent.log", rotation="1 MB")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_API_BASE)

# 假设有一个简单的 LLM 调用函数
# openai.api_key = DEEPSEEK_API_KEY
# openai.api_base = DEEPSEEK_API_BASE

def call_llm(prompt, tools=None):
    # 保持同步接口兼容
    return asyncio.run(call_llm_async(prompt, tools))

async def call_llm_async(prompt, tools=None):
    logger.info(f"调用Deepseek V3: {prompt}")
    try:
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ]
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


# ===== 动态编排支持工具调用 =====
async def get_tools_info():
    # 获取 FunctionTool 对象列表
    tool_objs = await mcp_client._list_tools()
    tools_info = []
    for tool in tool_objs:
        # 你可以根据 FunctionTool 的属性自定义导出
        tools_info.append({
            "name": tool.name,
            "description": tool.description or "",
            # 这里假设所有工具都只接受 input_text:str
            "parameters": {
                "type": "object",
                "properties": {
                    "input_text": {"type": "string", "description": "输入文本"}
                },
                "required": ["input_text"]
            }
        })
    return tools_info

def tools_info_to_openai_tools(tools_info):
    # 转换为 OpenAI function-calling 规范
    openai_tools = []
    for tool in tools_info:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input_text": {"type": "string", "description": "输入文本"}
                    },
                    "required": ["input_text"]
                }
            }
        })
    return openai_tools

async def get_dynamic_plan(user_query, tools_info):
    prompt = f"""
你是一个智能流程编排Agent。你可以调用如下工具，每个工具有唯一name、功能描述、输入输出说明：
{json.dumps(tools_info, ensure_ascii=False, indent=2)}

请根据用户需求“{user_query}”，输出一个结构化的任务编排JSON，要求每个节点包含如下字段：
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
    openai_tools = tools_info_to_openai_tools(tools_info) if tools_info else None
    llm_result = await call_llm_async(prompt, tools=openai_tools)
    plan_json = llm_result["content"] if llm_result else None
    if plan_json is None:
        raise ValueError("LLM未返回编排JSON")
    plan_json = plan_json.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(plan_json)

# glue 层支持 input_map/output_field，详细日志
import types

def apply_transform(value, transform):
    # 简单支持常见 transform，可扩展
    if not transform:
        return value
    if transform == "join":
        if isinstance(value, list):
            return "\n".join(value)
    if transform == "str":
        return str(value)
    # 你可以扩展更多 transform 规则
    return value

# 更宽泛的全局状态类型
class FullState(TypedDict, total=False):
    text: str
    sections: Optional[list]
    contents: Optional[list]
    user_input: Optional[str]
    outline: Optional[str]
    split_outline: Optional[list]
    sub_outlines: Optional[list]
    content: Optional[str]
    chart: Optional[str]

def build_dynamic_graph(plan, client):
    g = StateGraph(FullState)
    for node in plan["nodes"]:
        def make_node(tool_name, input_map=None, output_field="text", node_desc=None):
            async def node_fn(state):
                logger.info(f"[动态节点:{tool_name}] node_fn 收到的 state: {state}, 类型: {type(state)}")
                try:
                    logger.info(f"[动态节点:{tool_name}] input_map: {input_map}, output_field: {output_field}")
                    params = {}
                    # 参数提取
                    for param, rule in (input_map or {}).items():
                        value = state.get(rule.get("from"))
                        logger.info(f"[动态节点:{tool_name}] param: {param}, from: {rule.get('from')}, raw_value: {value}")
                        if value is None:
                            logger.warning(f"[动态节点:{tool_name}] 参数 {param} 来源 {rule.get('from')} 为空")
                        # 批量处理：如果 value 是 list 且 index 未指定或为 None，则循环调用工具
                        if isinstance(value, list) and ("index" not in rule or rule["index"] is None):
                            logger.info(f"[动态节点:{tool_name}] param: {param} 触发批量 glue，循环调用工具")
                            results = []
                            for item in value:
                                single_params = params.copy()
                                single_params[param] = item
                                logger.info(f"[动态节点:{tool_name}] 批量调用工具 {tool_name}，参数: {single_params}")
                                result = await client.call_tool(tool_name, single_params)
                                output = result.data if hasattr(result, 'data') else result
                                results.append(output)
                            logger.info(f"[动态节点:{tool_name}] 批量输出: {results}")
                            # 聚合结果
                            ret = {**state, output_field: results, "text": "\n".join(results)}
                            logger.info(f"[动态节点:{tool_name}] 批量 glue 返回: {ret}")
                            logger.info(f"[动态节点:{tool_name}] 返回后 state 预览: {ret}")
                            return ret
                        # 只有 value 是 list 且 index 有值时才取 index，否则直接用原值
                        if "index" in rule and rule["index"] is not None:
                            if isinstance(value, list):
                                try:
                                    value = value[rule["index"]]
                                    logger.info(f"[动态节点:{tool_name}] param: {param}, index: {rule['index']}, indexed_value: {value}")
                                except Exception as e:
                                    logger.error(f"[动态节点:{tool_name}] param: {param}, index: {rule['index']} 取值失败: {e}")
                            else:
                                logger.info(f"[动态节点:{tool_name}] param: {param}, index: {rule['index']}，但 value 不是 list，直接用原值: {value}")
                        if "transform" in rule and rule["transform"]:
                            value = apply_transform(value, rule["transform"])
                            logger.info(f"[动态节点:{tool_name}] param: {param}, transform: {rule['transform']}, transformed_value: {value}")
                        params[param] = value
                    logger.info(f"[动态节点:{tool_name}] 最终工具参数: {params}")
                    # 检查参数是否有 None
                    if any(v is None for v in params.values()):
                        logger.error(f"[动态节点:{tool_name}] 有参数为 None，跳过工具调用，params: {params}")
                        ret = {**state, output_field: f"[Fallback] {tool_name} 有参数为 None: {params}", "text": f"[Fallback] {tool_name} 有参数为 None: {params}"}
                        logger.info(f"[动态节点:{tool_name}] Fallback 返回后 state 预览: {ret}")
                        return ret
                    # 工具调用
                    logger.info(f"[动态节点:{tool_name}] 开始调用工具 {tool_name}，参数: {params}")
                    result = await client.call_tool(tool_name, params)
                    output = result.data if hasattr(result, 'data') else result
                    logger.info(f"[动态节点:{tool_name}] 工具输出: {repr(output)}")
                    # 检查 output_field 和下游 input_map 是否一一对应
                    if output_field not in state or any(
                        output_field == n.get("input_map", {}).get("from") for n in plan.get("nodes", []) if n.get("name") != node_desc):
                        logger.info(f"[动态节点:{tool_name}] output_field {output_field} 将用于下游节点 input_map")
                    else:
                        logger.warning(f"[动态节点:{tool_name}] output_field {output_field} 可能未被下游节点引用，请检查编排")
                    logger.info(f"[动态节点:{tool_name}] 返回: {output_field}={repr(output)}, text={repr(output)}")
                    ret = {**state, output_field: output, "text": output}
                    logger.info(f"[动态节点:{tool_name}] 返回后 state 预览: {ret}")
                    return ret
                except Exception as e:
                    logger.error(f"[动态节点:{tool_name}] 失败: {e}")
                    ret = {**state, output_field: f"[Fallback] {tool_name} 节点失败: {e}", "text": f"[Fallback] {tool_name} 节点失败: {e}"}
                    logger.info(f"[动态节点:{tool_name}] Exception Fallback 返回后 state 预览: {ret}")
                    return ret
            return node_fn
        g.add_node(
            node["name"],
            make_node(
                node["tool"],
                node.get("input_map", {}),
                node.get("output_field", "text"),
                node.get("desc")
            )
        )
    for edge in plan["edges"]:
        g.add_edge(edge["from"], edge["to"])
    g.set_entry_point(plan["entry"])
    g.set_finish_point(plan["finish"])
    return g.compile()

class StageNode:
    def __init__(self, name, desc, tool: Optional[str] = None):
        self.name = name
        self.desc = desc
        self.tool = tool

    async def run(self, input_text, client=None):
        logger.info(f"[{self.name}] 开始: {self.desc}")
        print(f"[{self.name}] 开始: {self.desc}")
        try:
            if self.tool and client is not None:
                result = await client.call_tool(self.tool, {"input_text": input_text})
                output = result.data if hasattr(result, 'data') else result
            elif self.tool:
                # fallback: 没有 client 时直接 LLM
                llm_result = await call_llm_async(f"{self.desc}: {input_text}")
                output = llm_result["content"] if llm_result else ""
            else:
                llm_result = await call_llm_async(f"{self.desc}: {input_text}")
                output = llm_result["content"] if llm_result else ""
            logger.info(f"[{self.name}] 结束: {output}")
            print(f"[{self.name}] 结束: {output}")
            return output
        except Exception as e:
            logger.error(f"[{self.name}] 失败: {e}")
            print(f"[{self.name}] 失败，尝试 fallback")
            return self.fallback(input_text)

    def fallback(self, input_text):
        # fallback 策略：简单返回原文+提示
        fallback_text = f"[Fallback] {self.name} 阶段失败，返回原文: {input_text}"
        logger.info(fallback_text)
        return fallback_text

    def reflect(self, output_text):
        # 简单反思：检查输出是否为空
        if not output_text.strip():
            logger.warning(f"[{self.name}] 输出为空，触发 fallback")
            return False
        return True

class AgentState(TypedDict):
    text: str
    sections: Optional[list]
    contents: Optional[list]

class WritingAgent:
    def __init__(self):
        self.graph = None

    async def run(self, query, tools_info=None):
        logger.info(f"[Agent] 开始 run，query: {query}")
        if tools_info is None:
            tools_info = await get_tools_info()
        logger.info(f"[Agent] 获取到 tools_info: {tools_info}")
        # plan = await get_dynamic_plan(query, tools_info)
        plan = {'nodes': [{'name': 'generate_outline', 'tool': 'outline_generator', 'desc': '生成关于LangGraph的文章大纲', 'input_map': {'input_text': {'from': 'user_input', 'index': 0, 'transform': None}}, 'output_field': 'outline'}, {'name': 'split_outline', 'tool': 'outline_splitter', 'desc': '解析大纲并拆分为二级大纲', 'input_map': {'input_text': {'from': 'outline', 'index': 0, 'transform': None}}, 'output_field': 'split_outline'}, {'name': 'generate_content', 'tool': 'content_generator', 'desc': '根据二级大纲生成详细内容', 'input_map': {'input_text': {'from': 'split_outline', 'index': None, 'transform': None}}, 'output_field': 'content'}, {'name': 'generate_chart', 'tool': 'chart_generator', 'desc': '生成相关表格信息', 'input_map': {'input_text': {'from': 'content', 'index': 0, 'transform': None}}, 'output_field': 'chart'}], 'edges': [{'from': 'generate_outline', 'to': 'split_outline'}, {'from': 'split_outline', 'to': 'generate_content'}, {'from': 'generate_content', 'to': 'generate_chart'}], 'entry': 'generate_outline', 'finish': 'generate_chart'}
        logger.info(f"动态编排结果: {plan}")
        async with Client(mcp_client) as client:
            self.graph = build_dynamic_graph(plan, client)
            logger.info(f"开始执行动态DAG")
            print("开始执行动态DAG")
            if self.graph is None:
                logger.error("DAG 未正确构建，graph 为 None")
                print("DAG 未正确构建，graph 为 None")
                return "[DAG 执行失败]"
            # # ====== 自动初始化 state 字段 ======
            # state_fields = set()
            # for node in plan.get("nodes", []):
            #     input_map = node.get("input_map", {})
            #     for rule in input_map.values():
            #         if rule.get("from"):
            #             state_fields.add(rule["from"])
            # logger.info(f"自动收集到的 state_fields: {state_fields}")
            state: FullState = {"text": query, "sections": None, "contents": None}
            # for field in state_fields:
            #     if field in ("text", "user_input"):
            #         state[field] = query
            #     else:
            #         state[field] = None
            state["user_input"] = query
            state["text"] = query
            logger.info(f"初始化后完整 state: {state}")
            logger.info(f"将要传递给 AgentState 的字段: text={state.get('text')}, sections={state.get('sections')}, contents={state.get('contents')}")
            init_state: FullState = state
            logger.info(f"最终传递给 graph 的 state: {init_state}, 类型: {type(init_state)}")
            if hasattr(self.graph, 'ainvoke'):
                logger.info(f"[Agent] 调用 graph.ainvoke ...")
                result = await self.graph.ainvoke(init_state)
            elif hasattr(self.graph, 'invoke'):
                logger.info(f"[Agent] 调用 graph.invoke ...")
                result = self.graph.invoke(init_state)
            else:
                logger.error("graph 对象不支持 ainvoke/invoke 方法。请检查 langgraph 版本。")
                print("graph 对象不支持 ainvoke/invoke 方法。请检查 langgraph 版本。")
                return "[DAG 执行失败]"
            logger.info(f"DAG 执行结束: {result.get('text')}")
            logger.info(f"DAG 执行结束后最终 state: {result}")
            print(f"DAG 执行结束: {result.get('text')}")
            return result.get('text')

if __name__ == "__main__":
    agent = WritingAgent()
    default_query = "请写一篇关于LangGraph的文章"
    # user_query = input(f"请输入写作需求（直接回车将使用默认内容: {default_query}）: ")
    # if not user_query.strip():
    #     user_query = default_query
    
    user_query = default_query
    asyncio.run(agent.run(user_query))


    # # 单独测试 llm
    # prompt = "请写一篇关于LangGraph的文章"
    # result = call_llm(prompt)
    # print(result)