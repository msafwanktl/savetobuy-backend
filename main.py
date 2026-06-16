from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
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
    try:
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
            room_name TEXT DEFAULT 'General'
        )''')
        cursor.execute("ALTER TABLE appliances ADD COLUMN IF NOT EXISTS room_name TEXT DEFAULT 'General'")
        
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

        cursor.execute('''CREATE TABLE IF NOT EXISTS solar_installations (
            id SERIAL PRIMARY KEY,
            capacity_kw REAL,
            daily_yield_per_kw REAL DEFAULT 4.0
        )''')
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print("Database startup error:", e)

# ----------------- PYDANTIC MODELS -----------------

class GoalCreate(BaseModel): product_name: str; target_price: float; image_url: str = ""; product_link: str = ""
class GoalUpdate(BaseModel): product_name: str; target_price: float; image_url: str = ""; product_link: str = ""
class SavingsLogCreate(BaseModel): goal_id: int; amount_saved: float
class SmartSplitRequest(BaseModel): amount: float
class ApplianceCreate(BaseModel): appliance_name: str; watts: float; room_name: str
class ApplianceUpdate(BaseModel): appliance_name: str; watts: float; room_name: str
class ElectricityLogCreate(BaseModel): prev_reading: float; curr_reading: float; rate_per_unit: float
class DailyLogUpdate(BaseModel): appliance_id: int; hours_used: float
class SolarCreate(BaseModel): capacity_kw: float; daily_yield_per_kw: float = 4.0

# ----------------- WEALTHRADAR ROUTES -----------------
# (These remain exactly the same)

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
    prompt = f"Distribute ₹{req.amount} across these goals: {json.dumps(active_goals)}. Rules: Do NOT allocate more than 'amount_needed'. Sum MUST equal exactly ₹{req.amount}. Respond ONLY with a valid JSON array matching this format: [{{\"goal_id\": 1, \"allocated_amount\": 500}}]"
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

# ----------------- ENERGY SAVER ROUTES (NOW BULLETPROOF) -----------------

@app.get("/api/v1/appliances")
def get_appliances():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT a.appliance_id, a.appliance_name, a.watts, a.room_name,
                   COALESCE(l.hours_used, 0) as today_hours 
            FROM appliances a 
            LEFT JOIN appliance_logs l ON a.appliance_id = l.appliance_id AND l.log_date = CURRENT_DATE
            ORDER BY a.room_name ASC, a.watts DESC
        """)
        apps = cursor.fetchall()
        cursor.close(); conn.close()
        return apps
    except Exception as e:
        print("Error fetching appliances:", e)
        return []

@app.post("/api/v1/energy/today")
def update_daily_log(log: DailyLogUpdate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO appliance_logs (appliance_id, log_date, hours_used) 
        VALUES (%s, CURRENT_DATE, %s) 
        ON CONFLICT (appliance_id, log_date) 
        DO UPDATE SET hours_used = EXCLUDED.hours_used
    """, (log.appliance_id, log.hours_used))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Timetable updated."}

@app.get("/api/v1/energy/solar")
def get_solar_config():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT capacity_kw, daily_yield_per_kw FROM solar_installations LIMIT 1")
        config = cursor.fetchone()
        cursor.close(); conn.close()
        if config: return config
        return {"capacity_kw": 0.0, "daily_yield_per_kw": 4.0}
    except Exception as e:
        return {"capacity_kw": 0.0, "daily_yield_per_kw": 4.0}

@app.post("/api/v1/energy/solar")
def set_solar_config(solar: SolarCreate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM solar_installations")
    cursor.execute("INSERT INTO solar_installations (capacity_kw, daily_yield_per_kw) VALUES (%s, %s)", 
                   (solar.capacity_kw, solar.daily_yield_per_kw))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Solar array configured!"}

@app.get("/api/v1/energy/live-meter")
def get_live_meter(rate: float = 6.50):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT COALESCE(SUM((a.watts * l.hours_used) / 1000.0), 0) as gross_units
            FROM appliance_logs l
            JOIN appliances a ON l.appliance_id = a.appliance_id
            WHERE EXTRACT(MONTH FROM l.log_date) = EXTRACT(MONTH FROM CURRENT_DATE)
              AND EXTRACT(YEAR FROM l.log_date) = EXTRACT(YEAR FROM CURRENT_DATE)
        """)
        gross_row = cursor.fetchone()
        gross_units = gross_row['gross_units'] if gross_row and gross_row['gross_units'] else 0.0
        
        cursor.execute("SELECT EXTRACT(DAY FROM CURRENT_DATE) as days_passed")
        days_passed = cursor.fetchone()['days_passed']
        
        cursor.execute("SELECT capacity_kw, daily_yield_per_kw FROM solar_installations LIMIT 1")
        solar_conf = cursor.fetchone()
        solar_units = 0.0
        if solar_conf:
            solar_units = solar_conf['capacity_kw'] * solar_conf['daily_yield_per_kw'] * days_passed

        cursor.close(); conn.close()
        
        gross_units = round(gross_units, 2)
        solar_units = round(solar_units, 2)
        net_units = round(gross_units - solar_units, 2)
        current_bill = round(net_units * rate, 2) if net_units > 0 else 0.0
        solar_savings = round(solar_units * rate, 2)
        
        return {
            "gross_units": gross_units, 
            "solar_units": solar_units,
            "net_units": net_units,
            "current_bill": current_bill,
            "solar_savings_rs": solar_savings
        }
    except Exception as e:
        print("Error in live-meter:", e)
        return {"gross_units": 0, "solar_units": 0, "net_units": 0, "current_bill": 0, "solar_savings_rs": 0}

@app.get("/api/v1/energy/room-summary")
def get_room_summary():
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT a.room_name, COALESCE(SUM((a.watts * l.hours_used) / 1000.0), 0) as total_units
            FROM appliance_logs l
            JOIN appliances a ON l.appliance_id = a.appliance_id
            WHERE EXTRACT(MONTH FROM l.log_date) = EXTRACT(MONTH FROM CURRENT_DATE)
              AND EXTRACT(YEAR FROM l.log_date) = EXTRACT(YEAR FROM CURRENT_DATE)
            GROUP BY a.room_name
            ORDER BY total_units DESC
        """)
        summary = cursor.fetchall()
        cursor.close(); conn.close()
        for row in summary: row['total_units'] = round(row['total_units'], 2)
        return summary
    except Exception as e:
        return []

@app.get("/api/v1/energy/daily-history")
def get_daily_history():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT TO_CHAR(l.log_date, 'YYYY-MM-DD') as log_date, 
                   COALESCE(SUM((a.watts * l.hours_used) / 1000.0), 0) as daily_units
            FROM appliance_logs l
            JOIN appliances a ON l.appliance_id = a.appliance_id
            WHERE EXTRACT(MONTH FROM l.log_date) = EXTRACT(MONTH FROM CURRENT_DATE)
              AND EXTRACT(YEAR FROM l.log_date) = EXTRACT(YEAR FROM CURRENT_DATE)
            GROUP BY l.log_date
            ORDER BY l.log_date DESC
        """)
        history = cursor.fetchall()
        cursor.close(); conn.close()
        for row in history: row['daily_units'] = round(row['daily_units'], 2)
        return history
    except Exception as e:
        return []

@app.post("/api/v1/appliances")
def add_appliance(app: ApplianceCreate):
    conn = get_db_connection(); cursor = conn.cursor()
    room = app.room_name.strip() if app.room_name else "General"
    cursor.execute("INSERT INTO appliances (appliance_name, watts, room_name) VALUES (%s, %s, %s)", 
                   (app.appliance_name, app.watts, room))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Appliance added!"}

@app.put("/api/v1/appliances/{app_id}")
def update_appliance(app_id: int, app: ApplianceUpdate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE appliances SET appliance_name = %s, watts = %s, room_name = %s WHERE appliance_id = %s", 
                   (app.appliance_name, app.watts, app.room_name, app_id))
    conn.commit(); cursor.close(); conn.close()
    return {"message": "Appliance updated successfully!"}

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
    cursor.execute("""
        SELECT a.appliance_name, a.watts, COALESCE(SUM(l.hours_used), 0) as month_hours
        FROM appliances a
        LEFT JOIN appliance_logs l ON a.appliance_id = l.appliance_id 
        AND EXTRACT(MONTH FROM l.log_date) = EXTRACT(MONTH FROM CURRENT_DATE)
        GROUP BY a.appliance_id
    """)
    apps = cursor.fetchall()
    
    cursor.execute("SELECT capacity_kw FROM solar_installations LIMIT 1")
    solar = cursor.fetchone()
    cursor.close(); conn.close()
    
    solar_str = f"User also has a {solar['capacity_kw']}kW solar array." if solar and solar['capacity_kw'] > 0 else ""
    app_list = ", ".join([f"{a['appliance_name']} ({a['watts']}W, logged {round(a['month_hours'],1)}h this month)" for a in apps])
    
    prompt = f"""
    The user lives in Kerala (KSEB). They logged these appliances this month: {app_list}. {solar_str}
    Their actual billed usage: {units} units (₹{bill} at ₹{rate}/unit).
    Give 3 bullet points to reduce this bill. 
    Format response strictly as JSON array of 3 strings: ["Tip 1", "Tip 2", "Tip 3"]
    """
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return {"tips": json.loads(response.text.strip().replace("```json", "").replace("```", ""))}
    except Exception:
        return {"tips": ["Shift heavy appliance usage to daytime to maximize solar.", "Clean AC filters.", "Turn off phantom loads."]}

# Boilerplate fallbacks
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