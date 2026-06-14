from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import base64
import json
from google import genai
from google.genai import types

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

@app.on_event("startup")
def startup():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # WealthRadar Tables
    cursor.execute('''CREATE TABLE IF NOT EXISTS goals (goal_id SERIAL PRIMARY KEY, product_name TEXT, target_price REAL)''')
    cursor.execute('ALTER TABLE goals ADD COLUMN IF NOT EXISTS image_url TEXT')
    cursor.execute('ALTER TABLE goals ADD COLUMN IF NOT EXISTS product_link TEXT')
    cursor.execute('''CREATE TABLE IF NOT EXISTS savings_logs (log_id SERIAL PRIMARY KEY, goal_id INTEGER, amount_saved REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Energy Saver Tables
    cursor.execute('''CREATE TABLE IF NOT EXISTS appliances (
        appliance_id SERIAL PRIMARY KEY,
        appliance_name TEXT,
        watts REAL,
        min_hours REAL,
        max_hours REAL
    )''')
    
    # NEW: Daily Timetable Logs
    cursor.execute('''CREATE TABLE IF NOT EXISTS appliance_logs (
        log_id SERIAL PRIMARY KEY,
        appliance_id INTEGER,
        log_date DATE DEFAULT CURRENT_DATE,
        hours_used REAL,
        UNIQUE(appliance_id, log_date)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS electricity_logs (
        log_id SERIAL PRIMARY KEY,
        prev_reading REAL,
        curr_reading REAL,
        rate_per_unit REAL,
        total_bill REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    cursor.close()
    conn.close()

# ----------------- PYDANTIC MODELS -----------------

class GoalCreate(BaseModel):
    product_name: str; target_price: float; image_url: str = ""; product_link: str = ""

class GoalUpdate(BaseModel):
    product_name: str; target_price: float; image_url: str = ""; product_link: str = ""

class SavingsLogCreate(BaseModel):
    goal_id: int; amount_saved: float
    
class ImageRequest(BaseModel):
    image_base64: str; mime_type: str = "image/jpeg"

class SmartSplitRequest(BaseModel):
    amount: float

class LinkRequest(BaseModel):
    link: str

class ApplianceCreate(BaseModel):
    appliance_name: str
    watts: float
    min_hours: float
    max_hours: float

class ElectricityLogCreate(BaseModel):
    prev_reading: float
    curr_reading: float
    rate_per_unit: float

class DailyLogUpdate(BaseModel):
    appliance_id: int
    hours_used: float

# ----------------- EXISTING ROUTES -----------------

@app.get("/api/v1/dashboard-stats")
def get_dashboard_stats():
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT COUNT(goal_id) as total_goals, SUM(target_price) as total_target FROM goals")
    goals_data = cursor.fetchone()
    cursor.execute("SELECT SUM(amount_saved) as total_saved FROM savings_logs")
    saved_data = cursor.fetchone()
    cursor.close(); conn.close()
    tt = goals_data["total_target"] if goals_data["total_target"] else 0
    ts = saved_data["total_saved"] if saved_data["total_saved"] else 0
    return {"total_saved": ts, "total_target": tt, "total_goals": goals_data["total_goals"] if goals_data["total_goals"] else 0, "overall_needed": max(0.0, tt - ts)}

@app.get("/api/v1/analytics/velocity")
def get_savings_velocity():
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT TO_CHAR(created_at, 'YYYY-MM') as month, SUM(amount_saved) as total FROM savings_logs GROUP BY month ORDER BY month ASC LIMIT 6")
    velocity = cursor.fetchall(); cursor.close(); conn.close()
    return velocity

@app.get("/api/v1/all-goals")
def get_all_goals():
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM goals ORDER BY goal_id DESC")
    goals = cursor.fetchall()
    result = []
    for goal in goals:
        cursor.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = %s", (goal['goal_id'],))
        log = cursor.fetchone()
        cs = log["total"] if log["total"] else 0
        pct = min(int((cs / goal['target_price']) * 100), 100)
        result.append({"goal_id": goal['goal_id'], "product_name": goal['product_name'], "target_price": goal['target_price'], "current_saved": cs, "percentage_complete": pct, "image_url": goal.get('image_url') or "", "product_link": goal.get('product_link') or ""})
    cursor.close(); conn.close()
    return result

@app.post("/api/v1/log-savings")
def log_savings(log: SavingsLogCreate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO savings_logs (goal_id, amount_saved) VALUES (%s, %s)", (log.goal_id, log.amount_saved))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Money added successfully!"}

@app.post("/api/v1/goals")
def create_goal(goal: GoalCreate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO goals (product_name, target_price, image_url, product_link) VALUES (%s, %s, %s, %s)", (goal.product_name, goal.target_price, goal.image_url, goal.product_link))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Goal created!"}

@app.delete("/api/v1/goals/{goal_id}")
def delete_goal(goal_id: int):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM goals WHERE goal_id = %s", (goal_id,))
    cursor.execute("DELETE FROM savings_logs WHERE goal_id = %s", (goal_id,))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Goal deleted!"}

@app.put("/api/v1/goals/{goal_id}")
def edit_goal(goal_id: int, goal: GoalUpdate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE goals SET product_name = %s, target_price = %s, image_url = %s, product_link = %s WHERE goal_id = %s", (goal.product_name, goal.target_price, goal.image_url, goal.product_link, goal_id))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Goal updated!"}

@app.get("/api/v1/history")
def get_savings_history():
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT logs.log_id, logs.amount_saved, logs.created_at, goals.product_name FROM savings_logs logs JOIN goals ON logs.goal_id = goals.goal_id ORDER BY logs.log_id DESC')
    logs = cursor.fetchall()
    history = [{"id": l["log_id"], "amount": l["amount_saved"], "date": str(l["created_at"])[:16], "product": l["product_name"]} for l in logs]
    cursor.close(); conn.close()
    return history

@app.post("/api/v1/smart-split")
def smart_split(req: SmartSplitRequest):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM goals")
    all_goals = cursor.fetchall()
    active_goals = []
    for g in all_goals:
        cursor.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = %s", (g['goal_id'],))
        log_data = cursor.fetchone()
        saved = log_data['total'] if log_data['total'] else 0
        if saved < g['target_price']:
            active_goals.append({"goal_id": g['goal_id'], "product_name": g['product_name'], "target_price": g['target_price'], "amount_needed": g['target_price'] - saved})
    if len(active_goals) == 0: 
        cursor.close(); conn.close()
        return {"message": "You have no active goals to fund!"}
    prompt = f"Distribute ₹{req.amount} across these goals: {json.dumps(active_goals)}. Rules: Do NOT allocate more than 'amount_needed' to any goal. Sum MUST equal exactly ₹{req.amount}. Respond ONLY with a valid JSON array matching this format: [{{\"goal_id\": 1, \"allocated_amount\": 500}}]"
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        allocations = json.loads(raw_text)
        for alloc in allocations:
            if alloc['allocated_amount'] > 0:
                cursor.execute("INSERT INTO savings_logs (goal_id, amount_saved) VALUES (%s, %s)", (alloc['goal_id'], alloc['allocated_amount']))
        conn.commit(); cursor.close(); conn.close()
        return {"message": f"Successfully swept ₹{req.amount} to your goals!"}
    except Exception:
        cursor.close(); conn.close()
        return {"error": "AI could not route the sweep."}

@app.get("/api/v1/deal-radar")
def run_deal_radar():
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT product_name, target_price FROM goals")
    all_goals = cursor.fetchall()
    cursor.close(); conn.close()
    if not all_goals: return {"radar_update": "No targets tracked yet."}
    items_list = ", ".join([f"'{g['product_name']}'" for g in all_goals])
    prompt = f"Scan web for live drops on these in India: {items_list}. Give a 2-sentence summary of best discounts."
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=types.GenerateContentConfig(tools=[{"google_search": {}}]))
        return {"radar_update": response.text.strip()}
    except Exception:
        return {"radar_update": "Market is stable today."}

# Boilerplate fallbacks for unused routes
@app.get("/api/v1/coach/{goal_id}")
def get_ai_coach(goal_id: int): return {"advice": "Keep pushing!"}
@app.get("/api/v1/predict/{goal_id}")
def get_prediction(goal_id: int): return {"prediction": "Keep depositing!"}
@app.get("/api/v1/monitor/{goal_id}")
def daily_monitor(goal_id: int): return {"status": "Market stable."}
@app.get("/api/v1/deal-hunter/{goal_id}")
def find_deals(goal_id: int): return {"deal": "No deals.", "link": ""}
@app.get("/api/v1/dupe-hunter/{goal_id}")
def find_dupe(goal_id: int): return {"error": "No dupes."}
@app.get("/api/v1/stock-check/{goal_id}")
def check_stock(goal_id: int): return {"status": "UNKNOWN", "message": "Verify manually."}

# ----------------- NEW: ENERGY SAVER ROUTES -----------------

@app.get("/api/v1/appliances")
def get_appliances():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Fetch appliances AND today's logged hours (if any)
    cursor.execute("""
        SELECT a.appliance_id, a.appliance_name, a.watts, a.min_hours, a.max_hours, 
               COALESCE(l.hours_used, 0) as today_hours 
        FROM appliances a 
        LEFT JOIN appliance_logs l ON a.appliance_id = l.appliance_id AND l.log_date = CURRENT_DATE
        ORDER BY a.watts DESC
    """)
    apps = cursor.fetchall()
    
    for app in apps:
        app['min_monthly_units'] = round((app['watts'] * app['min_hours'] * 30.44) / 1000, 2)
        app['max_monthly_units'] = round((app['watts'] * app['max_hours'] * 30.44) / 1000, 2)
        
    cursor.close(); conn.close()
    return apps

@app.post("/api/v1/energy/today")
def update_daily_log(log: DailyLogUpdate):
    conn = get_db_connection(); cursor = conn.cursor()
    # Upsert: Insert new log for today, or update if it already exists
    cursor.execute("""
        INSERT INTO appliance_logs (appliance_id, log_date, hours_used) 
        VALUES (%s, CURRENT_DATE, %s) 
        ON CONFLICT (appliance_id, log_date) 
        DO UPDATE SET hours_used = EXCLUDED.hours_used
    """, (log.appliance_id, log.hours_used))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Timetable updated."}

@app.get("/api/v1/energy/live-meter")
def get_live_meter(rate: float = 6.50):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    # Sum up all usage for the current month
    cursor.execute("""
        SELECT COALESCE(SUM((a.watts * l.hours_used) / 1000.0), 0) as total_units
        FROM appliance_logs l
        JOIN appliances a ON l.appliance_id = a.appliance_id
        WHERE EXTRACT(MONTH FROM l.log_date) = EXTRACT(MONTH FROM CURRENT_DATE)
          AND EXTRACT(YEAR FROM l.log_date) = EXTRACT(YEAR FROM CURRENT_DATE)
    """)
    result = cursor.fetchone()
    cursor.close(); conn.close()
    
    total_units = round(result['total_units'], 2)
    total_bill = round(total_units * rate, 2)
    return {"accumulated_units": total_units, "current_bill": total_bill}

@app.post("/api/v1/appliances")
def add_appliance(app: ApplianceCreate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO appliances (appliance_name, watts, min_hours, max_hours) VALUES (%s, %s, %s, %s)", 
                   (app.appliance_name, app.watts, app.min_hours, app.max_hours))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Appliance added!"}

@app.delete("/api/v1/appliances/{app_id}")
def delete_appliance(app_id: int):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM appliance_logs WHERE appliance_id = %s", (app_id,))
    cursor.execute("DELETE FROM appliances WHERE appliance_id = %s", (app_id,))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Appliance removed!"}

@app.post("/api/v1/energy/calculate")
def calculate_bill(log: ElectricityLogCreate):
    units_consumed = max(0, log.curr_reading - log.prev_reading)
    total_bill = round(units_consumed * log.rate_per_unit, 2)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO electricity_logs (prev_reading, curr_reading, rate_per_unit, total_bill) VALUES (%s, %s, %s, %s)", 
                   (log.prev_reading, log.curr_reading, log.rate_per_unit, total_bill))
    conn.commit(); cursor.close(); conn.close()
    return {"units": units_consumed, "total_bill": total_bill}

@app.get("/api/v1/energy/coach")
def energy_coach(units: float, bill: float, rate: float):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM appliances")
    apps = cursor.fetchall()
    cursor.close(); conn.close()
    
    theo_max = sum(((a['watts'] * a['max_hours'] * 30.44) / 1000) for a in apps)
    app_list = ", ".join([f"{a['appliance_name']} ({a['watts']}W, max {a['max_hours']}h/day)" for a in apps])
    
    prompt = f"""
    The user lives in Kerala (KSEB). Appliances: {app_list}.
    Max expected usage: {round(theo_max, 1)} units.
    Actual billed: {units} units (₹{bill} at ₹{rate}/unit).
    Give 3 high-impact bullet points to reduce this bill. 
    Format response strictly as a JSON array of 3 strings. Example: ["Tip 1", "Tip 2", "Tip 3"]
    """
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        tips = json.loads(raw_text)
        return {"tips": tips, "theoretical_units": round(theo_max, 1)}
    except Exception:
        return {"tips": ["Turn off phantom loads.", "Clean AC filters.", "Switch to LED bulbs."], "theoretical_units": round(theo_max, 1)}