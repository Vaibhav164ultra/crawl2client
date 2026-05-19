import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uuid
import logging
from typing import Dict, Any, List
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import traceback

from config import OLLAMA_MODEL
from pipeline import run_pipeline

app = FastAPI(title="Lead Scraper API")

# Allow frontend to access API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory task store
tasks: Dict[str, Dict[str, Any]] = {}

class ScrapeRequest(BaseModel):
    query: str
    max_sites: int = 5
    depth: int = 2
    skip_crawled: bool = False
    min_confidence: float = 0.2
    use_ai: bool = True
    workers: int = 4

class TaskResponse(BaseModel):
    task_id: str
    status: str
    query: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    total_leads: int
    error: str = None
    
class LeadResponse(BaseModel):
    business_name: str
    website: str
    email: str
    phone: str
    location: str
    niche: str
    tags: List[str]
    confidence_score: float
    outreach_score: float
    source_url: str

async def scrape_worker(task_id: str, req: ScrapeRequest):
    """Background worker that runs the pipeline."""
    tasks[task_id]["status"] = "running"
    try:
        leads = await run_pipeline(
            query=req.query,
            max_sites=req.max_sites,
            max_depth=req.depth,
            headless=True,
            concurrency=req.workers,
            skip_crawled=req.skip_crawled,
            min_confidence=req.min_confidence,
            use_ai=req.use_ai,
        )
        
        # Convert Lead models to dict
        leads_dict = []
        for lead in leads:
            leads_dict.append({
                "business_name": lead.business_name,
                "website": lead.website,
                "email": lead.email,
                "phone": lead.phone,
                "location": lead.location,
                "niche": lead.niche,
                "tags": lead.tags,
                "confidence_score": lead.confidence_score,
                "outreach_score": lead.outreach_score,
                "source_url": lead.source_url
            })
            
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["leads"] = leads_dict
        tasks[task_id]["progress"] = 100
        
    except Exception as exc:
        logging.error(f"Task {task_id} failed: {exc}")
        traceback.print_exc()
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(exc)

@app.post("/api/scrape", response_model=TaskResponse)
async def start_scrape(req: ScrapeRequest, bg_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "pending",
        "query": req.query,
        "progress": 0,
        "leads": [],
        "error": None
    }
    
    # We must use asyncio.create_task because run_pipeline launches playwright
    # which has to attach to the running event loop properly.
    asyncio.create_task(scrape_worker(task_id, req))
    
    return TaskResponse(task_id=task_id, status="pending", query=req.query)

@app.get("/api/status/{task_id}", response_model=TaskStatusResponse)
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
        
    t = tasks[task_id]
    return TaskStatusResponse(
        task_id=task_id,
        status=t["status"],
        progress=t["progress"],
        total_leads=len(t["leads"]),
        error=t["error"]
    )

@app.get("/api/results/{task_id}")
async def get_results(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
        
    return {"leads": tasks[task_id]["leads"]}

@app.get("/api/logs")
async def get_logs(lines: int = 100):
    """Return the last N lines of the log file for the live console."""
    try:
        with open("lead_scraper.log", "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return {"logs": all_lines[-lines:]}
    except FileNotFoundError:
        return {"logs": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
