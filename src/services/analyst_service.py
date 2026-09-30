import logging
import os
import time
import pandas as pd
from datetime import datetime
from uuid import uuid4
from fastapi import HTTPException
from src.services.llm_service import DEFAULT_MODEL
from src.services.data_context_service import generate_data_context
from src.agents.planner_agent import PlannerAgent
from src.agents.python_agent import PythonAgent
from src.agents.sql_agent import SQLAgent
from src.agents.chart_agent import ChartAgent
from src.services.session_service import register_chart
from src.utils.schemas import AnalysisResponse


logger = logging.getLogger(__name__)


def build_agents(client=None) -> dict:
    """Construct a fresh set of agents. Replaces the old module-level
    singletons (built once at import time) so tests can inject a fake LLM
    client instead of hitting the real Groq API — pass client= to have it
    forwarded to every agent instead of each one lazily building its own."""
    return {
        "planner": PlannerAgent(client=client),
        "python": PythonAgent(client=client),
        "sql": SQLAgent(client=client),
        "chart": ChartAgent(client=client),
    }


def analyse(question: str, file_path: str, session_id: str = None, original_filename: str = None) -> AnalysisResponse:
    agents_map = build_agents()
    planner = agents_map["planner"]
    python_agent = agents_map["python"]
    sql_agent = agents_map["sql"]
    chart_agent = agents_map["chart"]

    if session_id is None:
        session_id = str(uuid4())  # auto-generate for /ask backward compat
    start_time = time.time()

    try:
        df = pd.read_csv(file_path)
        row_count = len(df)
        column_count = len(df.columns)
        # Use original filename for display if provided (e.g. from upload), else extract from path
        file_name = original_filename if original_filename else os.path.basename(file_path)
        logger.info(f"CSV loaded: {row_count} rows, {column_count} columns | File: {file_name}")

        if row_count == 0:
            logger.warning(f"CSV file has no data rows: {file_path}")
            raise HTTPException(status_code=400, detail="The CSV file contains no data rows. Please upload a file with at least one row of data.")

    except FileNotFoundError:
        logger.error(f"CSV file not found: {file_path}")
        raise HTTPException(status_code=404, detail=f"CSV file not found: {file_path}")

    # Computed once here and passed to every agent below, instead of each
    # agent separately re-reading the CSV to build its own lightweight
    # summary (Phase 4 — was one of 7 redundant pd.read_csv calls per
    # request; see PHASES.md). This is also what replaced the hardcoded
    # TechMart RAG docs that caused any non-TechMart CSV to get incorrectly
    # ruled out of scope — see docs/BUGS_FOUND.md and PHASES.md Phase 4.
    data_context = generate_data_context(file_path)

    plan = planner.run(question, row_count, data_context, session_id=session_id)
    agents = plan.get("agents", ["python"])
    task_type = plan.get("task_type", "analysis")
    complexity = plan.get("complexity", "medium")
    reasoning = plan.get("reasoning", "")

    # A chart-only plan has nothing to chart — ChartAgent only runs after
    # python/sql have produced a result (see the "if chart ... and result is
    # not None" guard below). Coerce it into a valid plan instead of letting
    # result stay None all the way to the final AnalysisResponse(), which
    # fails Pydantic validation (result: str, not Optional) — see
    # docs/BUGS_FOUND.md #2.
    if "chart" in agents and "python" not in agents and "sql" not in agents:
        logger.warning(f"Chart-only plan from planner — adding 'python' so there's data to chart: {agents}")
        agents = ["python"] + agents

    logger.info(f"Plan: task_type={task_type} | complexity={complexity} | agents={agents}")

    # If the planner determined the question is out of scope, return early
    if "none" in agents:
        logger.info("Question out of scope — no agent dispatched")
        time_taken = f"{round(time.time() - start_time, 2)}s"
        return AnalysisResponse(
            question=question,
            result="This question cannot be answered from the provided data. Please ask a question related to the CSV file.",
            status="out_of_scope",
            attempts=0,
            time_taken=time_taken,
            model_used=DEFAULT_MODEL,
            row_count=row_count,
            column_count=column_count,
            file_name=file_name,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            agents_used=[],
            task_type=task_type,
            reasoning=reasoning,
            complexity=complexity,
            chart_path=None,
            session_id=session_id
        )

    result = None
    chart_path = None
    chart_url = None
    agents_used = []
    model_used = DEFAULT_MODEL

    attempts = 1
    result_parts = []
    try:
        if "python" in agents:
            logger.info("Routing to Python agent")
            python_result, attempts, model_used = python_agent.run(question, file_path, complexity, data_context, session_id=session_id)
            agents_used.append("python")
            result_parts.append(python_result)

        if "sql" in agents:
            logger.info("Routing to SQL agent")
            sql_result, attempts, model_used = sql_agent.run(question, file_path, complexity, session_id=session_id, original_filename=original_filename, data_context=data_context)
            agents_used.append("sql")
            result_parts.append(sql_result)

        # A plan naming both python and sql previously ran python only (the
        # dispatch used to be if/elif) — sql's result was silently dropped.
        # See docs/BUGS_FOUND.md #1. Both now run independently; if both
        # fired, label and concatenate so neither result is lost.
        if len(result_parts) == 2:
            result = f"Python result:\n{result_parts[0]}\n\nSQL result:\n{result_parts[1]}"
        elif result_parts:
            result = result_parts[0]

        if "chart" in agents and result is not None:
            logger.info("Routing to Chart agent")
            chart_path = chart_agent.run(question, result, file_path, complexity, session_id=session_id, data_context=data_context)
            agents_used.append("chart")
            # chart_path is a server-local filesystem path — chart_url is
            # the fetchable equivalent, served by main.py's /charts mount.
            chart_url = f"/charts/{os.path.basename(chart_path)}"
            register_chart(session_id, chart_path)

    except Exception as e:
        logger.error(f"Agent execution failed: {str(e)}")
        time_taken = f"{round(time.time() - start_time, 2)}s"
        return AnalysisResponse(
            question=question,
            result="Unable to answer your question at this time. Please try again or rephrase your question.",
            status="failed",
            attempts=attempts,
            time_taken=time_taken,
            model_used=model_used,
            row_count=row_count,
            column_count=column_count,
            file_name=file_name,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            agents_used=agents_used,
            task_type=task_type,
            reasoning=reasoning,
            complexity=complexity,
            chart_path=chart_path,
            chart_url=chart_url,
            session_id=session_id
        )

    time_taken = f"{round(time.time() - start_time, 2)}s"
    logger.info(f"Analysis complete | Time: {time_taken} | Agents: {agents_used}")

    return AnalysisResponse(
        question=question,
        result=result,
        status="success",
        attempts=attempts,
        time_taken=time_taken,
        model_used=model_used,
        row_count=row_count,
        column_count=column_count,
        file_name=file_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        agents_used=agents_used,
        task_type=task_type,
        reasoning=reasoning,
        complexity=complexity,
        chart_path=chart_path,
        chart_url=chart_url,
        session_id=session_id
    )
