import sqlite3
import pandas as pd
import os
import logging
from contextlib import closing

from src.config import get_settings

logger = logging.getLogger(__name__)

def load_csv_to_sqlite(file_path: str, session_id: str = None, original_filename: str = None) -> dict:
    try:
        # Derive table name from the original filename if provided, otherwise from the file path
        source_name = original_filename if original_filename else os.path.basename(file_path)
        table_name = os.path.splitext(source_name)[0]
        # Sanitize table name to be a valid SQLite identifier
        table_name = table_name.replace("-", "_").replace(" ", "_")

        # Prefix with session_id to prevent collision when multiple users upload same filename
        db_dir = get_settings().db_dir
        if session_id:
            db_path = f"{db_dir}/{session_id}_{table_name}.db"
        else:
            db_path = f"{db_dir}/{table_name}.db"
        
        # Load CSV into dataframe
        df = pd.read_csv(file_path)
        
        # Connect to SQLite and load dataframe as table.
        # closing() guarantees the connection is closed even if to_sql raises,
        # which is what was leaving the file locked on Windows.
        with closing(sqlite3.connect(db_path)) as conn:
            df.to_sql(table_name, conn, if_exists="replace", index=False)

        logger.info(f"CSV loaded into SQLite: {db_path} | Table: {table_name}")

        return {
            "db_path": db_path,
            "table_name": table_name,
            "columns": list(df.columns),
            "row_count": df.shape[0],
            "column_count": df.shape[1]
        }

    except Exception as e:
        logger.error(f"Failed to load CSV to SQLite: {str(e)}")
        raise
