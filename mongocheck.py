# filename: check_backups.py
from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
mongo_uri = os.getenv("MONGO_URI")

# Connect to MongoDB
client = MongoClient(mongo_uri)
db = client["NCHBot"]

# Check backup collection
backup_count = db.backups.count_documents({})
print(f"Found {backup_count} backups in MongoDB")

if backup_count > 0:
    # Show the most recent backup
    latest = db.backups.find_one(sort=[("date", -1)])
    print(f"Latest backup: {latest['date']}")
    print(f"Filename: {latest.get('filename', 'Not specified')}")