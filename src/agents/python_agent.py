import ast
import builtins
import logging
import threading
import pandas as pd
from contextlib import redirect_stdout
from io import StringIO
from src.services.llm_service import get_llm_client, get_model_for_complexity, get_retry_budget, DEFAULT_MODEL
from src.services.rag_service import retrieve_session_context, format_context_block
from fastapi import HTTPException
import time

logger = logging.getLogger(__name__)

# exec() sandboxing (PHASES.md risk #2, docs/THREAT_MODEL.md). exec()
# auto-injects the real __builtins__ into any globals dict that doesn't
# already define that key — the old `safe_environment = {"df": df}` looked
# restrictive by name but left open()/__import__()/eval() fully reachable.
# This allowlist is deliberately generous with safe names (so ordinary
# pandas-shaped code doesn't burn retries on a missing builtin) and strict
# only about the genuinely dangerous ones: no open, __import__, eval, exec,
# compile, input, getattr/setattr/delattr, globals/locals/vars.
_ALLOWED_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "min", "next", "pow", "print",
        "range", "repr", "reversed", "round", "set", "slice", "sorted",
        "str", "sum", "tuple", "type", "zip",
        "True", "False", "None",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "ZeroDivisionError", "StopIteration", "AttributeError",
        "NotImplementedError", "ArithmeticError", "OverflowError",
        # Not something generated code calls directly — the interpreter
        # itself invokes this internally to execute any `class` statement.
        # Without it, legitimate code defining a helper class (a real,
        # if uncommon, shape for the high-complexity scaffolding prompt to
        # produce) fails with "__build_class__ not found" even though the
        # class itself is completely benign. Found via manual edge-case
        # testing (2026-09-15), not something the unit tests caught.
        "__build_class__",
    )
    if hasattr(builtins, name)
}

EXEC_TIMEOUT_SECONDS = 10


def _validate_code_is_safe(code: str) -> None:
    """Static AST check, run before exec(). Restricted builtins alone don't
    stop two things: bare `import` statements (a distinct syntax node, not
    a name lookup a builtins dict can intercept) and dunder-attribute
    sandbox escapes like ().__class__.__bases__[0].__subclasses__(), which
    walk Python's object graph using only ordinary attribute access. Raises
    a plain Exception on any violation — this flows into the existing
    retry loop the same as any other code failure, so a rejected snippet
    just prompts the LLM to try again without the forbidden construct."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise Exception(f"Generated code has a syntax error: {e}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Actionable, not just descriptive — this message flows straight
            # into fix_code()'s retry prompt as the error context. A bare
            # "may not use import statements" left the retry loop to guess
            # why (observed: it removed the import but kept using the
            # now-undefined name, then re-added the import to fix that,
            # cycling until attempts ran out — see PHASES.md/BUGS_FOUND.md
            # for the concrete case: matplotlib import for a chart question).
            raise Exception(
                "Generated code may not use import statements. pandas is "
                "already available as 'pd' and the dataframe as 'df' — do "
                "not import pandas or anything else. Do not import or use "
                "any plotting library (matplotlib, seaborn, plotly) either "
                "— never generate a chart; only compute and print the "
                "requested data."
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            raise Exception(f"Generated code may not access dunder attributes ({node.attr!r}).")


# Added for "high" complexity only (see run()). Complexity used to also
# scale how many sample rows appeared in the prompt (PROMPT_SAMPLE_ROWS),
# but that lever died when data_context_service.py replaced the per-agent
# CSV peek with a fixed-size shared context (Phase 4) — leaving retry
# budget as the only thing that differed between medium and high, which
# only helps after a first attempt already failed. This scaffolding
# targets the actual gap: first-attempt reasoning quality on genuinely
# multi-step questions (the ones complexity=high is meant to describe),
# not just how many retries they get.
HIGH_COMPLEXITY_SCAFFOLDING = """
        This question requires combining multiple computations (e.g. trend over time,
        comparison across dimensions, or a multi-step calculation). Before writing the final
        code: identify each intermediate value you need and the order you need them in. Write
        code that computes and prints each intermediate step as well as the final answer, not
        just the final answer alone — this makes it possible to tell which step is wrong if the
        result looks incorrect.
        """


class PythonAgent:
    def __init__(self, client=None):
        self.client = client if client is not None else get_llm_client()
        self.model = DEFAULT_MODEL
        self.max_attempts = 3

    def clean_code(self, code: str) -> str:
        return code.replace("```python", "").replace("```", "").strip()

    def execute_code(self, code: str, file_path: str) -> str:
        try:
            _validate_code_is_safe(code)

            df = pd.read_csv(file_path)

            # __builtins__ explicitly restricted — see _ALLOWED_BUILTINS.
            # Without this key, exec() silently injects the real,
            # unrestricted __builtins__ into this dict regardless of its
            # name, which is what made the old "safe_environment" not
            # actually safe.
            #
            # pd is provided explicitly: generate_code()'s own prompt tells
            # the LLM to call pd.to_datetime()/pd.Grouper() for date-based
            # grouping, but pd was never actually in this environment (found
            # via manual edge-case testing, 2026-09-15) — every date/trend
            # question's generated code has been failing with "name 'pd' is
            # not defined" since this dict only ever contained "df". Import
            # access is otherwise blocked (see _validate_code_is_safe), so
            # this one pre-approved module reference doesn't reopen that.
            # __name__ is needed alongside __build_class__ above — a class
            # statement's machinery reads the enclosing __name__ (normally
            # supplied by the real module namespace) even for a completely
            # ordinary class body. Also found via manual edge-case testing.
            safe_environment = {
                "df": df, "pd": pd, "__name__": "sandboxed_code",
                "__builtins__": _ALLOWED_BUILTINS,
            }

            # Capture printed output. redirect_stdout guarantees sys.stdout
            # is restored on the way out of the `with` block even if exec()
            # raises something except Exception below wouldn't catch (e.g.
            # SystemExit) — a bare reassign-then-restore-in-except doesn't
            # cover that, and under concurrent requests a raw `sys.stdout =`
            # reassignment is a shared global that a second in-flight
            # request could also be reassigning at the same time.
            captured_output = StringIO()
            exec_error = []

            def _run():
                try:
                    with redirect_stdout(captured_output):
                        exec(code, safe_environment)
                except Exception as inner_e:
                    exec_error.append(inner_e)

            # Best-effort wall-clock bound, not a hard resource guarantee:
            # CPython threads can't be forcibly killed, so a snippet stuck
            # in a tight C-level pandas/numpy loop keeps running in the
            # background after this times out, still consuming CPU. This
            # bounds how long the *caller* waits; it does not reclaim that
            # thread's CPU/memory. A real hard-kill needs a subprocess
            # boundary (OS can terminate a process, not a thread) — noted
            # as a near-term follow-up in docs/THREAT_MODEL.md rather than
            # implemented here.
            worker = threading.Thread(target=_run, daemon=True)
            worker.start()
            worker.join(EXEC_TIMEOUT_SECONDS)

            if worker.is_alive():
                raise Exception(f"Execution exceeded the {EXEC_TIMEOUT_SECONDS}s time limit.")

            if exec_error:
                raise exec_error[0]

            output = captured_output.getvalue()

            if not output:
                raise Exception("Code executed successfully but produced no output. Make sure to print the final result.")

            return output

        except Exception as e:
            logger.error(f"Code execution failed: {str(e)}")
            raise Exception(f"Execution error: {str(e)}")

    def generate_code(self, question: str, data_context: str = "", rag_context: str = "", complexity: str = "medium") -> str:
        prompt = f"""
        You are a data analyst. You have access to a CSV file with the following structure:

        {data_context}
        {format_context_block(rag_context)}

        The user is asking: {question}
        {HIGH_COMPLEXITY_SCAFFOLDING if complexity == "high" else ""}
        Write Python code using pandas to answer this question — never answer directly, even
        if the answer seems simple. The last line must be a print() of the final result.
        'df' (the dataframe) and 'pd' (pandas) are already available — never write import
        statements, not even 'import pandas as pd'.
        Never generate a chart, plot, or import a plotting library (matplotlib, seaborn,
        plotly) — a separate step handles visualization; just compute and print the data.
        Print clean, human-readable output — never a raw dict or tuple containing numpy
        types (e.g. np.float64(...)); convert values to plain Python numbers/strings first.
        Return only the code, nothing else.
        Be precise: .mean() for average, .sum() for total, .median() for median, .std() for
        standard deviation.
        For monthly grouping use pd.Grouper(key='date', freq='ME') (freq='M' is deprecated).
        Convert date columns with pd.to_datetime() before any date-based grouping.
        """

        try:
            logger.info("LLM call started")
            llm_start = time.time()
            
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.1,  # Lower temperature for more deterministic output
                messages=[
                    {"role": "system", "content": "You are a helpful data analyst who writes clean Python code."},
                    {"role": "user", "content": prompt}
                ]
            )

            logger.info(f"LLM call completed in {round(time.time() - llm_start, 2)}s")
            
            generated_code = response.choices[0].message.content
            return generated_code
    
        except Exception as e:
            logger.error(f"Groq API call failed: {str(e)}")
            raise HTTPException(status_code=503, detail="AI service temporarily unavailable. Please try again later.")

    def fix_code(self, question: str, failed_code: str, error: str, data_context: str = "", rag_context: str = "", complexity: str = "medium") -> str:
        prompt = f"""
        You are a data analyst. You have access to a CSV file with the following structure:

        {data_context}
        {format_context_block(rag_context)}

        The user is asking: {question}

        You previously generated this code:
        {failed_code}

        But it failed with this error:
        {error}
        {HIGH_COMPLEXITY_SCAFFOLDING if complexity == "high" else ""}
        Fix the code and return only the corrected code, nothing else. The last line must be
        a print() of the final result.
        'df' (the dataframe) and 'pd' (pandas) are already available — never write import
        statements, not even 'import pandas as pd'.
        Never generate a chart, plot, or import a plotting library (matplotlib, seaborn,
        plotly) — a separate step handles visualization; just compute and print the data.
        Print clean, human-readable output — never a raw dict or tuple containing numpy
        types (e.g. np.float64(...)); convert values to plain Python numbers/strings first.
        """

        try:
            logger.info("LLM error fix call started")
            llm_start = time.time()

            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                messages=[
                    {"role": "system", "content": "You are a helpful data analyst who writes clean Python code."},
                    {"role": "user", "content": prompt}
                ]
            )

            logger.info(f"LLM error fix call completed in {round(time.time() - llm_start, 2)}s")
            
            generated_code = response.choices[0].message.content
            return generated_code
        
        except Exception as e:
            logger.error(f"Groq API call failed: {str(e)}")
            raise HTTPException(status_code=503, detail="AI service temporarily unavailable. Please try again later.")

    def run(self, question: str, file_path: str, complexity: str = "medium", data_context: str = "", session_id: str = None) -> tuple[str, int, str]:
        self.model = get_model_for_complexity(complexity)
        self.max_attempts = get_retry_budget(complexity)
        logger.info(f"Python agent running for question: {question} | complexity={complexity} | model={self.model} | max_attempts={self.max_attempts}")

        rag_context = retrieve_session_context(session_id, question)
        generated_code = self.clean_code(self.generate_code(question, data_context, rag_context, complexity))
        logger.info(f"Generated code:\n{generated_code}")

        attempt = 1
        while attempt <= self.max_attempts:
            try:
                logger.info(f"Execution attempt {attempt} of {self.max_attempts}")
                result = self.execute_code(generated_code, file_path)
                logger.info("Execution successful")
                return result, attempt, self.model

            except Exception as e:
                logger.warning(f"Attempt {attempt} failed: {str(e)}")

                if attempt == self.max_attempts:
                    raise Exception(f"Python agent failed after {self.max_attempts} attempts: {str(e)}")

                generated_code = self.clean_code(self.fix_code(question, generated_code, str(e), data_context, rag_context, complexity))
                attempt += 1
