"""Pydantic response models for the FastAPI endpoints."""

from typing import Optional

from pydantic import BaseModel, Field


class CampaignAnalyzed(BaseModel):
    campaign: str
    budget: float
    impressions: int
    clicks: int
    spend: float
    orders: int
    sales: float
    ctr: float = Field(..., description="Clicks / Impressions * 100")
    cpc: float = Field(..., description="Spend / Clicks")
    conversion_rate: float = Field(..., description="Orders / Clicks * 100")
    roas: float = Field(..., description="Sales / Spend")
    acos: float = Field(..., description="Spend / Sales * 100")
    label: str = Field(..., description="Scale | Optimize | Pause")


class CampaignRef(BaseModel):
    name: str
    roas: float


class LabelBreakdown(BaseModel):
    Scale: int
    Optimize: int
    Pause: int


class SummaryResponse(BaseModel):
    total_spend: float
    total_sales: float
    overall_roas: float
    best_campaign: Optional[CampaignRef]
    worst_campaign: Optional[CampaignRef]
    label_breakdown: LabelBreakdown
    wasted_spend_pct: float


class FlaggedCampaign(BaseModel):
    campaign_name: str
    issue: str
    metric_value: str
    recommendation: str
    reason: str


class InsightsResponse(BaseModel):
    flagged_campaigns: list[FlaggedCampaign]
    total_flagged: int
    summary: str


class RecommendationAction(BaseModel):
    action: str
    campaign: str
    reason: str
    estimated_impact: str
    impact_value: float


class RecommendationsResponse(BaseModel):
    top_actions: list[RecommendationAction]
    total_candidates_evaluated: int
    note: str
