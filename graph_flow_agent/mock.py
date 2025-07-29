
from typing import Dict, Any
import random
import asyncio
from loguru import logger

class MockTools:
    @staticmethod
    async def outline_generator(input_text: str) -> Dict[str, Any]:
        """模拟大纲生成工具"""
        logger.info(f"[Mock] outline_generator 收到输入: {input_text}")
        await asyncio.sleep(0.5)  # 模拟处理延迟
        outline = """
        # LangGraph 技术文档
        
        ## 1. 核心概念
        - 状态图(StateGraph)
        - 节点(Node)
        - 边(Edge)
        
        ## 2. 使用场景
        - 任务编排
        - 数据处理流水线
        - 决策流程
        
        ## 3. 高级特性
        - 并发执行
        - 错误处理
        - 条件分支
        """
        return {"data": outline.strip(), "status": "success"}

    @staticmethod
    async def outline_splitter(input_text: str) -> Dict[str, Any]:
        """模拟大纲拆分工具"""
        logger.info(f"[Mock] outline_splitter 收到输入: {input_text[:50]}...")
        await asyncio.sleep(0.3)
        sections = [
            {"title": "核心概念", "content": ["状态图(StateGraph)", "节点(Node)", "边(Edge)"]},
            {"title": "使用场景", "content": ["任务编排", "数据处理流水线", "决策流程"]},
            {"title": "高级特性", "content": ["并发执行", "错误处理", "条件分支"]}
        ]
        return {"data": sections, "status": "success"}

    @staticmethod
    async def content_generator(input_text: Dict[str, Any]) -> Dict[str, Any]:
        """模拟内容生成工具(支持批量处理)"""
        logger.info(f"[Mock] content_generator 收到 {len(input_text['data'])} 个章节")
        await asyncio.sleep(0.2 * len(input_text["data"]))
        
        # 模拟批量生成内容
        results = []
        for section in input_text["data"]:
            content = f"## {section['title']}\n\n"
            for item in section:
                content += f"- {item}: 这是关于{item}的详细说明...\n"
            results.append(content.strip())
        return {"data": results, "status": "success"}

    @staticmethod
    async def chart_generator(input_text: str) -> Dict[str, Any]:
        """模拟图表生成工具"""
        logger.info(f"[Mock] chart_generator 收到输入: {input_text[:50]}...")
        await asyncio.sleep(0.4)
        chart = """
        | 特性        | 说明                      | 示例场景          |
        |-------------|---------------------------|-------------------|
        | 状态图      | 管理执行流程              | 多步骤任务编排    |
        | 并发执行    | 提高吞吐量                | 批量数据处理      |
        | 条件分支    | 动态路由                  | 决策流程          |
        """
        return {"data": chart.strip(), "status": "success"}

class MockClient:
    async def __aenter__(self):
        """支持异步上下文管理器协议"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """支持异步上下文管理器协议"""
        pass

    async def call_tool(self, tool_name: str, params: Dict[str, Any]):
        """模拟工具调用客户端"""
        tool_method = getattr(MockTools, tool_name, None)
        if not tool_method:
            raise ValueError(f"未知工具: {tool_name}")
        
        # 处理批量参数
        if "input_text" in params and isinstance(params["input_text"], dict):
            return await tool_method(params["input_text"])
        return await tool_method(params.get("input_text", ""))