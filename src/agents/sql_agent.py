import logging
import sqlite3
from contextlib import closing
from tabulate import tabulate
from src.services.llm_service import get_llm_client, get_model_for_complexity, get_retry_budget, DEFAULT_MODEL
from src.services.database_service import load_csv_to_sqlite
from src.services.rag_service import retrieve_session_context, format_context_block

logger = logging.getLogger(__name__)

# Same reasoning as python_agent.py's HIGH_COMPLEXITY_SCAFFOLDING: complexity
# used to also scale prompt richness (PROMPT_SAMPLE_ROWS), which died when
# data_context_service.py replaced the per-agent CSV peek with a fixed-size
# shared context (Phase 4), leaving retry budget as the only real
# differentiator between medium and high — which only helps after a first
# attempt already failed. This targets first-attempt query quality on
# genuinely multi-step questions instead.
HIGH_COMPLEXITY_SQL_SCAFFOLDING = """
        This question requires multiple steps (e.g. filtering, grouping, then comparing or
        ranking results). Consider using a CTE (WITH clause) or subquery to compute
        intermediate results clearly, rather than one dense query — this makes each step of
        the logic easier to verify if the result looks incorrect.
        """


class SQLAgent:
    def __init__(self, client=None):
        self.client = client if client is not None else get_llm_client()
        self.model = DEFAULT_MODEL
        self.max_attempts = 3

    def generate_sql(self, question: str, db_info: dict, data_context: str = "", rag_context: str = "", complexity: str = "medium") -> str:
        schema = f"Table: {db_info['table_name']}\n"
        schema += f"Columns: {db_info['columns']}\n"
        schema += f"Row count: {db_info['row_count']}"

        prompt = f"""
        You are a SQL expert. You have access to a SQLite database with the following schema:

        {schema}

        The underlying dataset looks like this:
        {data_context}
        {format_context_block(rag_context)}

        The user is asking: {question}
        {HIGH_COMPLEXITY_SQL_SCAFFOLDING if complexity == "high" else ""}
        Write a SQLite SQL query to answer this question.
        Return only the SQL query, nothing else.
        Do not include any explanation or markdown.
        Always double-quote column names in the query (e.g. "Total Revenue"), since column
        names may contain spaces or other characters that are invalid as bare SQL identifiers.
        """

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are a SQL expert who writes clean SQLite queries."},
                {"role": "user", "content": prompt}
            ]
        )

        return response.choices[0].message.content.strip()

    def fix_sql(self, question: str, failed_sql: str, error: str, db_info: dict, data_context: str = "", rag_context: str = "", complexity: str = "medium") -> str:
        schema = f"Table: {db_info['table_name']}\n"
        schema += f"Columns: {db_info['columns']}\n"

        prompt = f"""
        You are a SQL expert. You have access to a SQLite database with the following schema:

        {schema}

        The underlying dataset looks like this:
        {data_context}
        {format_context_block(rag_context)}

        The user is asking: {question}

        You previously generated this SQL:
        {failed_sql}

        But it failed with this error:
        {error}
        {HIGH_COMPLEXITY_SQL_SCAFFOLDING if complexity == "high" else ""}
        Fix the SQL and return only the corrected query, nothing else.
        Always double-quote column names in the query (e.g. "Total Revenue"), since column
        names may contain spaces or other characters that are invalid as bare SQL identifiers.
        """

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are a SQL expert who writes clean SQLite queries."},
                {"role": "user", "content": prompt}
            ]
        )

        return response.choices[0].message.content.strip()

    def execute_sql(self, sql: str, db_path: str) -> str:
        # closing() guarantees the connection is closed even if cursor.execute
        # raises (e.g. invalid LLM-generated SQL) — an unclosed connection is
        # what left the .db file locked on Windows during session cleanup.
        with closing(sqlite3.connect(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description]

        if not rows:
            return "Query executed successfully but returned no results"
        
        result = tabulate(rows, headers=columns, tablefmt="pretty")

        # Previously we formatted this way, but it was not clean
        # result = " | ".join(columns) + "\n"
        # result += "-" * 50 + "\n"
        # for row in rows:
        #     result += " | ".join(str(val) for val in row) + "\n"

        return result

    def clean_sql(self, sql: str) -> str:
      return sql.replace("```sql", "").replace("```", "").strip()
    
    def run(self, question: str, file_path: str, complexity: str = "medium", session_id: str = None, original_filename: str = None, data_context: str = "") -> tuple[str, int, str]:
        self.model = get_model_for_complexity(complexity)
        self.max_attempts = get_retry_budget(complexity)
        logger.info(f"SQL agent running for question: {question} | complexity={complexity} | model={self.model} | max_attempts={self.max_attempts}")

        db_info = load_csv_to_sqlite(file_path, session_id=session_id, original_filename=original_filename)
        rag_context = retrieve_session_context(session_id, question)

        sql = self.generate_sql(question, db_info, data_context, rag_context, complexity)
        sql = self.clean_sql(sql)
        logger.info(f"Generated SQL:\n{sql}")

        attempt = 1
        while attempt <= self.max_attempts:
            try:
                logger.info(f"SQL execution attempt {attempt} of {self.max_attempts}")
                result = self.execute_sql(sql, db_info["db_path"])
                logger.info("SQL execution successful")
                return result, attempt, self.model

            except Exception as e:
                logger.warning(f"Attempt {attempt} failed: {str(e)}")

                if attempt == self.max_attempts:
                    raise Exception(f"SQL agent failed after {self.max_attempts} attempts: {str(e)}")

                sql = self.fix_sql(question, sql, str(e), db_info, data_context, rag_context, complexity)
                sql = self.clean_sql(sql)
                attempt += 1
