import discord
from discord.ext import commands
from discord import app_commands
import datetime
import json
import os
from dotenv import load_dotenv
import logging
import asyncio

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord_bot")

# Load environment variables
load_dotenv('.env')
TOKEN = os.getenv('BOT_TOKEN')
STOCK_CHANNEL_ID = int(os.getenv('STOCK_CHANNEL_ID', 0))

# Bot initialization
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Data structures
items = {}
user_earnings = {}  # Changed from earnings to user_earnings
sale_history = []
stock_message_id = None

# Predefined prices and categories
predefined_prices = {
    'bud_sojokush': 4950,
    'bud_khalifakush': 1095,
    'bud_pineappleexpress': 735,
    'bud_sourdiesel': 735,
    'bud_whitewidow': 645,
    'bud_ogkush': 630,
    'joint_ogkush': 28,
    'joint_whitewidow': 28,
    'joint_sourdiesel': 30,
    'joint_pineappleexpress': 35,
    'joint_khalifakush': 60,
    'joint_sojokush': 130,
    'bagof_ogkush': 35,
    'bagof_whitewidow': 35,
    'bagof_sourdiesel': 35,
    'bagof_pineappleexpress': 38,
    'bagof_khalifakush': 69,
    'bagof_sojokush': 300
}

item_categories = {
    'bud': ['bud_ogkush', 'bud_bizarre', 'bud_strange', 'bud_smelly', 'bud_sourdiesel', 'bud_whacky', 'bud_old', 'bud_pineappleexpress'],
    'joint': ['joint_ogkush', 'joint_whitewidow', 'joint_sourdiesel', 'joint_pineappleexpress', 'joint_khalifakush', 'joint_sojokush'],
    'bag': ['bagof_ogkush', 'bagof_whitewidow', 'bagof_sourdiesel', 'bagof_pineappleexpress', 'bagof_khalifakush', 'bagof_sojokush']
}

item_list = list(predefined_prices.keys())

def save_data():
    """Save current state to data.json with pretty formatting"""
    with open("data.json", "w") as f:
        json.dump({
            "items": items,
            "earnings": user_earnings,
            "sale_history": sale_history
        }, f, indent=2)  # Added indent=2 for pretty formatting
    logger.info("💾 Data saved to data.json")
    
def load_data():
    """Load state from data.json"""
    try:
        with open("data.json", "r") as f:
            data = json.load(f)
            items.update(data.get("items", {}))
            user_earnings.update(data.get("earnings", {}))
            if "sale_history" in data:
                sale_history.extend(data["sale_history"])
        logger.info("📂 Data loaded successfully")
    except FileNotFoundError:
        logger.info("📝 No existing data found. Starting fresh.")
async def is_admin(interaction: discord.Interaction) -> bool:
    """Check if user has admin permissions"""
    if not interaction.guild:
        return False
    return interaction.user.guild_permissions.administrator

async def update_stock_message():
    """Update the stock tracking message"""
    if not STOCK_CHANNEL_ID:
        logger.error("❌ Stock channel ID not set!")
        return

    channel = bot.get_channel(STOCK_CHANNEL_ID)
    if not channel:
        logger.error(f"❌ Cannot find stock channel {STOCK_CHANNEL_ID}")
        return

    stock_content = "**📊 Current Shop Stock**\n"
    
    for category, category_items in item_categories.items():
        stock_content += f"\n**{category.upper()}**\n```\n"
        has_items = False
        
        for item_name in category_items:
            if item_name in items:
                total_quantity = sum(entry['quantity'] for entry in items[item_name])
                if total_quantity > 0:
                    has_items = True
                    owners = {}
                    for entry in items[item_name]:
                        owners[entry['person']] = owners.get(entry['person'], 0) + entry['quantity']
                    
                    stock_content += f"{item_name}: {total_quantity} total\n"
                    for owner, amount in owners.items():
                        stock_content += f"  └─ {owner}: {amount}\n"
        
        if not has_items:
            stock_content += "No stock available\n"
        stock_content += "```\n"
    
    stock_content += f"\nLast updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    global stock_message_id
    try:
        if stock_message_id:
            message = await channel.fetch_message(stock_message_id)
            await message.edit(content=stock_content)
        else:
            message = await channel.send(content=stock_content)
            stock_message_id = message.id
            with open("config.json", "w") as f:
                json.dump({"stock_message_id": stock_message_id}, f)
    except Exception as e:
        logger.error(f"❌ Failed to update stock message: {e}")

async def process_sale(item_name, quantity, price):
    """Process a sale from the shop"""
    logger.info("=====================================")
    logger.info(f"🛒 PROCESSING SALE: {quantity}x {item_name}")
    
    if item_name not in items:
        logger.error(f"❌ Sale failed: {item_name} not found in stock")
        return
        
    total_quantity = sum(entry['quantity'] for entry in items[item_name])
    logger.info(f"📊 Current stock: {total_quantity}")
    
    if total_quantity < quantity:
        logger.error(f"❌ Sale failed: Not enough stock (needed: {quantity}, have: {total_quantity})")
        return

    remaining = quantity
    sorted_entries = sorted(items[item_name], key=lambda x: x["date"])
    
    for entry in sorted_entries:
        if remaining <= 0:
            break
            
        sell_amount = min(entry["quantity"], remaining)
        remaining -= sell_amount
        
        logger.info(f"📦 Taking {sell_amount} from {entry['person']}'s stock of {entry['quantity']}")
        
        entry["quantity"] -= sell_amount
        
        if entry["person"] not in user_earnings:
            user_earnings[entry["person"]] = 0
        user_earnings[entry["person"]] += sell_amount * entry["price"]
        logger.info(f"💰 Added ${sell_amount * entry['price']} to {entry['person']}'s earnings")

    items[item_name] = [entry for entry in items[item_name] if entry["quantity"] > 0]
    
    sale_record = {
        "date": str(datetime.datetime.now()),
        "item": item_name,
        "quantity": quantity,
        "price": price
    }
    sale_history.append(sale_record)
    
    save_data()
    await update_stock_message()
    logger.info(f"✅ Sale completed: {quantity}x {item_name}")
    logger.info("=====================================")

async def item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=item, value=item)
        for item in item_list if current.lower() in item.lower()
    ][:25]

@bot.tree.command(name="help")
async def help_cmd(interaction: discord.Interaction):
    """Show available commands"""
    is_admin_user = await is_admin(interaction)
    help_text = """
**Available Commands:**
• `/stock` - View current stock levels
• `/earnings` - Check your earnings
• `/add` - Add items to your stock
• `/remove` - Remove stock from inventory
• `/setstock` - Set exact stock quantity"""

    if is_admin_user:
        help_text += """
• `/clearstock` - Clear stock entries (admin only)

**Admin Examples:**
`/setstock quantity:100 item:bud_ogkush user:@username`
`/clearstock item:bud_ogkush user:@username`
`/clearstock` - Clear all stock"""

    help_text += """

**Usage Examples:**
`/add quantity:10 item:bud_ogkush`
`/remove quantity:5 item:bud_ogkush`
`/setstock quantity:100 item:bud_ogkush`
"""
    await interaction.response.send_message(help_text, ephemeral=True)
@bot.tree.command(name="stock")
async def stock_cmd(interaction: discord.Interaction):
    """View current stock levels"""
    stock_message = "**Current Stock:**\n"
    for item_name, item_entries in items.items():
        total_quantity = sum(entry['quantity'] for entry in item_entries)
        if total_quantity > 0:
            stock_message += f"{item_name}: {total_quantity}\n"
    
    if stock_message == "**Current Stock:**\n":
        stock_message = "No items in stock!"
        
    await interaction.response.send_message(stock_message)

@bot.tree.command(name="earnings")
async def check_earnings(interaction: discord.Interaction):
    """Check your earnings"""
    user = str(interaction.user)
    if user in user_earnings and user_earnings[user] > 0:
        await interaction.response.send_message(
            f"Your current earnings: ${user_earnings[user]:,}",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "You have no earnings yet.",
            ephemeral=True
        )

@bot.tree.command(name="add")
@app_commands.describe(
    quantity="Amount to add",
    item="Item name",
    price="Price per item (optional)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def add_stock(
    interaction: discord.Interaction, 
    quantity: int, 
    item: str, 
    price: int = None
):
    """Add items to your stock"""
    normalized_item = item.lower().replace(' ', '_')
    
    if price is None:
        price = predefined_prices.get(normalized_item)
        if price is None:
            await interaction.response.send_message("❌ No predefined price found. Please specify a price.", ephemeral=True)
            return

    logger.info(f"Adding stock: {quantity}x {normalized_item} at ${price}")
    
    if normalized_item not in predefined_prices:
        logger.error(f"Invalid item attempted: {normalized_item}")
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items are: {', '.join(predefined_prices.keys())}",
            ephemeral=True
        )
        return

    if quantity <= 0:
        await interaction.response.send_message("❌ Quantity must be positive!", ephemeral=True)
        return
        
    date = str(datetime.date.today())
    if normalized_item not in items:
        items[normalized_item] = []
        
    items[normalized_item].append({
        "person": str(interaction.user),
        "quantity": quantity,
        "date": date,
        "price": price
    })
    
    save_data()
    await update_stock_message()
    logger.info(f"✅ Stock added successfully: {quantity}x {normalized_item} at ${price}")
    await interaction.response.send_message(
        f"✅ Added {quantity}x {normalized_item} at ${price} each",
        ephemeral=True
    )

@bot.tree.command(name="remove")
@app_commands.describe(
    quantity="Amount to remove",
    item="Item name"
)
@app_commands.autocomplete(item=item_autocomplete)
async def remove_stock(
    interaction: discord.Interaction, 
    quantity: int, 
    item: str
):
    """Remove stock from your inventory"""
    normalized_item = item.lower().replace(' ', '_')
    user = str(interaction.user)
    
    if normalized_item not in items:
        await interaction.response.send_message(
            "❌ No stock found for this item",
            ephemeral=True
        )
        return

    # Calculate total user quantity
    total_user_quantity = sum(
        entry['quantity'] for entry in items[normalized_item] 
        if entry['person'] == user
    )

    if total_user_quantity < quantity:
        await interaction.response.send_message(
            f"❌ You only have {total_user_quantity}x {normalized_item} in stock",
            ephemeral=True
        )
        return

    # Remove stock using FIFO
    remaining = quantity
    entries_to_keep = []
    
    for entry in sorted(items[normalized_item], key=lambda x: x['date']):
        if entry['person'] == user and remaining > 0:
            if entry['quantity'] <= remaining:
                remaining -= entry['quantity']
            else:
                entry['quantity'] -= remaining
                entries_to_keep.append(entry)
                remaining = 0
        else:
            entries_to_keep.append(entry)

    items[normalized_item] = entries_to_keep
    save_data()
    await update_stock_message()
    
    await interaction.response.send_message(
        f"✅ Removed {quantity}x {normalized_item} from your stock",
        ephemeral=True
    )
@bot.tree.command(name="setstock")
@app_commands.describe(
    quantity="New quantity to set",
    item="Item name",
    user="User to set stock for (admin only)",
    price="Price per item (optional)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def set_stock(
    interaction: discord.Interaction, 
    quantity: int, 
    item: str,
    user: discord.Member = None,
    price: int = None
):
    """Set stock quantity for an item (overwrites existing)"""
    normalized_item = item.lower().replace(' ', '_')
    target_user = str(user) if user else str(interaction.user)
    
    # Check permissions if setting for another user
    if user and not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can set stock for other users",
            ephemeral=True
        )
        return

    if price is None:
        price = predefined_prices.get(normalized_item)
        if price is None:
            await interaction.response.send_message("❌ No predefined price found. Please specify a price.", ephemeral=True)
            return

    if normalized_item not in predefined_prices:
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items are: {', '.join(predefined_prices.keys())}",
            ephemeral=True
        )
        return

    if quantity < 0:
        await interaction.response.send_message("❌ Quantity cannot be negative!", ephemeral=True)
        return
        
    # Replace all existing stock with new entry
    items[normalized_item] = [{
        "person": target_user,
        "quantity": quantity,
        "date": str(datetime.date.today()),
        "price": price
    }] if quantity > 0 else []
    
    save_data()
    await update_stock_message()
    
    await interaction.response.send_message(
        f"✅ Set {normalized_item} stock to {quantity} at ${price} each for user {target_user}",
        ephemeral=True
    )
@bot.tree.command(name="clearstock")
@app_commands.describe(
    item="Item to clear (leave empty to clear all)",
    user="User to clear stock for (admin only, leave empty to clear all users)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def clear_stock(
    interaction: discord.Interaction,
    item: str = None,
    user: discord.Member = None
):
    """Clear stock entries (admin only)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can clear stock",
            ephemeral=True
        )
        return
    
    target_user = str(user) if user else None
    
    if item:
        # Clear specific item
        normalized_item = item.lower().replace(' ', '_')
        if normalized_item in items:
            if target_user:
                # Clear for specific user
                items[normalized_item] = [
                    entry for entry in items[normalized_item] 
                    if entry['person'] != target_user
                ]
                msg = f"✅ Cleared {normalized_item} stock for user {target_user}"
            else:
                # Clear for all users
                items[normalized_item] = []
                msg = f"✅ Cleared all {normalized_item} stock"
        else:
            msg = "❌ Item not found"
    else:
        # Clear all items
        if target_user:
            # Clear all items for specific user
            for item_name in items:
                items[item_name] = [
                    entry for entry in items[item_name] 
                    if entry['person'] != target_user
                ]
            msg = f"✅ Cleared all stock for user {target_user}"
        else:
            # Clear everything
            items.clear()
            msg = "✅ Cleared all stock for all users"
    
    save_data()
    await update_stock_message()
    await interaction.response.send_message(msg, ephemeral=True)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Debug webhook info
    if message.webhook_id:
        logger.info(f"Webhook message received from: {message.author.name} ({message.webhook_id})")

    # Check if it's CaptainHook
    if "Captain Hook" in message.author.name or message.webhook_id:
        logger.info("=====================================")
        
        # Get message content from either content or embeds
        message_text = message.content
        if not message_text and message.embeds:
            embed = message.embeds[0]
            message_text = embed.description or ""
        
        logger.info(f"Message content:\n{message_text}")
        
        try:
            if "[PURCHASE INFO]" in message_text:
                # Clean and split lines
                lines = [
                    line.strip().replace('**', '').replace('> ', '') 
                    for line in message_text.split('\n') 
                    if line.strip()
                ]
                
                # Find required info
                name_line = next((line for line in lines if "Name:" in line), None)
                amount_line = next((line for line in lines if "Amount:" in line), None)
                profit_line = next((line for line in lines if "Profit:" in line), None)
                
                if all([name_line, amount_line, profit_line]):
                    # Extract values
                    item_name = name_line.split(': ')[1].strip()
                    quantity = int(amount_line.split(': ')[1].strip())
                    price = int(profit_line.split('$')[1].replace(',', '').strip())
                    
                    logger.info(f"📦 Parsed sale: {quantity}x {item_name} for ${price}")
                    await process_sale(item_name, quantity, price)
                else:
                    logger.error("❌ Missing required information in message")
                    logger.error(f"Name line: {name_line}")
                    logger.error(f"Amount line: {amount_line}")
                    logger.error(f"Profit line: {profit_line}")
                
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            logger.error(f"Full message:\n{message_text}")
            import traceback
            logger.error(f"Traceback:\n{traceback.format_exc()}")

    await bot.process_commands(message)
@bot.event
async def on_ready():
    """Called when the bot is ready"""
    logger.info(f"🤖 Logged in as {bot.user}")
    try:
        try:
            with open("config.json", "r") as f:
                config = json.load(f)
                global stock_message_id
                stock_message_id = config.get("stock_message_id")
        except FileNotFoundError:
            pass

        synced = await bot.tree.sync()
        logger.info(f"✅ Synced {len(synced)} commands")
        await update_stock_message()
    except Exception as e:
        logger.error(f"❌ Error during startup: {e}")

if __name__ == "__main__":
    load_data()
    logger.info("🚀 Starting bot...")
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
    finally:
        save_data()
        logger.info("✨ Cleanup complete")