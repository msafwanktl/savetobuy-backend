from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from google import genai
from google.genai import types # NEW: Needed for sending images to Gemini
import base64 # NEW: For reading the image file
import json # NEW: For cleaning up the AI's response

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect to the new Neon Postgres Database
def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

@app.on_event("startup")
def startup():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Upgraded table creation using Postgres dialect
    cursor.execute('''CREATE TABLE IF NOT EXISTS goals (
        goal_id SERIAL PRIMARY KEY,
        product_name TEXT,
        target_price REAL
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS savings_logs (
        log_id SERIAL PRIMARY KEY,
        goal_id INTEGER,
        amount_saved REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Inject test data if empty
    cursor.execute("SELECT COUNT(*) FROM goals")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO goals (product_name, target_price) VALUES ('Sony Headphones', 50000.0)")
        cursor.execute("INSERT INTO savings_logs (goal_id, amount_saved) VALUES (1, 15000.0)")
    
    conn.commit()
    cursor.close()
    conn.close()

# Pydantic Models
class GoalCreate(BaseModel):
    product_name: str
    target_price: float

class SavingsLogCreate(BaseModel):
    goal_id: int
    amount_saved: float

class GoalUpdate(BaseModel):
    product_name: str
    target_price: float
    
class ImageRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"

@app.get("/api/v1/all-goals")
def get_all_goals():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM goals")
    goals = cursor.fetchall()
    
    result = []
    for goal in goals:
        cursor.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = %s", (goal['goal_id'],))
        log = cursor.fetchone()
        current_saved = log["total"] if log["total"] else 0
        
        percentage = min(int((current_saved / goal['target_price']) * 100), 100)
        
        result.append({
            "goal_id": goal['goal_id'],
            "product_name": goal['product_name'],
            "target_price": goal['target_price'],
            "current_saved": current_saved,
            "percentage_complete": percentage
        })
        
    cursor.close()
    conn.close()
    return result

@app.post("/api/v1/log-savings")
def log_savings(log: SavingsLogCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO savings_logs (goal_id, amount_saved) VALUES (%s, %s)", (log.goal_id, log.amount_saved))
    conn.commit()
    cursor.close()
    conn.close()
    return {"message": "Money added successfully!"}

@app.post("/api/v1/goals")
def create_goal(goal: GoalCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO goals (product_name, target_price) VALUES (%s, %s)", (goal.product_name, goal.target_price))
    conn.commit()
    cursor.close()
    conn.close()
    return {"message": "Goal created!"}

@app.delete("/api/v1/goals/{goal_id}")
def delete_goal(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM goals WHERE goal_id = %s", (goal_id,))
    cursor.execute("DELETE FROM savings_logs WHERE goal_id = %s", (goal_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"message": "Goal and history deleted!"}

@app.put("/api/v1/goals/{goal_id}")
def edit_goal(goal_id: int, goal: GoalUpdate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE goals SET product_name = %s, target_price = %s WHERE goal_id = %s", (goal.product_name, goal.target_price, goal_id))
    conn.commit()
    cursor.close()
    conn.close()
    return {"message": "Goal updated successfully!"}

@app.get("/api/v1/history")
def get_savings_history():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('''
        SELECT logs.log_id, logs.amount_saved, logs.created_at, goals.product_name 
        FROM savings_logs logs
        JOIN goals ON logs.goal_id = goals.goal_id
        ORDER BY logs.log_id DESC
    ''')
    logs = cursor.fetchall()
    
    history = []
    for log in logs:
        history.append({
            "id": log["log_id"],
            "amount": log["amount_saved"],
            "date": str(log["created_at"])[:16],
            "product": log["product_name"]
        })
    cursor.close()
    conn.close()
    return history

@app.get("/api/v1/coach/{goal_id}")
def get_ai_coach(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT product_name, target_price FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    if not goal:
        cursor.close()
        conn.close()
        return {"advice": "Goal not found."}
        
    cursor.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = %s", (goal_id,))
    logs = cursor.fetchone()
    current_saved = logs["total"] if logs["total"] else 0
    
    cursor.close()
    conn.close()
    
    prompt = f"I am saving up for {goal['product_name']} which costs ₹{goal['target_price']}. I currently have ₹{current_saved} saved. Give me a 2-sentence highly motivating, practical financial tip to help me reach my goal faster. Be energetic and concise."
    
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt
        )
        return {"advice": response.text.strip()}
    except Exception as e:
        print(f"AI Error: {e}")
        return {"advice": "Keep pushing! Every rupee counts towards your goal."}
    
    # NEW: The Predictive Timeline Endpoint
@app.get("/api/v1/predict/{goal_id}")
def get_prediction(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Get the Goal Info
    cursor.execute("SELECT product_name, target_price FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    
    # 2. Get the exact history of deposits for this specific goal
    cursor.execute("SELECT amount_saved, created_at FROM savings_logs WHERE goal_id = %s ORDER BY created_at ASC", (goal_id,))
    logs = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    if not goal:
        return {"prediction": "Goal not found."}
        
    if len(logs) == 0:
        return {"prediction": "Add some money first so I can analyze your saving habits!"}
        
    current_saved = sum(log["amount_saved"] for log in logs)
    
    if current_saved >= goal["target_price"]:
        return {"prediction": "You already reached your goal! Go buy it!"}
        
    # 3. Format the history into a sentence for the AI to read
    history_text = ", ".join([f"₹{log['amount_saved']} on {str(log['created_at'])[:10]}" for log in logs])
    
    # 4. Ask Gemini to do the math and forecast the date
    prompt = f"I am saving for a {goal['product_name']} that costs ₹{goal['target_price']}. I have currently saved ₹{current_saved}. Here is my exact deposit history: {history_text}. Based strictly on the frequency and amounts of these past deposits, predict the specific date (or month) I will reach my goal. Give me a 2-sentence encouraging prediction."
    
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt
        )
        return {"prediction": response.text.strip()}
    except Exception as e:
        print(f"AI Error: {e}")
        return {"prediction": "Keep depositing consistently so we can predict your timeline!"}
    
    # NEW: Snap to Save (Vision AI) Endpoint
@app.post("/api/v1/vision/extract-goal")
def extract_goal_from_image(req: ImageRequest):
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        
        # 1. Decode the image string back into actual bytes
        image_bytes = base64.b64decode(req.image_base64)
        
        # 2. Tell Gemini EXACTLY what we want
        prompt = """
        Analyze this image. Find the main product being shown and its price. 
        Respond ONLY with a valid JSON object matching this exact format, with no markdown, no code blocks, and no extra text:
        {"product_name": "Name of Product", "target_price": 0.0}
        """
        
        # 3. Send both the text prompt and the image to the Vision model
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type=req.mime_type)
            ]
        )
        
        # 4. Clean the response and send it back to the app
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw_text)
        
    except Exception as e:
        print(f"Vision AI Error: {e}")
        return {"error": "Could not read the image. Please enter manually."}