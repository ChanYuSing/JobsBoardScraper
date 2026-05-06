import sys, time
sys.path.insert(0, 'src')
from jobboard.db import connect

c = connect()

queries = [
    ("plain COUNT(*) on job_all", "SELECT COUNT(*) FROM job_all", []),
    ("COUNT with LEFT JOIN job_status", """
        SELECT COUNT(*) FROM job_all j
        LEFT JOIN job_status js ON js.source=j.source AND js.job_id=j.job_id
    """, []),
    ("COUNT + 1 LIKE on title", """
        SELECT COUNT(*) FROM job_all j
        LEFT JOIN job_status js ON js.source=j.source AND js.job_id=j.job_id
        WHERE (LOWER(COALESCE(j.title,'')) LIKE %s)
    """, ['%engineer%']),
    ("COUNT + 2 LIKEs (title + company)", """
        SELECT COUNT(*) FROM job_all j
        LEFT JOIN job_status js ON js.source=j.source AND js.job_id=j.job_id
        WHERE (LOWER(COALESCE(j.title,'')) LIKE %s)
          AND (LOWER(COALESCE(j.company,'')) LIKE %s)
    """, ['%engineer%', '%google%']),
    ("PAGE 50 with title LIKE + ORDER BY date_posted", """
        SELECT j.*, COALESCE(js.status,'new') AS triage_status
        FROM job_all j
        LEFT JOIN job_status js ON js.source=j.source AND js.job_id=j.job_id
        WHERE (LOWER(COALESCE(j.title,'')) LIKE %s)
        ORDER BY j.date_posted DESC, j.first_seen_at DESC
        LIMIT 50 OFFSET 0
    """, ['%engineer%']),
    ("description_text LIKE (NOT-wrapped exclude)", """
        SELECT COUNT(*) FROM job_all j
        LEFT JOIN job_status js ON js.source=j.source AND js.job_id=j.job_id
        WHERE NOT (LOWER(COALESCE(j.description_text,'')) LIKE %s)
    """, ['%senior%']),
]

for label, sql, params in queries:
    # warm-up
    c.execute(sql, params).fetchone() if 'COUNT' in sql else c.execute(sql, params).fetchall()
    times = []
    for _ in range(3):
        t = time.perf_counter()
        if 'COUNT' in sql.split()[1] if sql.strip().split() else False:
            c.execute(sql, params).fetchone()
        else:
            c.execute(sql, params).fetchall()
        times.append((time.perf_counter() - t) * 1000)
    print(f"{label:55s}  best={min(times):6.0f} ms  avg={sum(times)/len(times):6.0f} ms")

c.close()
