import sys, time
sys.path.insert(0, 'src')
from jobboard.db import connect
c = connect()

q1 = "SELECT COUNT(*) FROM job_all WHERE LOWER(COALESCE(description_text,'')) LIKE %s"
# warm
c.execute(q1, ['%senior%']).fetchone()
t = time.perf_counter()
n = c.execute(q1, ['%senior%']).fetchone()[0]
print(f"positive description LIKE: {n} rows in {(time.perf_counter()-t)*1000:.0f} ms")

q2 = "SELECT COUNT(*) FROM job_all WHERE LOWER(description_text) LIKE %s"
t = time.perf_counter()
n = c.execute(q2, ['%senior%']).fetchone()[0]
print(f"positive (no COALESCE):    {n} rows in {(time.perf_counter()-t)*1000:.0f} ms")

print("\nPlan for positive LIKE (with COALESCE wrapper, as the app uses):")
for r in c.execute("EXPLAIN " + q1, ['%senior%']).fetchall():
    print(" ", r[0])

print("\nPlan for positive LIKE (no COALESCE):")
for r in c.execute("EXPLAIN " + q2, ['%senior%']).fetchall():
    print(" ", r[0])
