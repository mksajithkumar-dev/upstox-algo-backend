from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import asyncio
import math

app = FastAPI()

# ഡാറ്റ മോഡലുകൾ
class AlgoConfig(BaseModel):
    token: str
    instrument_key: str
    expiry_date: str
    qty: int
    sl_points: float
    target_points: float
    trail_step: float
    is_paper_trade: bool

# ഗ്ലോബൽ സ്റ്റേറ്റ് വേരിയബിളുകൾ
algo_state = {
    "is_running": False,
    "status": "സെർവർ റെഡിയാണ്",
    "spot_ltp": 0.0,
    "spot_vwap": 0.0,
    "spot_rsi": 0.0,
    "atm_strike": 0,
    "is_position_open": False,
    "active_symbol": "ഇല്ല",
    "entry_price": 0.0,
    "current_ltp": 0.0,
    "sl_price": 0.0,
    "target_price": 0.0,
    "pnl": 0.0,
    "today_trades": 0,
    "today_pnl": 0.0
}

current_config = None
active_option_key = ""
initial_sl = 0.0
highest_ltp = 0.0
step_moved = 0

@app.get("/")
def home():
    return {"message": "Option Algo Cloud Server is Running!"}

@app.post("/start-algo")
def start_algo(config: AlgoConfig):
    global algo_state, current_config
    current_config = config
    algo_state["is_running"] = True
    algo_state["status"] = "അൽഗോ ക്ലൗഡിൽ ആക്ടീവ് ആണ്..."
    
    # ബാക്ക്ഗ്രൗണ്ടിൽ അൽഗോ ലൂപ്പ് സ്റ്റാർട്ട് ചെയ്യുന്നു
    asyncio.create_task(run_algo_loop())
    return {"status": "success", "message": "Algo started on cloud"}

@app.post("/stop-algo")
def stop_algo():
    global algo_state
    algo_state["is_running"] = False
    algo_state["status"] = "അൽഗോ നിർത്തലാക്കിയിരിക്കുന്നു"
    return {"status": "success", "message": "Algo stopped"}

@app.get("/status")
def get_status():
    return algo_state

# സെർവറിലെ പ്രധാന അൽഗോ ലൂപ്പ് (ഓരോ 60 സെക്കൻഡിലും Spot സ്കാൻ ചെയ്യുന്നു)
async def run_algo_loop():
    global algo_state, current_config
    while algo_state["is_running"]:
        try:
            if not algo_state["is_position_open"] and current_config:
                await scan_market()
        except Exception as e:
            algo_state["status"] = f"ലൂപ്പ് പിശക്: {str(e)}"
        
        await asyncio.sleep(60)

async def scan_market():
    global algo_state, current_config
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {current_config.token}'
    }
    
    # 1. Spot Candle Fetch
    url = f"https://api.upstox.com/v2/historical-candle/intraday/NSE_INDEX%7CNifty%2050/1minute"
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        return
    
    candles_data = res.json().get('data', {}).get('candles', [])
    if len(candles_data) < 20:
        return
    
    # Simple calculation simulation for cloud
    current_spot = candles_data[0][4] # Close price
    vwap = current_spot - 5 # Simplified vwap check
    
    algo_state["spot_ltp"] = current_spot
    algo_state["atm_strike"] = round(current_spot / 50) * 50
    
    # സിഗ്നൽ ഒത്തുവന്നാൽ (ഉദാഹരണത്തിന് Spot > VWAP)
    if current_spot > vwap:
        await open_position("CE")

async def open_position(option_type: str):
    global algo_state, current_config, active_option_key, initial_sl, highest_ltp, step_moved
    
    strike = algo_state["atm_strike"]
    active_option_key = f"NSE_FO|PAPER_{strike}_{option_type}"
    entry_p = 120.0 # ഫേക്ക് അല്ലെങ്കിൽ ഫെച്ച് ചെയ്ത പ്രീമിയം
    
    algo_state["is_position_open"] = True
    algo_state["active_symbol"] = f"NIFTY {strike} {option_type}"
    algo_state["entry_price"] = entry_p
    algo_state["current_ltp"] = entry_p
    highest_ltp = entry_p
    step_moved = 0
    
    initial_sl = entry_p - current_config.sl_points
    algo_state["sl_price"] = initial_sl
    algo_state["target_price"] = entry_p + current_config.target_points
    algo_state["today_trades"] += 1
    
    algo_state["status"] = f"പൊസിഷൻ ഓപ്പൺ ചെയ്തു: {algo_state['active_symbol']}"
    
    # പൊസിഷൻ ഓപ്പൺ ആയാൽ ഓരോ 5 സെക്കൻഡിൽ TSL മോണിറ്റർ ചെയ്യുന്നു
    asyncio.create_task(monitor_position())

async def monitor_position():
    global algo_state, current_config, active_option_key, initial_sl, highest_ltp, step_moved
    
    while algo_state["is_position_open"] and algo_state["is_running"]:
        try:
            # ഇവിടെ ലൈവ് ഓപ്ഷൻ LTP ഫെച്ച് ചെയ്യാം (ഉദാഹരണത്തിന് നിലവിലെ പ്രീമിയം ഒരു ചെറിയ വ്യത്യാസത്തിൽ കൂട്ടുന്നു)
            latest_ltp = algo_state["current_ltp"] + 1.0 # സിമുലേഷൻ
            algo_state["current_ltp"] = latest_ltp
            algo_state["pnl"] = (latest_ltp - algo_state["entry_price"]) * current_config.qty
            
            if latest_ltp > highest_ltp:
                highest_ltp = latest_ltp
                
            # TSL ലോജിക്
            profit_pts = latest_ltp - algo_state["entry_price"]
            completed_steps = math.floor(profit_pts / current_config.trail_step)
            if completed_steps > 0:
                new_sl = initial_sl + (completed_steps * current_config.trail_step)
                if new_sl > algo_state["sl_price"]:
                    algo_state["sl_price"] = new_sl
                    step_moved = completed_steps
                    
            # Target / SL Hit Check
            if latest_ltp >= algo_state["target_price"] or latest_ltp <= algo_state["sl_price"]:
                await close_position("TARGET/SL HIT")
                break
                
        except Exception as e:
            print("Monitor error:", e)
            
        await asyncio.sleep(5)

async def close_position(reason: str):
    global algo_state
    pnl = algo_state["pnl"]
    algo_state["today_pnl"] += pnl
    algo_state["is_position_open"] = False
    algo_state["status"] = f"പൊസിഷൻ ക്ലോസ് ചെയ്തു ({reason}). P&L: ₹{pnl:.2f}"
    algo_state["pnl"] = 0.0