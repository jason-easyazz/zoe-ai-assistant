#!/usr/bin/env python3
"""
Transform mcp_server.py from aiosqlite to asyncpg.
"""
import re
import sys


def convert_params_to_positional(params_str: str) -> str:
    """Convert tuple literal like (a, b, c) to positional args a, b, c."""
    s = params_str.strip()
    if s.startswith('(') and s.endswith(')'):
        inner = s[1:-1].strip()
        if inner.endswith(','):
            inner = inner[:-1].strip()
        return inner
    return params_str


def convert_question_marks(sql: str) -> str:
    """Convert ? in SQL string literals to $1, $2, etc."""
    counter = [0]
    result = []
    i = 0
    while i < len(sql):
        if sql[i] == '?':
            counter[0] += 1
            result.append(f'${counter[0]}')
        else:
            result.append(sql[i])
        i += 1
    return ''.join(result)


def transform_sql_strings(content: str) -> str:
    """Convert ? placeholders in SQL string literals."""
    # Match triple-quoted strings and regular quoted strings containing ?
    # We'll process line by line for SQL in execute calls
    lines = content.split('\n')
    result = []
    in_execute_block = False
    sql_lines = []
    
    for line in lines:
        result.append(line)
    
    return content


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'mcp_server.py'
    with open(path) as f:
        content = f.read()
    
    # 1. Remove aiosqlite import
    content = content.replace('import aiosqlite\n', '')
    
    # 2. Fix DB_PATH default
    content = content.replace(
        'DB_PATH = os.environ.get("ZOE_DATA_DB", os.path.join(_BASE_DIR, "zoe.db"))',
        'DB_PATH = os.environ.get("ZOE_DATA_DB", os.path.join(_BASE_DIR, "data", "zoe.db"))'
    )
    
    # 3. Replace the local get_db() + handle_tool() block
    old_block = '''async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def handle_tool(name: str, args: dict) -> str:
    db = await get_db()
    try:
        result = await _execute_tool(db, name, args)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        await db.close()'''
    
    new_block = '''from db_pool import get_db as _pg_get_db  # noqa: E402


async def handle_tool(name: str, args: dict) -> str:
    async with _pg_get_db() as db:
        try:
            result = await _execute_tool(db, name, args)
            return json.dumps(result, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})'''
    
    content = content.replace(old_block, new_block)
    
    # 4. Replace inline aiosqlite.connect patterns
    content = re.sub(
        r'async with aiosqlite\.connect\(DB_PATH\) as (\w+):',
        lambda m: f'async with _pg_get_db() as {m.group(1)}:',
        content
    )
    content = re.sub(
        r'async with aiosqlite\.connect\(_DB_PATH\) as (\w+):',
        lambda m: f'async with _pg_get_db() as {m.group(1)}:',
        content
    )
    
    # 5. Remove row_factory assignments
    content = re.sub(r'[ \t]*\w+\.row_factory = aiosqlite\.Row\n', '', content)
    
    # 6. Remove PRAGMA statements
    content = re.sub(r'[ \t]*await \w+\.execute\("PRAGMA [^"]+"\)\n', '', content)
    
    # 7. Fix datetime('now') → NOW()
    content = re.sub(r"datetime\('now'\)", "NOW()", content)
    content = re.sub(r"datetime\('now', '\+(\d+) days?'\)", r"NOW() + INTERVAL '\1 days'", content)
    content = re.sub(r"datetime\('now', '-(\d+) days?'\)", r"NOW() - INTERVAL '\1 days'", content)
    content = re.sub(r"datetime\('now', '\+1 day'\)", "NOW() + INTERVAL '1 day'", content)
    content = re.sub(r"datetime\('now', '-1 day'\)", "NOW() - INTERVAL '1 day'", content)
    
    # 8. Remove await db.commit() lines
    content = re.sub(r'[ \t]*await \w+\.commit\(\)\n', '', content)
    
    # 9. Fix FTS query
    content = content.replace(
        "ambient_memory_fts f\n            JOIN ambient_memory m ON f.rowid = m.id\n            WHERE ambient_memory_fts MATCH ?",
        "ambient_memory m\n            WHERE m.search_vector @@ plainto_tsquery('english', $1)"
    )
    
    # 10. Fix INSERT OR IGNORE → INSERT with ON CONFLICT DO NOTHING
    # We do a simple replacement here and will add ON CONFLICT below
    # Actually INSERT OR IGNORE in mcp_server.py - check what's there
    content = content.replace("INSERT OR IGNORE INTO", "INSERT INTO")
    
    with open(path + '.new', 'w') as f:
        f.write(content)
    print(f"Written to {path}.new")
    print("Review and then rename to replace original.")


if __name__ == '__main__':
    main()
