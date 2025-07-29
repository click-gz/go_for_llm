import asyncio
import os
import json
from typing import Any, Dict, Optional, TypedDict
from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI
# from agent_tools import mcp as mcp_client
from fastmcp import Client
from langgraph.graph import StateGraph
from pydantic import create_model
import time
from mock import MockClient

"""
场景	说明/建议字段
条件分支	condition/edges 支持条件
多输入/多输出	input_map/output_field 多参数
产物聚合	glue 层支持聚合操作
节点依赖	edges 支持多对一/一对多
重试/超时/限流	retry/timeout 字段
上下文注入	context 字段
类型校验	schema/type 字段
日志可观测	log_level/trace 字段
动态扩展	支持热插拔/动态变更
终止/中断	支持 cancel/abort
"""

# 日志配置
logger.add("writer_agent.log", rotation="1 MB")

# 加载 .env 配置
load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://qianfan.baidubce.com/v2")
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_API_BASE)

async def call_llm_async(prompt, tools=None):
    """
    调用 Deepseek/OpenAI LLM，支持 function-calling 工具参数
    prompt: 用户/系统输入的提示词
    tools: 可选，function-calling 工具列表
    返回: {"content": LLM输出内容}
    """
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
    """
    获取所有可用工具的元信息，供 LLM 编排用（mcp/fastmcp 规范）
    - 直接读取每个工具的 parameters 字段（JSON Schema）
    - 支持 tags/annotations 字段
    - 日志记录完整工具元信息
    - 返回标准 mcp 工具元信息 list
    """
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
    调用 LLM 进行流程编排，返回 DAG 计划(plan)，支持 glue 相关字段和产物作用域（scope）
    """
    # ===== 构造 LLM prompt，要求输出包含 glue 相关字段和 scope 字段的结构化 JSON 编排 =====
    prompt = f"""
    你是一个智能流程编排Agent。你可以调用如下工具，每个工具有唯一name、功能描述、参数schema：
    {repr(tools_info)}

    请根据用户需求“{query}”，输出一个结构化的任务编排JSON，要求每个节点包含如下字段：
    - name: 节点名
    - tool: 工具名（如为 glue/聚合节点可省略）
    - desc: 节点描述
    - input_map: 工具参数与上游 state 字段的映射关系，支持如下配置：
        - from: 来源 state 字段名
        - index: 如果来源是 list，可指定下标
        - transform: 可选，常见如 join/str/json
        - batch: 是否对 list 自动循环 glue（true/false）
        - concurrent: 是否并发执行（true/false）
    - output_field: 本节点输出写入 state 的字段名（可选）
    - scope: 产物作用域，global=写入全局state，local=仅节点间传递
    - fallback: 可选，失败时切换到的备用节点名
    - condition: 可选，条件分支表达式
    - store_to_globals: 可选，指定哪些产物需要写入全局 state 的字段名列表

    JSON 格式如下：
    {{
    "nodes": [
        {{
        "name": "节点名",
        "tool": "工具名",
        "desc": "节点描述",
        "input_map": {{
            "参数名": {{"from": "state字段", "index": 0, "transform": null, "batch": false, "concurrent": false}}
        }},
        "output_field": "字段名",
        "scope": "global",
        "fallback": "备用节点名",
        "condition": "表达式",
        "store_to_globals": ["字段名"]
        }}
    ],
    "edges": [
        {{"from": "节点名", "to": "节点名"}}
    ],
    "entry": "入口节点名",
    "finish": "出口节点名"
    }}
    注意：只有 scope=global 的产物才会写入全局 state，scope=local 的产物仅在本节点与下游节点间传递。
    只输出JSON，不要多余解释。
    """
    logger.info(f"[plan_dag] prompt: {prompt}")
    try:
        llm_result = await call_llm_async(prompt)
        content = llm_result["content"] if llm_result else None
        logger.info(f"[plan_dag] LLM 返回: {content}")
        if not content:
            raise RuntimeError("plan_dag LLM 返回为空")
        # ===== 去除 markdown 代码块包裹，解析为 JSON =====
        plan_json = content.strip().removeprefix("```json").removesuffix("```").strip()
        plan = json.loads(plan_json)
        logger.info(f"[plan_dag] 解析后 plan: {plan}")
        return plan
    except Exception as e:
        logger.error(f"[plan_dag] LLM 编排失败: {e}")
        raise RuntimeError(f"plan_dag LLM 编排失败: {e}")


def apply_transform(value, transform):
    """
    glue 层参数格式转换器
    - 根据 transform 字段自动处理参数类型/格式
    - 支持 join/str 等常见转换，可扩展更多规则
    - 用于 glue 层自动适配不同节点/工具的输入要求
    """
    if not transform:
        return value
    if transform == "join":
        if isinstance(value, list):
            return "\n".join(value)
    if transform == "str":
        return str(value)
    if transform == "dict":
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
    if transform == "list":
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str):
            import ast
            try:
                result = ast.literal_eval(value)
                if isinstance(result, list):
                    return result
                elif isinstance(result, tuple):
                    return list(result)
            except Exception as e:
                logger.warning(f"ast.literal_eval failed: {e}")
            # 智能分隔符识别
            if ',' in value:
                return [x.strip() for x in value.split(',') if x.strip()]
            elif '\n' in value:
                return [x.strip() for x in value.split('\n') if x.strip()]
            elif ';' in value:
                return [x.strip() for x in value.split(';') if x.strip()]
    # 可扩展更多 transform 规则，如 json、sum、mean 等
    return value

# 兼容 dict 和 pydantic model 的字段访问
def get_state_value(state, key):
    if isinstance(state, dict):
        return state.get(key)
    return getattr(state, key, None)

def build_dynamic_graph(plan: dict, client: Any, state_schema=dict) -> Any:
    """
    根据 plan 构建 StateGraph DAG，支持 glue 相关字段和全局中间变量存储（_globals）
    - state_schema 默认为 dict，支持任意 key 动态扩展
    """
    from langgraph.graph import StateGraph
    g = StateGraph(state_schema)

    for node in plan["nodes"]:
        def make_node(tool_name=None, input_map=None, output_field="text", node_desc=None, fallback=None, condition=None, scope="global", store_to_globals=None):
            async def node_fn(state):
                logger.info(f"[动态节点:{tool_name}] node_fn 收到的 state: {state}")
                try:
                    params = {}
                    batch_mode = False
                    batch_param = None
                    batch_value = None
                    # ===== glue 层参数提取与处理 =====
                    for param, rule in (input_map or {}).items():
                        # 优先从 _globals 获取全局变量，其次从 state 获取临时变量
                        globals_dict = get_state_value(state, "_globals") or {}
                        value = globals_dict.get(rule.get("from"))
                        
                        if value is None:
                            value = get_state_value(state, rule.get("from"))
                        
                        # todo: value 为空，异常处理：1. 中断执行并返回错误信息
                                                # 2. llm通过全局上下文，补充value
                                                # 3. 重回上一步
                        

                        # glue: transform，自动格式转换
                        if "transform" in rule and rule["transform"]:
                            value = apply_transform(value, rule["transform"])

                        #todo: transform 失败处理


                        # glue: index，list 下标取值
                        if "index" in rule and rule["index"] is not None:
                            if isinstance(value, list):
                                value = value[rule["index"]]
                                
                        # glue: batch（批量处理，自动循环 glue）
                        if rule.get("batch") and isinstance(value, list):
                            batch_mode = True
                            batch_param = param
                            batch_value = value
                        else:
                            params[param] = value
                    # ===== glue/聚合节点（无 tool 字段） =====
                    if tool_name is None:
                        logger.info(f"[动态节点:GLUE] 聚合节点，直接聚合参数: {params}")
                        safe_params = {k: (v if v is not None else "") for k, v in params.items()}
                        if len(safe_params) == 1:
                            output = list(safe_params.values())[0]
                        else:
                            try:
                                output = "\n".join(str(v) for v in safe_params.values())
                            except Exception:
                                output = safe_params
                        if not output or output in ["", [], None]:
                            output = "[GLUE聚合无有效内容]"
                        ret = dict(state)
                        ret[output_field] = output
                        ret["text"] = output
                        # 显式存入全局变量
                        if store_to_globals:
                            for field in store_to_globals:
                                ret.setdefault("_globals", {})[field] = output if field == output_field else None
                        logger.info(f"[动态节点:GLUE] 返回: {ret}")
                        return ret
                    # ===== 批量 glue 处理 =====
                    if batch_mode and batch_param and batch_value:
                        async def call_single(item):
                            single_params = params.copy()
                            single_params[batch_param] = item
                            try:
                                result = await client.call_tool(tool_name, single_params)
                                output = result.data if hasattr(result, 'data') else result
                                return output
                            except Exception as e:
                                logger.error(f"[动态节点:{tool_name}] 批量调用失败: {e}")
                                if fallback:
                                    logger.info(f"[动态节点:{tool_name}] 触发 fallback: {fallback}")
                                    return f"[Fallback] {tool_name} 节点失败: {e}"
                                else:
                                    return f"[Exception] {tool_name} 节点异常: {e}"
                        results = await asyncio.gather(*(call_single(item) for item in batch_value))
                        # 聚合所有结果后只写入一次 output_field，避免并发写入冲突
                        ret = dict(state)
                        ret[output_field] = results
                        ret["text"] = results
                        if store_to_globals:
                            for field in store_to_globals:
                                ret.setdefault("_globals", {})[field] = results if field == output_field else None
                        logger.info(f"[动态节点:{tool_name}] 批量 glue 返回: {ret}")
                        return ret
                    # ===== 工具节点参数为 None 时 fallback =====
                    if any(v is None for v in params.values()):
                        logger.error(f"[动态节点:{tool_name}] 有参数为 None，跳过工具调用，params: {params}")
                        fallback_val = f"[Fallback] {tool_name} 有参数为 None: {params}"
                        ret = dict(state)
                        ret[output_field] = fallback_val
                        ret["text"] = fallback_val
                        if store_to_globals:
                            for field in store_to_globals:
                                ret.setdefault("_globals", {})[field] = fallback_val if field == output_field else None
                        return ret
                    # ===== 单次 glue 及异常 fallback =====
                    try:
                        logger.info(f"[动态节点:{tool_name}] 调用工具: {tool_name}，参数: {params}")
                        result = await client.call_tool(tool_name, params)
                        output = result.data if hasattr(result, 'data') else result
                        ret = dict(state)
                        ret[output_field] = output
                        ret["text"] = output
                        if store_to_globals:
                            for field in store_to_globals:
                                ret.setdefault("_globals", {})[field] = output if field == output_field else None
                        logger.info(f"[动态节点:{tool_name}] 返回: {ret}")
                        return ret
                    except Exception as e:
                        logger.error(f"[动态节点:{tool_name}] 工具调用失败: {e}")
                        fallback_val = f"[Fallback] {tool_name} 节点失败: {e}"
                        ret = dict(state)
                        ret[output_field] = fallback_val
                        ret["text"] = fallback_val
                        if store_to_globals:
                            for field in store_to_globals:
                                ret.setdefault("_globals", {})[field] = fallback_val if field == output_field else None
                        return ret
                except Exception as e:
                    logger.error(f"[动态节点:{tool_name}] 节点异常: {e}")
                    fallback_val = f"[Exception] {tool_name} 节点异常: {e}"
                    ret = dict(state or {})
                    ret[output_field] = fallback_val
                    ret["text"] = fallback_val
                    if store_to_globals:
                        for field in store_to_globals:
                            ret.setdefault("_globals", {})[field] = fallback_val if field == output_field else None
                    return ret
            return node_fn

        g.add_node(
            node["name"],
            make_node(
                node.get("tool"),  # 允许 tool=None
                node.get("input_map", {}),
                node.get("output_field", "text"),
                node.get("desc"),
                node.get("fallback"),
                node.get("condition"),
                node.get("scope", "global"),
                node.get("store_to_globals", [])
            )
        )

    # ===== 注册 DAG 边、入口、出口 =====
    for edge in plan["edges"]:
        g.add_edge(edge["from"], edge["to"])
    g.set_entry_point(plan["entry"])
    g.set_finish_point(plan["finish"])
    return g.compile()

async def execute_dag(graph: Any, init_state: dict) -> dict:
    """
    执行 DAG，返回最终 state
    - 支持异步执行
    - 详细日志记录 DAG 执行过程和最终产物
    """
    logger.info(f"[execute_dag] 开始执行 DAG，初始 state: {init_state}")
    start_time = time.time()
    if hasattr(graph, 'ainvoke'):
        logger.info(f"[execute_dag] 调用 graph.ainvoke ...")
        result = await graph.ainvoke(init_state)
    elif hasattr(graph, 'invoke'):
        logger.info(f"[execute_dag] 调用 graph.invoke ...")
        result = graph.invoke(init_state)
    else:
        logger.error("graph 对象不支持 ainvoke/invoke 方法。请检查 langgraph 版本。")
        raise RuntimeError("[DAG 执行失败]")
    elapsed = time.time() - start_time
    logger.info(f"[execute_dag] DAG 执行结束，最终 state: {result}")
    logger.info(f"[execute_dag] DAG 执行耗时: {elapsed:.2f} 秒")
    return result

# ===================== 3. 结果输出 =====================
def output_result(final_state: dict) -> Any:
    """
    处理和输出最终结果
    - 可根据实际需求输出全文、结构化产物、日志等
    - 默认输出 state["text"] 字段
    """
    logger.info(f"[output_result] 最终产物: {final_state}")
    result = final_state.get("text", "")
    if isinstance(result, list):
        # 用分隔线分割每个部分，便于查看
        return "\n\n------ 分割线 ------\n\n".join(str(x) for x in result)
    return result

# ===================== 主入口 =====================
class WritingAgent:
    def __init__(self):
        self.graph = None

    async def run(self, query: str) -> Any:
        """
        写作 Agent 主流程：
        1. 获取工具元信息
        2. LLM 编排生成 DAG 计划
        3. 构建 StateGraph DAG
        4. 初始化 state
        5. 执行 DAG
        6. 输出最终结果
        """
        logger.info(f"[Agent] 开始 run，query: {query}")
        total_start = time.time()
        # 1. 获取工具信息
        # tools_info = await get_tools_info()
        # logger.info(f"[Agent] 获取到 tools_info: {tools_info}")
        # 2. 编排 DAG
        # plan = await plan_dag(query, tools_info)
        plan = {
    "nodes": [
        {
            "name": "generate_outline",
            "tool": "outline_generator",
            "desc": "生成关于LangGraph的文章大纲",
            "input_map": {
                "input_text": {"from": "user_input", "transform": "str", "batch": False, "concurrent": False}
            },
            "output_field": "outline",
            "store_to_globals": ["outline"]
        },
        {
            "name": "split_outline",
            "tool": "outline_splitter",
            "desc": "解析Markdown大纲，拆分为二级大纲及其内容列表",
            "input_map": {
                "input_text": {"from": "outline", "transform": "str", "batch": False, "concurrent": False}
            },
            "output_field": "sections",
            "store_to_globals": []
        },
        {
            "name": "generate_content",
            "tool": "content_generator",
            "desc": "根据二级大纲生成详细的Markdown正文片段",
            "input_map": {
                "input_text": {"from": "sections", "batch": True, "concurrent": True}
            },
            "output_field": "content_parts",
            "store_to_globals": ["content_parts"]
        },
        {
            "name": "generate_chart",
            "tool": "chart_generator",
            "desc": "生成适合的Markdown表格，展示LangGraph的核心特性",
            "input_map": {
                "input_text": {"from": "user_input", "transform": "str", "batch": False, "concurrent": False}
            },
            "output_field": "chart",
            "store_to_globals": ["chart"]
        },
        {
            "name": "combine_content",
            "desc": "合并所有生成的内容和图表",
            "input_map": {
                "content_parts": {"from": "content_parts", "transform": "join", "batch": False, "concurrent": False},
                "chart": {"from": "chart", "transform": "str", "batch": False, "concurrent": False}
            },
            "output_field": "final_content",
            "store_to_globals": ["final_content"]
        }
    ],
    "edges": [
        {"from": "generate_outline", "to": "split_outline"},
        {"from": "split_outline", "to": "generate_content"},
        {"from": "generate_content", "to": "generate_chart"},
        {"from": "generate_chart", "to": "combine_content"}
    ],
    "entry": "generate_outline",
    "finish": "combine_content"
}
        logger.info(f"[Agent] 编排得到 plan: {plan}")
        # 动态生成 pydantic DynamicState
        global_vars = set()
        for node in plan["nodes"]:
            for field in node.get("store_to_globals", []):
                global_vars.add(field)
        # 字段类型全部为 str，默认 None
        field_defs = {k: (str, None) for k in global_vars}
        # DynamicState = create_model("DynamicState", **field_defs)
        # 3. 构建 DAG
        # async with Client(mcp_client) as client:
        async with MockClient() as client:
            self.graph = build_dynamic_graph(plan, client, dict)
            logger.info(f"[Agent] 构建 DAG 完成")
            # 4. 初始化 state，增加 _globals 字段
            init_state = {"_globals": {k: None for k in global_vars}, "text": query, "user_input": query}
            # 5. 执行 DAG
            dag_start = time.time()
            final_state = await execute_dag(self.graph, init_state)
            dag_elapsed = time.time() - dag_start
            logger.info(f"[Agent] DAG 执行结束，最终 state: {final_state}")
            logger.info(f"[Agent] DAG 执行耗时: {dag_elapsed:.2f} 秒")
            # 6. 输出结果
            total_elapsed = time.time() - total_start
            logger.info(f"[Agent] 总耗时: {total_elapsed:.2f} 秒")
            return output_result(final_state)

if __name__ == "__main__":
    agent = WritingAgent()
    default_query = "请写一篇关于LangGraph的文章"
    user_query = default_query
    asyncio.run(agent.run(user_query))