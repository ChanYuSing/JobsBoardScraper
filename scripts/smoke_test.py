import sys
sys.path.insert(0, 'src')
from jobboard.db import connect

c = connect()
print('job_jobsdb   :', c.execute('SELECT COUNT(*) FROM job_jobsdb').fetchone()[0])
print('job_linkedin :', c.execute('SELECT COUNT(*) FROM job_linkedin').fetchone()[0])
print('job_all      :', c.execute('SELECT COUNT(*) FROM job_all').fetchone()[0])
print('field_def    :', c.execute('SELECT COUNT(*) FROM field_def').fetchone()[0])

# sample read
r = c.execute("SELECT source, job_id, title, company FROM job_all ORDER BY first_seen_at DESC LIMIT 3").fetchall()
print('\nLatest 3 jobs:')
for row in r:
    print(f'  [{row["source"]}] {row["title"][:50]!r} @ {row["company"]!r}')

# write test: insert a fake jobsdb row, verify trigger fires, then delete
print('\nWrite test: insert sentinel job_jobsdb row...')
c.execute("""
    INSERT INTO job_jobsdb (job_id, title, company, location, first_seen_at, last_seen_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT (job_id) DO UPDATE SET title = EXCLUDED.title
""", ("__sentinel_test__", "Migration Test Job", "TestCo", "Nowhere", "2026-05-06T00:00:00Z", "2026-05-06T00:00:00Z"))
c.commit()
chk = c.execute("SELECT title, company FROM job_all WHERE source='jobsdb' AND job_id='__sentinel_test__'").fetchone()
print('  job_all has it ->', dict(zip(chk.keys(), tuple(chk))) if chk else 'MISSING (trigger broken!)')
c.execute("DELETE FROM job_jobsdb WHERE job_id='__sentinel_test__'")
c.commit()
chk2 = c.execute("SELECT 1 FROM job_all WHERE source='jobsdb' AND job_id='__sentinel_test__'").fetchone()
print('  after delete, job_all ->', 'still there (trigger broken!)' if chk2 else 'cleaned up OK')

c.close()
