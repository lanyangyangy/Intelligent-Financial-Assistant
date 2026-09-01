from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.common.response import ApiResponse
from app.common.security.auth import require_staff_permission
from app.models.auth import User

router = APIRouter(prefix="/graph", tags=["knowledge-graph"])


@router.get("/stats", response_model=ApiResponse[dict])
async def graph_stats(
    request: Request,
    _user: Annotated[User, Depends(require_staff_permission("product:read"))],
) -> ApiResponse[dict]:
    """查看图谱节点/关系统计（Phase 3 F3.1）。"""
    graph = request.app.state.knowledge_graph
    return ApiResponse(data=await graph.get_graph_stats())


@router.get("/customers", response_model=ApiResponse[list[dict]])
async def graph_customers(
    request: Request,
    _user: Annotated[User, Depends(require_staff_permission("customer:read"))],
) -> ApiResponse[list[dict]]:
    """图谱中客户名单（前端客户选择器）。"""
    graph = request.app.state.knowledge_graph
    return ApiResponse(data=await graph.list_customers())


@router.get("/visualization/{customer_id}", response_model=ApiResponse[dict])
async def graph_visualization(
    request: Request,
    customer_id: str,
    _user: Annotated[User, Depends(require_staff_permission("customer:read"))],
) -> ApiResponse[dict]:
    """客户图谱可视化数据（节点+边 JSON）。"""
    graph = request.app.state.knowledge_graph
    return ApiResponse(data=await graph.get_customer_graph(customer_id))


@router.get("/products/{product_name}/industry", response_model=ApiResponse[list[dict]])
async def product_industry(
    request: Request,
    product_name: str,
    _user: Annotated[User, Depends(require_staff_permission("product:read"))],
) -> ApiResponse[list[dict]]:
    graph = request.app.state.knowledge_graph
    return ApiResponse(data=await graph.get_product_industry(product_name))
