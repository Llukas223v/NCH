from dotenv import load_dotenv
import os
import os
print("Current directory:", os.getcwd())
load_dotenv()

bot_token = os.getenv("BOT_TOKEN")

if bot_token:
    print(f"Bot token loaded: {bot_token}")
else:
    print("Bot token not found.")
    
    print(os.getcwd())
print(os.listdir()) #Add this line