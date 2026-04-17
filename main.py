"""FastAPI app exposing /analyze, /summary, /insights, /recommendations, and a dashboard UI."""

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from models import (
    CampaignAnalyzed,
    InsightsResponse,
    RecommendationsResponse,
    SummaryResponse,
)
from services import (
    analyze_campaigns,
    build_insights,
    build_recommendations,
    build_summary,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="DataFuel Amazon Ads Analyzer",
    description="Cleans an Amazon Ads CSV and exposes analysis, account summary, and actionable insights.",
    version="1.0.0",
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _load():
    try:
        return analyze_campaigns()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/", tags=["ui"], include_in_schema=False)
def dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api", tags=["meta"])
def api_index():
    return {
        "name": "DataFuel Amazon Ads Analyzer",
        "endpoints": ["/analyze", "/summary", "/insights", "/recommendations"],
        "dashboard": "/",
        "docs": "/docs",
    }


@app.get("/analyze", response_model=list[CampaignAnalyzed], tags=["analysis"])
def analyze():
    return _load()


@app.get("/summary", response_model=SummaryResponse, tags=["analysis"])
def summary():
    return build_summary(_load())


@app.get("/insights", response_model=InsightsResponse, tags=["analysis"])
def insights():
    return build_insights(_load())


@app.get("/recommendations", response_model=RecommendationsResponse, tags=["bonus"])
def recommendations():
    campaigns = _load()
    return build_recommendations(campaigns, build_summary(campaigns))
