import sqlite3

con = sqlite3.connect(r"d:\project1\chargeflow\backend\chargeflow.db")
print("work_orders:", con.execute("SELECT id, pile_id, type, status FROM work_orders").fetchall())
print("ai scenes:", con.execute("SELECT scene, COUNT(*), SUM(ok) FROM ai_logs GROUP BY scene").fetchall())
print("ai diagnose rows:", con.execute("SELECT scene, ok, substr(error,1,80) FROM ai_logs WHERE scene LIKE '%diag%' OR scene LIKE '%report%' ORDER BY id DESC LIMIT 5").fetchall())
con.close()
