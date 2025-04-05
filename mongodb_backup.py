# mongodb_backup_standalone.py - Run this separately, not part of your bot
import os
import json
import datetime
from pymongo import MongoClient
import traceback
from urllib.parse import quote_plus  # For URL encoding the password

# Properly encode password
username = "Lukas223v"
password = quote_plus("<Ceww8pgnr>")  # URL encode the password

# Build connection string with encoded credentials
MONGO_URI = f"mongodb+srv://{username}:{password}@nchbot.yv5rp.mongodb.net"
DB_NAME = "NCHBot"

try:
    print("Connecting to MongoDB...")
    
    # Connect without the database in the URI
    client = MongoClient(MONGO_URI)
    
    # Verify connection
    client.admin.command('ping')  # Simple command to verify connection
    print("Connected successfully!")
    
    # Access the database
    db = client[DB_NAME]
    
    # Create backup data structure
    backup = {
        "items": {},
        "settings": {}
    }
    
    # Backup items collection
    print("Backing up items collection...")
    for item in db.items.find():
        if "_id" in item and "entries" in item:
            item_id = str(item["_id"])  # Convert ObjectId to string if needed
            backup["items"][item_id] = item["entries"]
    
    # Backup settings collection
    print("Backing up settings collection...")
    for setting in db.settings.find():
        if "_id" in setting and "data" in setting:
            setting_id = str(setting["_id"])
            backup["settings"][setting_id] = setting["data"]
    
    # Save to file with timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"mongodb_backup_{timestamp}.json"
    
    with open(backup_file, "w") as f:
        json.dump(backup, f, indent=2, default=str)
    
    print(f"✅ Backup saved to {backup_file}")
    
except Exception as e:
    print(f"❌ Error: {e}")
    traceback.print_exc()