from pydantic import BaseModel
from typing import Optional, List

class AnalysisResponse(BaseModel):
    question: str
    result: str
    status: str
    attempts: int
    time_taken: str
    model_used: str
    row_count: int
    column_count: int
    file_name: str
    timestamp: str
    agents_used: List[str] = []
    task_type: str = ""
    reasoning: str = ""
    complexity: str = ""
    chart_path: Optional[str] = None
    # Server-local filesystem path (chart_path) is only meaningful to
    # someone with disk access to the server — useless to a remote caller.
    # chart_url is the fetchable equivalent, e.g. "/charts/{uuid}.png",
    # served by main.py's static mount. Additive: chart_path is unchanged
    # for backward compatibility.
    chart_url: Optional[str] = None
    session_id: Optional[str] = None
