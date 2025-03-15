# 1. Imports
import discord
from discord.ext import commands
from discord import app_commands
import datetime
import json
from dotenv import load_dotenv
import os
import logging
import aiohttp
from flask import Flask, request, jsonify
import threading
import asyncio

# 2. Basic setup
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord_bot")

# 3. Environment variables
TOKEN = os.getenv('BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
logger.info(f"Webhook URL configured: {'Yes' if WEBHOOK_URL else 'No'}")

# 4. Discord bot initialization
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 5. Flask app initialization
app = Flask(__name__)

# 6. Data structures
items = {}
earnings = {}
sale_history = []
# Define predefined prices for items
predefined_prices = {
    'Bizarre Bud': 4950,
    'Strange Bud': 1095,
    'Smelly Bud': 735,
    'Sour Diesel Bud': 645,
    'Whacky Bud': 630,
    'Old Bud': 795,
    'Bags of OG Kush': 35,
    'Bags of White Widow': 35,
    'Bag of Sour Diesel': 35,
    'Bag of Pineapple Express': 38,
    'Bag of Khalifa Kush': 69,
    'Bag of Sojokush': 300,
    'OG Kush Joint': 28,
    'White Widow Joint': 28,
    'Sour Diesel Joint': 30,
    'Pineapple Express Joint': 35,
    'Khalifa Kush Joint': 60,
    'Sojokush Joint': 130
}

item_list = list(predefined_prices.keys())

# 7. Utility functions
def save_data():
    with open("data.json", "w") as f:
        json.dump({"items": items, "earnings": earnings, "sale_history": sale_history}, f)
    logger.info("Data saved to data.json")

def load_data():
    try:
        with open("data.json", "r") as f:
            data = json.load(f)
            items.update(data.get("items", {}))
            earnings.update(data.get("earnings", {}))
            if "sale_history" in data:
                sale_history.extend(data["sale_history"])
    except FileNotFoundError:
        logger.info("No data file found. Starting with empty data.")

# 8. Webhook handling
async def process_stock_addition(item_name, quantity, price):
    """Process stock being added to the shop"""
    if item_name not in item_list:
        logger.warning(f"Invalid item '{item_name}', it's not in the approved list.")
        return

    date = str(datetime.date.today())
    if item_name not in items:
        items[item_name] = []

    items[item_name].append({
        "person": "WebhookAdd",  # or some default name
        "quantity": quantity,
        "date": date,
        "price": price
    })
    save_data()
    logger.info(f"Added {quantity} {item_name} at {price} each via webhook")

async def process_sale(item_name, quantity, price):
    """Process a sale from the shop"""
    logger.info(f"Processing sale: {quantity}x {item_name} at ${price} each")
    
    if item_name not in items:
        logger.error(f"Item {item_name} not found in stock")
        return
        
    total_quantity = sum(entry['quantity'] for entry in items[item_name])
    logger.info(f"Current stock for {item_name}: {total_quantity}")
    
    if total_quantity < quantity:
        logger.error(f"Not enough stock for {item_name}")
        return

    # Process the sale
    remaining = quantity
    sorted_entries = sorted(items[item_name], key=lambda x: x["date"])
    
    for entry in sorted_entries:
        if remaining <= 0:
            break
            
        sell_amount = min(entry["quantity"], remaining)
        remaining -= sell_amount
        
        logger.info(f"Taking {sell_amount} from {entry['person']}'s stock of {entry['quantity']}")
        
        entry["quantity"] -= sell_amount
        
        # Update earnings
        if entry["person"] not in earnings:
            earnings[entry["person"]] = 0
        earnings[entry["person"]] += sell_amount * entry["price"]
        logger.info(f"Added ${sell_amount * entry['price']} to {entry['person']}'s earnings")

    # Clean up empty entries
    items[item_name] = [entry for entry in items[item_name] if entry["quantity"] > 0]
    save_data()
    logger.info(f"Completed sale of {quantity} {item_name}")
    
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        logger.error("No data received in webhook")
        return jsonify({'status': 'error'}), 400

    message = data.get('content', '')
    logger.info(f"Webhook received: {message}")

    try:
        parts = message.split(' ')
        quantity = int(parts[0].replace('x', ''))
        
        if "purchased" in message.lower():
            # Handle purchase
            item_name = ' '.join(parts[1:]).split(' purchased')[0].strip()
            price = int(parts[-1].replace('$', '').replace(',', ''))
            asyncio.run(process_sale(item_name, quantity, price))
            
        elif "added to shop" in message.lower():
            # Handle stock addition
            item_name = ' '.join(parts[1:]).split(' added to shop')[0].strip()
            price = int(parts[-1].replace('$', '').replace(',', ''))
            asyncio.run(process_stock_addition(item_name, quantity, price))
            
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 400

# 9. Flask thread setup
def run_flask():
    app.run(port=5000, use_reloader=False)

# 10. Bot event handlers
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    await bot.tree.sync()
    logger.info("Commands synced")

# 11. Bot commands
# ... your existing commands ...

# 12. Main execution
if __name__ == "__main__":
    # Load data
    load_data()
    
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run the bot
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        save_data()