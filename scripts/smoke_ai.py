"""Verify the AI scoring write path (save_job_analysis) works against Postgres."""
import sys
sys.path.insert(0, 'src')
from jobboard.db import connect
from jobboard.web.services.analyse import save_job_analysis, get_field_defs, get_score_job_counts, get_jobs_for_analysis, _already_scored_keys

c = connect()

# 1. Field defs — read
fds = get_field_defs(c)
print(f'1. get_field_defs() ......... {len(fds)} fields')
for f in fds[:3]:
    print(f"     - {f['name']:25s} ({f['type']})")

# 2. Score counts — read across both source tables + EXISTS join
counts = get_score_job_counts(c)
print(f'2. get_score_job_counts() ... total={counts["total"]} new={counts["new"]}')

# 3. Already-scored keys — uses IN (?,?,...) placeholder expansion
field_names = [f['name'] for f in fds]
keys = _already_scored_keys(c, field_names)
print(f'3. _already_scored_keys() ... {len(keys)} jobs already scored')

# 4. Pull a real job for analysis
jobs = get_jobs_for_analysis(c, max_jobs=1)
print(f'4. get_jobs_for_analysis() .. {len(jobs)} job pulled')
if jobs:
    j = jobs[0]
    print(f"     {j['source']} / {j['job_id']} / {(j.get('title') or '')[:40]!r}")

    # 5. Write a fake analysis result for that real job
    fake = {f['name']: 7 if f['type'] == 'int' else 'good fit' for f in fds[:2]}
    print(f'5. save_job_analysis() ..... writing {fake} ...')
    save_job_analysis(c, j['source'], j['job_id'], fake)

    # 6. Read back
    rows = c.execute(
        """
        SELECT fd.name, ja.value_int, ja.value_str
        FROM job_analysis ja JOIN field_def fd ON fd.id = ja.field_id
        WHERE ja.source = ? AND ja.job_id = ?
        """,
        (j['source'], j['job_id']),
    ).fetchall()
    print(f'   read back: {[(r["name"], r["value_int"], r["value_str"]) for r in rows]}')

    # 7. UPSERT — write again with different values
    fake2 = {f['name']: 9 if f['type'] == 'int' else 'updated' for f in fds[:2]}
    save_job_analysis(c, j['source'], j['job_id'], fake2)
    rows2 = c.execute(
        "SELECT fd.name, ja.value_int, ja.value_str FROM job_analysis ja "
        "JOIN field_def fd ON fd.id = ja.field_id "
        "WHERE ja.source = ? AND ja.job_id = ?",
        (j['source'], j['job_id']),
    ).fetchall()
    print(f'7. UPSERT updated values .. {[(r["name"], r["value_int"], r["value_str"]) for r in rows2]}')

    # 8. Cleanup
    c.execute("DELETE FROM job_analysis WHERE source = ? AND job_id = ?", (j['source'], j['job_id']))
    c.commit()
    print('8. cleaned up sentinel rows OK')

c.close()
print('\nAI write path: OK')
