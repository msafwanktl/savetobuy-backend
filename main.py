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
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS goals (
        goal_id SERIAL PRIMARY KEY,
        product_name TEXT,
        target_price REAL
    )''')
    
    cursor.execute('ALTER TABLE goals ADD COLUMN IF NOT EXISTS image_url TEXT')
    cursor.execute('ALTER TABLE goals ADD COLUMN IF NOT EXISTS product_link TEXT')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS savings_logs (
        log_id SERIAL PRIMARY KEY,
        goal_id INTEGER,
        amount_saved REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    cursor.close()
    conn.close()

# ----------------- PYDANTIC MODELS -----------------

class GoalCreate(BaseModel):
    product_name: str
    target_price: float
    image_url: str = ""
    product_link: str = ""

class GoalUpdate(BaseModel):
    product_name: str
    target_price: float
    image_url: str = ""
    product_link: str = ""

class SavingsLogCreate(BaseModel):
    goal_id: int
    amount_saved: float
    
class ImageRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"

class SmartSplitRequest(BaseModel):
    amount: float

class LinkRequest(BaseModel):
    link: str

# ----------------- CORE DATABASE ROUTES -----------------

@app.get("/api/v1/all-goals")
def get_all_goals():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM goals ORDER BY goal_id DESC")
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
            "percentage_complete": percentage,
            "image_url": goal.get('image_url') or "",
            "product_link": goal.get('product_link') or ""
        })
        
    cursor.close()
    conn.close()
    return result

@app.post("/api/v1/log-savings")
def log_savings(log: SavingsLogCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO savings_logs (goal_id, amount_saved) VALUES (%s, %s)", 
        (log.goal_id, log.amount_saved)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"message": "Money added successfully!"}

@app.post("/api/v1/goals")
def create_goal(goal: GoalCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO goals (product_name, target_price, image_url, product_link) VALUES (%s, %s, %s, %s)", 
        (goal.product_name, goal.target_price, goal.image_url, goal.product_link)
    )
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
    cursor.execute(
        "UPDATE goals SET product_name = %s, target_price = %s, image_url = %s, product_link = %s WHERE goal_id = %s", 
        (goal.product_name, goal.target_price, goal.image_url, goal.product_link, goal_id)
    )
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
    for l in logs:
        history.append({
            "id": l["log_id"], 
            "amount": l["amount_saved"], 
            "date": str(l["created_at"])[:16], 
            "product": l["product_name"]
        })
        
    cursor.close()
    conn.close()
    return history

# ----------------- AI SUPERPOWER ROUTES -----------------

@app.get("/api/v1/coach/{goal_id}")
def get_ai_coach(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT product_name, target_price FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    
    cursor.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = %s", (goal_id,))
    log_data = cursor.fetchone()
    current_saved = log_data["total"] if log_data["total"] else 0
    cursor.close()
    conn.close()
    
    prompt = f"I am saving up for {goal['product_name']} which costs ₹{goal['target_price']}. I currently have ₹{current_saved} saved. Give me a 2-sentence highly motivating, practical financial tip. Be energetic."
    
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return {"advice": response.text.strip()}
    except Exception as e:
        return {"advice": "Keep pushing! Every rupee counts towards your goal."}
    
@app.get("/api/v1/predict/{goal_id}")
def get_prediction(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT product_name, target_price FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    
    cursor.execute("SELECT amount_saved, created_at FROM savings_logs WHERE goal_id = %s ORDER BY created_at ASC", (goal_id,))
    logs = cursor.fetchall()
    cursor.close()
    conn.close()
    
    if len(logs) == 0: 
        return {"prediction": "Add some money first so I can analyze your saving habits!"}
        
    current_saved = sum(log["amount_saved"] for log in logs)
    if current_saved >= goal["target_price"]: 
        return {"prediction": "You already reached your goal! Go buy it!"}
    
    history_text = ", ".join([f"₹{log['amount_saved']} on {str(log['created_at'])[:10]}" for log in logs])
    prompt = f"I am saving for a {goal['product_name']} that costs ₹{goal['target_price']}. I have currently saved ₹{current_saved}. Here is my deposit history: {history_text}. Predict the specific date (or month) I will reach my goal in 2 sentences."
    
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return {"prediction": response.text.strip()}
    except Exception as e:
        return {"prediction": "Keep depositing consistently so we can predict your timeline!"}

@app.post("/api/v1/vision/extract-goal")
def extract_goal_from_image(req: ImageRequest):
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        image_bytes = base64.b64decode(req.image_base64)
        prompt = """
        Analyze this image. Find the main product being shown and its price. 
        Respond ONLY with a valid JSON object matching this exact format, with no markdown, no code blocks, and no extra text:
        {"product_name": "Name of Product", "target_price": 0.0}
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=[prompt, types.Part.from_bytes(data=image_bytes, mime_type=req.mime_type)]
        )
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw_text)
    except Exception as e:
        return {"error": "Could not read the image."}

@app.get("/api/v1/deal-hunter/{goal_id}")
def find_deals(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT product_name, target_price FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    cursor.close()
    conn.close()
    
    prompt = f"""
    Search live internet for current prices of '{goal['product_name']}' in India. Target was ₹{goal['target_price']}. 
    Respond ONLY with a valid JSON object matching this exact format, with no markdown, no extra text:
    {{"deal": "A brief exciting 2-sentence response detailing lowest price.", "link": "Direct web URL to product"}}
    """
    
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt, 
            config=types.GenerateContentConfig(tools=[{"google_search": {}}])
        )
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        parsed_data = json.loads(raw_text)
        return {"deal": parsed_data.get("deal"), "link": parsed_data.get("link", "")}
    except Exception as e:
        return {"deal": "Could not find deals right now.", "link": ""}

@app.get("/api/v1/dupe-hunter/{goal_id}")
def find_dupe(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT product_name, target_price FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    cursor.close()
    conn.close()
        
    prompt = f"""
    Search live internet for a highly-rated, significantly cheaper alternative (dupe) to '{goal['product_name']}' (₹{goal['target_price']}) in India.
    Respond ONLY with a valid JSON object matching this exact format, with no markdown, no extra text:
    {{"dupe_name": "Name", "dupe_price": 0.0, "reason": "1-sentence reason.", "link": "URL"}}
    """
    
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt, 
            config=types.GenerateContentConfig(tools=[{"google_search": {}}])
        )
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw_text)
    except Exception as e:
        return {"error": "Could not find a cheaper alternative right now."}

@app.post("/api/v1/smart-split")
def smart_split(req: SmartSplitRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM goals")
    all_goals = cursor.fetchall()
    
    active_goals = []
    for g in all_goals:
        cursor.execute("SELECT SUM(amount_saved) as total FROM savings_logs WHERE goal_id = %s", (g['goal_id'],))
        log_data = cursor.fetchone()
        saved = log_data['total'] if log_data['total'] else 0
        
        if saved < g['target_price']:
            active_goals.append({
                "goal_id": g['goal_id'], 
                "product_name": g['product_name'], 
                "target_price": g['target_price'], 
                "amount_needed": g['target_price'] - saved
            })
            
    if len(active_goals) == 0: 
        cursor.close()
        conn.close()
        return {"message": "You have no active goals to fund!"}

    prompt = f"""
    Distribute ₹{req.amount} across these goals: {json.dumps(active_goals)}. 
    Rules: Do NOT allocate more than 'amount_needed' to any goal. The sum of all allocated amounts MUST equal exactly ₹{req.amount}.
    Respond ONLY with a valid JSON array of objects matching this format, with no markdown or backticks:
    [{{"goal_id": 1, "allocated_amount": 500}}]
    """
    
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        allocations = json.loads(raw_text)
        
        for alloc in allocations:
            if alloc['allocated_amount'] > 0:
                cursor.execute("INSERT INTO savings_logs (goal_id, amount_saved) VALUES (%s, %s)", (alloc['goal_id'], alloc['allocated_amount']))
        conn.commit()
        cursor.close()
        conn.close()
        return {"message": f"Successfully split ₹{req.amount} across your goals!"}
    except Exception as e:
        cursor.close()
        conn.close()
        return {"error": "AI could not calculate the split."}

@app.post("/api/v1/link/extract-goal")
def extract_from_link(req: LinkRequest):
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        prompt = f"""
        The user wants to buy the product found at this link: {req.link}
        Use Google Search to find out what this product is, its current price in INR, and find a direct public image URL (.jpg or .png) of this product.
        If you cannot find an image URL, return an empty string "".
        Respond ONLY with a valid JSON object matching this exact format, with no markdown, no code blocks:
        {{"product_name": "Name", "target_price": 0.0, "image_url": "https://..."}}
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(tools=[{"google_search": {}}])
        )
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(raw_text)
    except Exception as e:
        return {"error": "Could not read the product link due to security blocks. Please enter details manually."}

@app.get("/api/v1/monitor/{goal_id}")
def daily_monitor(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT product_name, target_price FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not goal:
        return {"status": "Goal not found."}
        
    prompt = f"""
    I am saving up for a '{goal['product_name']}'. My original target price was ₹{goal['target_price']}. 
    Search Google for the current market status and pricing of '{goal['product_name']}' in India today (Amazon, Flipkart, News).
    Has the price dropped? Is there a flash sale? 
    Respond ONLY with a valid JSON object matching this exact format, with no markdown:
    {{"status": "A 2-sentence daily market update focusing on price drops, sales, or stock status."}}
    """
    
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(tools=[{"google_search": {}}])
        )
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        parsed_data = json.loads(raw_text)
        return {"status": parsed_data.get("status", "Market is stable today.")}
    except Exception as e:
        return {"status": "Could not fetch the daily market update right now."}
    
@app.get("/api/v1/stock-check/{goal_id}")
def check_stock(goal_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT product_name, product_link FROM goals WHERE goal_id = %s", (goal_id,))
    goal = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not goal:
        return {"error": "Goal not found."}
        
    prompt = f"""
    Search the live internet for the current stock availability of '{goal['product_name']}' in India.
    If this link is provided, prioritize checking it: {goal.get('product_link', 'No specific link provided')}.
    Is it currently in stock, out of stock, or low in stock?
    Respond ONLY with a valid JSON object matching this exact format, with no markdown:
    {{"status": "IN_STOCK" or "OUT_OF_STOCK" or "UNKNOWN", "message": "1-sentence explanation."}}
    """
    
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(tools=[{"google_search": {}}])
        )
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        parsed_data = json.loads(raw_text)
        return parsed_data
    except Exception as e:
        return {"status": "UNKNOWN", "message": "Could not verify stock right now."}