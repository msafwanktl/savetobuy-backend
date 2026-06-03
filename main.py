from fastapi import FastAPI
from pydantic import BaseModel
import sqlite3
import google.generativeai as genai
import os # NEW: This lets Python read secret server variables

app = FastAPI()

# NEW: Securely pull the key from the server's hidden vault
api_key = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=api_key)

# Allow the app to talk to the server safely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 1. Database Connection ---
def get_db():
    # This creates a file called 'savetobuy.db' in your folder
    conn = sqlite3.connect("savetobuy.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row # Lets us access columns by name
    return conn

# --- 2. Create Tables on Startup ---
@app.on_event("startup")
def startup():
    db = get_db()
    # Create the goals table
    db.execute('''CREATE TABLE IF NOT EXISTS goals (
        goal_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT,
        target_price REAL
    )''')
    
    # UPGRADED: We added the created_at column natively here!
    db.execute('''CREATE TABLE IF NOT EXISTS savings_logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id INTEGER,
        amount_saved REAL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Inject test data if empty
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM goals")
    if cursor.fetchone()[0] == 0:
        db.execute("INSERT INTO goals (product_name, target_price) VALUES ('Sony Headphones', 50000.0)")
        db.execute("INSERT INTO savings_logs (goal_id, amount_saved) VALUES (1, 15000.0)")
    db.commit()

# --- 3. Data Models ---
class GoalInput(BaseModel):
    product_name: str
    target_price: float

class GoalUpdate(BaseModel):
    product_name: str
    target_price: float

class SavingsEntry(BaseModel):
    goal_id: int
    amount_saved: float

# --- 4. The Endpoints (API) ---

# NEW: Endpoint to save a brand new product
@app.post("/api/v1/goals")
def add_goal(goal: GoalInput):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO goals (product_name, target_price) VALUES (?, ?)", 
                (goal.product_name, goal.target_price))
    db.commit()
    return {"status": "success", "new_goal_id": cursor.lastrowid}

# UPDATED: Endpoint to log money into the SQL database
@app.post("/api/v1/log-savings")
def log_savings(entry: SavingsEntry):
    db = get_db()
    db.execute("INSERT INTO savings_logs (goal_id, amount_saved) VALUES (?, ?)", 
            (entry.goal_id, entry.amount_saved))
    db.commit()
    return {"status": "success"}

# UPDATED: Endpoint to calculate progress using SQL MATH
@app.get("/api/v1/goal-progress/{goal_id}")
def get_progress(goal_id: int):
    db = get_db()
    
    # 1. Get the target price
    goal = db.execute("SELECT * FROM goals WHERE goal_id = ?", (goal_id,)).fetchone()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    
    # 2. Get the total saved money (SQL SUM)
    saved_row = db.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = ?", (goal_id,)).fetchone()
    current_saved = saved_row["total"] if saved_row["total"] else 0.0
    
    # 3. Calculate percentage
    percentage = min((current_saved / goal["target_price"]) * 100, 100)
    
    return {
        "goal_id": goal["goal_id"],
        "product_name": goal["product_name"],
        "target_price": goal["target_price"],
        "current_saved": current_saved,
        "percentage_complete": round(percentage, 2)
    }
    
    # NEW: Endpoint to get ALL goals for the dashboard
@app.get("/api/v1/all-goals")
def get_all_goals():
    db = get_db()
    
    # 1. Grab every single goal from the database
    goals = db.execute("SELECT * FROM goals").fetchall()
    
    dashboard_data = []
    
    # 2. Loop through them and calculate the progress for each one
    for goal in goals:
        goal_id = goal["goal_id"]
        saved_row = db.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = ?", (goal_id,)).fetchone()
        current_saved = saved_row["total"] if saved_row["total"] else 0.0
        percentage = min((current_saved / goal["target_price"]) * 100, 100)
        
        dashboard_data.append({
            "goal_id": goal_id,
            "product_name": goal["product_name"],
            "target_price": goal["target_price"],
            "current_saved": current_saved,
            "percentage_complete": round(percentage, 2)
        })
        
    # 3. Send the entire list back to the app
    return dashboard_data

# NEW: Endpoint to delete a goal and its history
@app.delete("/api/v1/goals/{goal_id}")
def delete_goal(goal_id: int):
    db = get_db()
    # Delete the product itself
    db.execute("DELETE FROM goals WHERE goal_id = ?", (goal_id,))
    # Delete all the money logs associated with it so we don't leave ghost data
    db.execute("DELETE FROM savings_logs WHERE goal_id = ?", (goal_id,))
    db.commit()
    
    return {"status": "success", "message": f"Goal {goal_id} deleted"}

@app.put("/api/v1/goals/{goal_id}")
def update_goal(goal_id: int, goal: GoalUpdate):
    db = get_db()
    db.execute("UPDATE goals SET product_name = ?, target_price = ? WHERE goal_id = ?", 
            (goal.product_name, goal.target_price, goal_id))
    db.commit()
    return {"status": "success", "message": "Goal updated"}

# NEW: Endpoint to get a ledger of all money saved
@app.get("/api/v1/history")
def get_savings_history():
    db = get_db()
    logs = db.execute('''
        SELECT logs.log_id, logs.amount_saved, logs.created_at, goals.product_name 
        FROM savings_logs logs
        JOIN goals ON logs.goal_id = goals.goal_id
        ORDER BY logs.log_id DESC
    ''').fetchall()
    
    history = []
    for log in logs:
        history.append({
            "id": log["log_id"],
            "amount": log["amount_saved"],
            "date": log["created_at"],
            "product": log["product_name"]
        })
    return history

# NEW: The AI Financial Coach Endpoint
@app.get("/api/v1/coach/{goal_id}")
def get_ai_coach(goal_id: int):
    db = get_db()
    # 1. Fetch the specific goal
    goal = db.execute("SELECT product_name, target_price FROM goals WHERE goal_id = ?", (goal_id,)).fetchone()
    if not goal:
        return {"advice": "Goal not found."}
        
    # 2. Fetch the current savings total
    logs = db.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = ?", (goal_id,)).fetchone()
    current_saved = logs["total"] if logs["total"] else 0
    
    # 3. Build the prompt for the AI
    prompt = f"I am saving up for {goal['product_name']} which costs ₹{goal['target_price']}. I currently have ₹{current_saved} saved. Give me a 2-sentence highly motivating, practical financial tip to help me reach my goal faster. Be energetic and concise."
    
    try:
        # 4. Generate the response
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return {"advice": response.text.strip()}
    except Exception as e:
        return {"advice": "Keep pushing! Every rupee counts towards your goal."}