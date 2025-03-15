############### IMPORTS ###############
import discord
from discord.ext import commands
from discord import app_commands
import datetime
import json
import os
from dotenv import load_dotenv
import logging
import asyncio
from typing import Dict, List, Optional, Union, Any
import traceback
import nacl  # Add this for voice support

class ItemView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=180)  # 3 minute timeout
        self.category = category
        
        # Add buttons for each item in category
        for item in shop_data.item_categories[category]:
            self.add_item(ItemButton(item))
        
class ItemButton(discord.ui.Button):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,  # This will show the friendly name
            style=discord.ButtonStyle.gray,
            custom_id=item_name
        )

    async def callback(self, interaction: discord.Interaction):
        # Show quantity modal
        modal = QuantityModal(self.internal_name)
        await interaction.response.send_modal(modal)

class RemoveItemButton(discord.ui.Button):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.red,
            custom_id=f"remove_{item_name}"
        )

    async def callback(self, interaction: discord.Interaction):
        modal = RemoveQuantityModal(self.internal_name)
        await interaction.response.send_modal(modal)

# Add this new class after RemoveItemButton:

class RemoveQuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Remove {display_name}")
        
        self.quantity = discord.ui.TextInput(
            label=f"Amount to remove",
            placeholder="Enter amount to remove",
            required=True
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.quantity.value)
            
            # Call existing remove_stock function
            await remove_stock(
                interaction=interaction,
                quantity=quantity,
                item=self.internal_name
            )
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in remove quantity modal: {e}")
            await interaction.response.send_message(
                "❌ Something went wrong. Please try again.",
                ephemeral=True
            )

class RemoveCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
    
    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.red, row=0)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.red, row=0)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.red, row=1)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bag')

    @discord.ui.button(label="🎮 Tebex", style=discord.ButtonStyle.red, row=1)
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'tebex')

    async def show_category_items(self, interaction: discord.Interaction, category: str):
        # Create a new view with buttons for each item
        view = discord.ui.View(timeout=180)
        
        # Only show items the user has in stock
        user = str(interaction.user)
        for item in shop_data.item_categories[category]:
            if shop_data.get_user_quantity(item, user) > 0:
                view.add_item(RemoveItemButton(item))
        
        if not view.children:
            await interaction.response.send_message(
                f"❌ You don't have any {category} items in stock!",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"🗑️ Remove {category.title()}",
                description="Select an item to remove from your stock:",
                color=COLORS['ERROR']
            ),
            view=view,
            ephemeral=True
        )

# Replace the existing QuantityModal class with:

class QuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Add {display_name}")
        
        self.quantity = discord.ui.TextInput(
            label=f"Amount of {display_name}",
            placeholder="Enter amount to add",
            required=True
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Convert input to integer
            quantity = int(self.quantity.value)
            
            # Process the addition
            await add_stock_internal(
                interaction=interaction,
                quantity=quantity,
                item=self.internal_name
            )
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in quantity modal: {e}")
            await interaction.response.send_message(
                "❌ Something went wrong. Please try again.",
                ephemeral=True
            )

# Setup logging with more detailed formatting
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("discord_bot")

############### CONSTANTS ###############
COLORS = {
    'SUCCESS': 0x57F287,  # Green
    'ERROR': 0xED4245,    # Red
    'INFO': 0x3498DB,     # Blue
    'WARNING': 0xFEE75C,  # Yellow
    'DEFAULT': 0x2F3136   # Dark
}

DATA_FILE = "data.json"
CONFIG_FILE = "config.json"
CATEGORY_EMOJIS = {
    'bud': '🥦',
    'joint': '🚬',
    'bag': '🛍️',
    'tebex': '💎'
}


LOW_STOCK_THRESHOLDS = {
    'bud': 30,
    'joint': 100,
    'bag': 100
}

# Load environment variables
load_dotenv('.env')
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    logger.critical("❌ BOT_TOKEN not found in environment variables!")
    raise ValueError("BOT_TOKEN environment variable is required")

STOCK_CHANNEL_ID = int(os.getenv('STOCK_CHANNEL_ID', 0))
if not STOCK_CHANNEL_ID:
    logger.warning("⚠️ STOCK_CHANNEL_ID not set or invalid. Stock updates will be disabled.")
    
############### CLASSES ###############

class CategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)  # 5 minute timeout
    
    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green, row=0)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🥦 Add Buds",
                description="Select an item to add to your stock:",
                color=COLORS['SUCCESS']
            ),
            view=ItemView('bud'),
            ephemeral=True
        )

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple, row=0)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🚬 Add Joints",
                description="Select an item to add to your stock:",
                color=COLORS['INFO']
            ),
            view=ItemView('joint'),
            ephemeral=True
        )

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray, row=1)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🛍️ Add Bags",
                description="Select an item to add to your stock:",
                color=COLORS['DEFAULT']
            ),
            view=ItemView('bag'),
            ephemeral=True
        )

    @discord.ui.button(label="🎮 Tebex", style=discord.ButtonStyle.primary, row=1)
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎮 Add Tebex Items",
                description="Select an item to add to your stock:",
                color=COLORS['INFO']
            ),
            view=ItemView('tebex'),
            ephemeral=True
        )
    
# Predefined data structures
class ShopData:
    def __init__(self):
        self.items: Dict[str, List[Dict[str, Any]]] = {}
        self.user_earnings: Dict[str, int] = {}
        self.sale_history: List[Dict[str, Any]] = []
        self.stock_message_id: Optional[int] = None
        
        self.display_names = {
            'bud_sojokush': 'Bizarre Bud',
            'bud_khalifakush': 'Strange Bud',
            'bud_pineappleexpress': 'Smelly Bud',
            'bud_sourdiesel': 'Sour Diesel Bud',
            'bud_whitewidow': 'Whacky Bud',
            'bud_ogkush': 'Old Bud',
            'joint_sojokush': 'Bizarre Joint',
            'joint_khalifakush': 'Strange Joint',
            'joint_pineappleexpress': 'Smelly Joint',
            'joint_sourdiesel': 'Sour Diesel Joint',
            'joint_whitewidow': 'Whacky Joint',
            'joint_ogkush': 'Old Joint',
            'bagof_sojokush': 'Bizarre Bag',
            'bagof_khalifakush': 'Strange Bag',
            'bagof_pineappleexpress': 'Smelly Bag',
            'bagof_sourdiesel': 'Sour Diesel Bag',
            'bagof_whitewidow': 'Whacky Bag',
            'bagof_ogkush': 'Old Bag'
        }
        self.display_names.update({
            'tebex_vinplate': 'Stolen Plate',
            'tebex_talentreset': 'Talent Reset',
            'tebex_deep_pockets': 'Deep Pockets',
            'licenseplate': 'Custom Plate',
            'tebex_carwax': 'Car Wax',
            'tebex_xpbooster': 'XP Booster'
        })
    
        # Predefined prices and categories
        self.predefined_prices = {
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
        self.predefined_prices.update({
            'tebex_vinplate': 350000,
            'tebex_talentreset': 550000,
            'tebex_deep_pockets': 1075000,
            'licenseplate': 535000,
            'tebex_carwax': 595000,
            'tebex_xpbooster': 1450000
        })
        
        self.item_categories = {
            'bud': ['bud_ogkush', 'bud_whitewidow', 'bud_sourdiesel', 'bud_pineappleexpress', 
                   'bud_khalifakush', 'bud_sojokush'],
            'joint': ['joint_ogkush', 'joint_whitewidow', 'joint_sourdiesel', 'joint_pineappleexpress', 
                     'joint_khalifakush', 'joint_sojokush'],
            'bag': ['bagof_ogkush', 'bagof_whitewidow', 'bagof_sourdiesel', 'bagof_pineappleexpress', 
                   'bagof_khalifakush', 'bagof_sojokush']
        }
        self.item_categories['tebex'] = [
            'tebex_vinplate',           # Added commas
            'tebex_talentreset',
            'tebex_deep_pockets',
            'licenseplate',
            'tebex_carwax',
            'tebex_xpbooster'
        ]
        # Create a list of all valid items
        self.item_list = list(self.predefined_prices.keys())
    
    def save_data(self) -> None:
        """Save current state to data.json with pretty formatting"""
        try:
            with open(DATA_FILE, "w") as f:
                json.dump({
                    "items": self.items,
                    "earnings": self.user_earnings,
                    "sale_history": self.sale_history
                }, f, indent=2)
            logger.info("💾 Data saved to data.json")
        except Exception as e:
            logger.error(f"❌ Error saving data: {e}")
    
    def load_data(self) -> None:
        """Load state from data.json"""
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                self.items.update(data.get("items", {}))
                self.user_earnings.update(data.get("earnings", {}))
                if "sale_history" in data:
                    self.sale_history.extend(data["sale_history"])
            logger.info("📂 Data loaded successfully")
        except FileNotFoundError:
            logger.info("📝 No existing data found. Starting fresh.")
        except json.JSONDecodeError:
            logger.error("❌ Corrupted data file. Starting fresh.")
        except Exception as e:
            logger.error(f"❌ Error loading data: {e}")
    
    def load_config(self) -> None:
        """Load configuration from config.json"""
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                self.stock_message_id = config.get("stock_message_id")
                logger.info(f"📂 Config loaded: stock_message_id={self.stock_message_id}")
        except FileNotFoundError:
            logger.info("📝 No existing config found. Starting fresh.")
        except Exception as e:
            logger.error(f"❌ Error loading config: {e}")
    
    def save_config(self) -> None:
        """Save configuration to config.json"""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"stock_message_id": self.stock_message_id}, f, indent=2)
            logger.info("💾 Config saved")
        except Exception as e:
            logger.error(f"❌ Error saving config: {e}")
    
    def get_total_quantity(self, item_name: str) -> int:
        """Get total quantity of an item in stock"""
        if item_name not in self.items:
            return 0
        return sum(entry['quantity'] for entry in self.items[item_name])
    
    def get_user_quantity(self, item_name: str, user: str) -> int:
        """Get quantity of an item owned by a specific user"""
        if item_name not in self.items:
            return 0
        return sum(entry['quantity'] for entry in self.items[item_name] if entry['person'] == user)
    
    def is_valid_item(self, item_name: str) -> bool:
        """Check if an item is valid"""
        return item_name in self.predefined_prices
    
    def add_to_history(self, action: str, item: str, quantity: int, price: int, user: str) -> None:
        """Add an event to the sale history with more details"""
        self.sale_history.append({
            "date": str(datetime.datetime.now()),
            "action": action,
            "item": item,
            "quantity": quantity,
            "price": price,
            "user": user
        })
    
    def get_category_for_item(self, item_name: str) -> Optional[str]:
        """Get category for an item"""
        for category, items in self.item_categories.items():
            if item_name in items:
                return category
        return None

    def is_low_stock(self, item_name: str, quantity: int) -> bool:
        """Check if item is low on stock"""
        category = self.get_category_for_item(item_name)
        if not category:
            return False
        return quantity <= LOW_STOCK_THRESHOLDS.get(category, 0)


# Add this class near your other View classes
class StockView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)  # 3 minute timeout

    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'bag')

    @discord.ui.button(label="📊 All Stock", style=discord.ButtonStyle.primary)
    async def all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'all')

    
async def show_category(self, interaction: discord.Interaction, category: str):
    embed = discord.Embed(
        title="📊 Current Stock",
        color=COLORS['INFO'],
        timestamp=datetime.datetime.now()
    )
    
    total_value = 0
    categories = shop_data.item_categories.items()
    if category != 'all':
        categories = [(category, shop_data.item_categories[category])]

    for cat, category_items in categories:
        content = []
        category_value = 0
        
        for item_name in category_items:
            qty = shop_data.get_total_quantity(item_name)
            if qty > 0:
                price = shop_data.predefined_prices.get(item_name, 0)
                value = qty * price
                category_value += value
                display_name = shop_data.display_names.get(item_name, item_name)
                
                # Add warning symbols if needed
                status = ""
                if qty <= LOW_STOCK_THRESHOLDS.get(cat, 0): status = "⚠️"
                elif qty >= LOW_STOCK_THRESHOLDS.get(cat, 0) * 3: status = "📈"
                
                content.append(f"`{display_name[:15]:<15} {qty:>4,} @ ${price:>6,} = ${value:>8,} {status}`")
        
        if content:
            total_value += category_value
            embed.add_field(
                name=f"{CATEGORY_EMOJIS.get(cat, '📦')} {cat.upper()} (${category_value:,})",
                value="\n".join(content),
                inline=False
            )
    
    if total_value > 0:
        embed.description = f"💰 Total Value: ${total_value:,}"
    else:
        embed.description = "No items in stock!"
    
    embed.set_footer(text="Use /add or /quickadd to add items")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Bot initialization
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
shop_data = ShopData()

# Add after bot initialization
@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state changes"""
    pass  # We don't need voice functionality, but this prevents warnings

############### HELPER FUNCTIONS ###############
async def is_admin(interaction: discord.Interaction) -> bool:
    """Check if user has admin permissions"""
    if not interaction.guild:
        return False
    return interaction.user.guild_permissions.administrator

# Find the update_stock_message() function (around line 180-200) and update this section:

async def update_stock_message() -> None:
    """Update the stock tracking message"""
    if not STOCK_CHANNEL_ID:
        logger.error("❌ Stock channel ID not set!")
        return

    channel = bot.get_channel(STOCK_CHANNEL_ID)
    if not channel:
        logger.error(f"❌ Cannot find stock channel {STOCK_CHANNEL_ID}")
        return

    messages = []  # We'll split content into multiple messages if needed
    current_message = "**📊 Current Shop Stock**\n\n"
    current_message += f"Last updated: <t:{int(datetime.datetime.now().timestamp())}:R>\n\n"
    total_value = 0

    for category, category_items in shop_data.item_categories.items():
        category_content = f"{CATEGORY_EMOJIS.get(category, '')} **{category.upper()}**\n```ml\n"
        category_content += f"{'Item':<20} {'Stock':>8} {'Price':>8} {'Value':>10}\n"
        category_content += "─" * 50 + "\n"
        
        has_items = False
        category_value = 0
        
        for item_name in category_items:
            if item_name in shop_data.items:
                total_quantity = shop_data.get_total_quantity(item_name)
                if total_quantity > 0:
                    has_items = True
                    price = shop_data.predefined_prices.get(item_name, 'N/A')
                    item_value = total_quantity * price if price != 'N/A' else 0
                    category_value += item_value
                    display_name = shop_data.display_names.get(item_name, item_name)
                    
                    warning = ""
                    if total_quantity <= LOW_STOCK_THRESHOLDS.get(category, 0):
                        warning = "⚠️"
                    elif total_quantity >= LOW_STOCK_THRESHOLDS.get(category, 0) * 3:
                        warning = "📈"
                    
                    item_line = f"{display_name[:20]:<20} {total_quantity:>8,} {price:>8,} {item_value:>10,} {warning}\n"
                    
                    # Check if adding this line would exceed Discord's limit
                    if len(category_content) + len(item_line) > 1000:
                        category_content += "```\n"
                        if len(current_message + category_content) > 1900:
                            messages.append(current_message)
                            current_message = category_content
                        else:
                            current_message += category_content
                        category_content = f"```ml\n{item_line}"
                    else:
                        category_content += item_line

        if not has_items:
            category_content += "No stock available\n"
        
        category_content += "```\n"
        total_value += category_value
        
        # Check if adding category would exceed limit
        if len(current_message + category_content) > 1900:
            messages.append(current_message)
            current_message = category_content
        else:
            current_message += category_content

    # Add final summary
    summary = f"\n💰 **Total Stock Value:** ${total_value:,}"
    current_message += summary
    messages.append(current_message)

    try:
        # Delete old message(s)
        if shop_data.stock_message_id:
            try:
                old_message = await channel.fetch_message(shop_data.stock_message_id)
                await old_message.delete()
            except discord.NotFound:
                pass

        # Send new message(s)
        last_message = None
        for i, content in enumerate(messages):
            last_message = await channel.send(content)
            if i == len(messages) - 1:  # Save ID of last message
                shop_data.stock_message_id = last_message.id
                shop_data.save_config()
        
        logger.info("📝 Stock message updated")
    except Exception as e:
        logger.error(f"❌ Failed to update stock message: {e}")
        logger.error(traceback.format_exc())
    
async def process_sale(item_name: str, quantity: int, price: int) -> bool:
    """Process a sale from the shop, returns success status"""
    display_name = shop_data.display_names.get(item_name, item_name)
    logger.info(f"🛒 PROCESSING SALE: {quantity}x {display_name}")
    
    if item_name not in shop_data.items:
        logger.error(f"❌ Sale failed: {display_name} not found in stock")
        return False
        
    total_quantity = shop_data.get_total_quantity(item_name)
    
    if total_quantity < quantity:
        logger.error(f"❌ Sale failed: Not enough stock (needed: {quantity}, have: {total_quantity})")
        return False

    remaining = quantity
    # Sort by date (FIFO)
    sorted_entries = sorted(shop_data.items[item_name], key=lambda x: x["date"])
    
    for entry in sorted_entries:
        if remaining <= 0:
            break
            
        sell_amount = min(entry["quantity"], remaining)
        remaining -= sell_amount
        
        logger.info(f"📦 Taking {sell_amount} from {entry['person']}'s stock of {entry['quantity']}")
        
        entry["quantity"] -= sell_amount
        
        if entry["person"] not in shop_data.user_earnings:
            shop_data.user_earnings[entry["person"]] = 0
            
        sale_value = sell_amount * entry["price"]
        shop_data.user_earnings[entry["person"]] += sale_value
        logger.info(f"💰 Added ${sale_value} to {entry['person']}'s earnings")

    # Remove empty entries
    shop_data.items[item_name] = [entry for entry in shop_data.items[item_name] if entry["quantity"] > 0]
    
    # Record the sale
    shop_data.add_to_history("sale", item_name, quantity, price, "customer")
    
    shop_data.save_data()
    await update_stock_message()
    logger.info(f"✅ Sale completed: {quantity}x {display_name}")
    return True

async def item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for item names"""
    return [
        app_commands.Choice(name=shop_data.display_names.get(item, item), value=item)
        for item in shop_data.item_list if current.lower() in shop_data.display_names.get(item, item).lower()
    ][:25]

async def add_stock_internal(interaction: discord.Interaction, quantity: int, item: str, price: Optional[int] = None) -> bool:
    """Internal function to add stock, used by both /add command and quick add"""
    normalized_item = item.lower().replace(' ', '_')
    
    # Validate item
    if not shop_data.is_valid_item(normalized_item):
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items are: {', '.join(shop_data.predefined_prices.keys())}",
            ephemeral=True
        )
        return False
    
    # Get price
    if price is None:
        price = shop_data.predefined_prices.get(normalized_item)
    
    # Validate quantity
    if quantity <= 0:
        await interaction.response.send_message("❌ Quantity must be positive!", ephemeral=True)
        return False
    
    # Add to stock
    date = str(datetime.date.today())
    if normalized_item not in shop_data.items:
        shop_data.items[normalized_item] = []
        
    shop_data.items[normalized_item].append({
        "person": str(interaction.user),
        "quantity": quantity,
        "date": date,
        "price": price
    })
    
    # Record the addition
    shop_data.add_to_history("add", normalized_item, quantity, price, str(interaction.user))
    
    shop_data.save_data()
    await update_stock_message()
    
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    embed = discord.Embed(
        title="✅ Stock Added",
        description=f"Added {quantity}x {display_name} at ${price} each",
        color=discord.Color.green()
    )
    
    total_quantity = shop_data.get_total_quantity(normalized_item)
    category = next((cat for cat, items in shop_data.item_categories.items() if normalized_item in items), None)
    if category and total_quantity > LOW_STOCK_THRESHOLDS.get(category, 0) * 3:
        embed.add_field(
            name="⚠️ High Stock Warning",
            value=f"You now have {total_quantity} in stock, which is quite high!",
            inline=False
            
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    return True

############### COMMANDS ###############
# Replace the existing help command with:

@bot.tree.command(name="help")
async def help_cmd(interaction: discord.Interaction):
    """Show available commands"""
    is_admin_user = await is_admin(interaction)
    
    embed = discord.Embed(
        title="🏪 Shop Commands",
        color=COLORS['INFO']
    )
    
    # Basic commands in compact format
    commands = [
        "`/quickadd` Quick add items",
        "`/add` Add to stock",
        "`/remove` Remove from stock",
        "`/stock` View inventory",
        "`/earnings` Check profits"
    ]
    
    if is_admin_user:
        admin_commands = [
            "`/setstock` Set quantities",
            "`/clearstock` Clear stock",
            "`/sellmanual` Process sales",
            "`/userinfo` View user data",
            "`/history` View transactions"
        ]
        commands.extend(admin_commands)
    
    embed.add_field(
        name="Available Commands",
        value="\n".join(commands),
        inline=False
    )
    
    embed.add_field(
        name="Quick Tips",
        value="• Tab complete for item names\n"
              "• Use `/quickadd` for fast stocking\n"
              "• Check `/stock` for warnings",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="stock")
async def stock_cmd(interaction: discord.Interaction):
    """View current stock levels"""
    await interaction.response.send_message(
        "**📊 Stock Viewer**\nSelect a category to view:",
        view=StockView(),
        ephemeral=True
    )
@bot.tree.command(name="earnings")
async def check_earnings(interaction: discord.Interaction):
    """Check your earnings"""
    user = str(interaction.user)
    earnings = shop_data.user_earnings.get(user, 0)
    
    embed = discord.Embed(
        title="💰 Your Earnings",
        color=discord.Color.gold()
    )
    
    if earnings > 0:
        embed.description = f"Your current earnings: **${earnings:,}**"
    else:
        embed.description = "You have no earnings yet."
        embed.add_field(
            name="💡 How to earn",
            value="Add items to your stock using `/add` and they'll be sold automatically",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Replace the existing add_stock command with:

@bot.tree.command(name="add")
@app_commands.describe(
    quantity="Amount to add",
    item="Item name",
    user="User to add stock for (admin only)",
    price="Price per item (optional, uses default if not specified)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def add_stock(
    interaction: discord.Interaction, 
    quantity: int, 
    item: str,
    user: Optional[discord.Member] = None,
    price: Optional[int] = None
):
    """Add items to stock"""
    # Check permissions if adding for another user
    if user and not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can add stock for other users",
            ephemeral=True
        )
        return

    # Use target user if specified, otherwise use command invoker
    target_user = str(user) if user else str(interaction.user)
    
    normalized_item = item.lower().replace(' ', '_')
    
    # Validate item
    if not shop_data.is_valid_item(normalized_item):
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items are: {', '.join(shop_data.predefined_prices.keys())}",
            ephemeral=True
        )
        return False
    
    # Get price
    if price is None:
        price = shop_data.predefined_prices.get(normalized_item)
    
    # Validate quantity
    if quantity <= 0:
        await interaction.response.send_message("❌ Quantity must be positive!", ephemeral=True)
        return False
    
    # Add to stock
    date = str(datetime.date.today())
    if normalized_item not in shop_data.items:
        shop_data.items[normalized_item] = []
        
    shop_data.items[normalized_item].append({
        "person": target_user,
        "quantity": quantity,
        "date": date,
        "price": price
    })
    
    # Record the addition
    shop_data.add_to_history("add", normalized_item, quantity, price, target_user)
    
    shop_data.save_data()
    await update_stock_message()
    
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    embed = discord.Embed(
        title="✅ Stock Added",
        color=COLORS['SUCCESS']
    )
    embed.add_field(
        name="Details",
        value=f"```ml\nItem: {display_name}\nQuantity: {quantity:,}\nPrice: ${price:,}\nUser: {target_user}\nTotal Value: ${quantity * price:,}```",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    return True

@bot.tree.command(name="remove")
@app_commands.describe(
    quantity="Amount to remove",
    item="Item name"
)
@app_commands.autocomplete(item=item_autocomplete)  # Add autocomplete
async def remove_stock(interaction: discord.Interaction, quantity: int, item: str):
    """Remove items from your stock"""
    # Get internal name from display name
    normalized_item = item.lower().replace(' ', '_')
    user = str(interaction.user)
    
    # Add display name for messages
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    
    # Check if item exists in stock
    if normalized_item not in shop_data.items:
        await interaction.response.send_message(
            f"❌ No stock found for {display_name}",
            ephemeral=True
        )
        return

    # Check if user has enough
    total_user_quantity = shop_data.get_user_quantity(normalized_item, user)
    if total_user_quantity < quantity:
        await interaction.response.send_message(
            f"❌ You only have {total_user_quantity}x {display_name} in stock",
            ephemeral=True
        )
        return

    # Remove the stock
    removed = 0
    for entry in shop_data.items[normalized_item]:
        if entry['person'] == user:
            if entry['quantity'] <= quantity - removed:
                removed += entry['quantity']
                entry['quantity'] = 0
            else:
                entry['quantity'] -= (quantity - removed)
                removed = quantity
                break

    # Clean up empty entries
    shop_data.items[normalized_item] = [
        entry for entry in shop_data.items[normalized_item] 
        if entry['quantity'] > 0
    ]

    # Save changes
    shop_data.add_to_history("remove", normalized_item, quantity, 0, user)
    shop_data.save_data()
    await update_stock_message()
    
    embed = discord.Embed(
        title="✅ Stock Removed",
        color=COLORS['SUCCESS']
    )
    embed.add_field(
        name="Details",
        value=f"```ml\nItem: {display_name}\nQuantity: {quantity:,}\nRemaining: {shop_data.get_total_quantity(normalized_item):,}```",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

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
    user: Optional[discord.Member] = None,
    price: Optional[int] = None
):
    """Set stock quantity for an item (overwrites existing)"""
    # Validate user
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server",
            ephemeral=True
        )
        return

    normalized_item = item.lower().replace(' ', '_')
    target_user = str(user) if user else str(interaction.user)
    
    # Check permissions if setting for another user
    if user and not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can set stock for other users",
            ephemeral=True
        )
        return

    # Validate item
    if not shop_data.is_valid_item(normalized_item):
        valid_items = ", ".join(shop_data.display_names.values())
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items:\n{valid_items}",
            ephemeral=True
        )
        return

    # Validate quantity
    if quantity < 0:
        await interaction.response.send_message(
            "❌ Quantity cannot be negative",
            ephemeral=True
        )
        return

    try:
        # Get price
        if price is None:
            price = shop_data.predefined_prices.get(normalized_item)
            if price is None:
                await interaction.response.send_message(
                    "❌ No default price found for this item",
                    ephemeral=True
                )
                return

        # Get previous quantity for change tracking
        previous_quantity = shop_data.get_user_quantity(normalized_item, target_user)

        # Remove existing entries for this user and item
        if normalized_item in shop_data.items:
            shop_data.items[normalized_item] = [
                entry for entry in shop_data.items[normalized_item] 
                if entry['person'] != target_user
            ]
        else:
            shop_data.items[normalized_item] = []

        # Add new entry if quantity > 0
        if quantity > 0:
            shop_data.items[normalized_item].append({
                "person": target_user,
                "quantity": quantity,
                "date": str(datetime.date.today()),
                "price": price
            })

        # Record the change
        shop_data.add_to_history("set", normalized_item, quantity, price, target_user)
        shop_data.save_data()
        await update_stock_message()

        # Create response embed
        display_name = shop_data.display_names.get(normalized_item, normalized_item)
        embed = discord.Embed(
            title="✅ Stock Updated",
            description=f"Set {display_name} stock to {quantity} at ${price:,} each",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Details",
            value=f"**User:** {target_user}\n**Change:** {previous_quantity} → {quantity}",
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in set_stock: {str(e)}")
        await interaction.response.send_message(
            "❌ An error occurred while updating stock",
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
    item: Optional[str] = None,
    user: Optional[discord.Member] = None
):
    """Clear stock entries (admin only)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can clear stock",
            ephemeral=True
        )
        return
    
    target_user = str(user) if user else None
    
    embed = discord.Embed(
        title="🗑️ Stock Cleared",
        color=discord.Color.red()
    )
    
    if item:
        # Clear specific item
        normalized_item = item.lower().replace(' ', '_')
        display_name = shop_data.display_names.get(normalized_item, normalized_item)
        
        if normalized_item in shop_data.items:
            if target_user:
                # Clear for specific user
                shop_data.items[normalized_item] = [
                    entry for entry in shop_data.items[normalized_item] 
                    if entry['person'] != target_user
                ]
                embed.description = f"Cleared {display_name} stock for user {target_user}"
            else:
                # Clear for all users
                shop_data.items[normalized_item] = []
                embed.description = f"Cleared all {display_name} stock"
        else:
            embed.description = f"❌ Item {display_name} not found"
            embed.color = discord.Color.orange()
    else:
        # Clear all items
        if target_user:
            # Clear all items for specific user
            for item_name in shop_data.items:
                shop_data.items[item_name] = [
                    entry for entry in shop_data.items[item_name] 
                    if entry['person'] != target_user
                ]
            embed.description = f"Cleared all stock for user {target_user}"
        else:
            # Clear everything
            shop_data.items.clear()
            embed.description = "⚠️ Cleared all stock for all users"
    
    # Record the clear action
    shop_data.add_to_history(
        "clear", 
        item if item else "all", 
        0, 
        0, 
        target_user if target_user else "all"
    )
    
    shop_data.save_data()
    await update_stock_message()
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Add new command
@bot.tree.command(name="quickadd")
async def quick_add(interaction: discord.Interaction):
    """Quick add multiple items using buttons"""
    await interaction.response.send_message(
        "**📦 Quick Stock Addition**\nSelect a category:",
        view=CategoryView(),
        ephemeral=True
    )

@bot.tree.command(name="history")
@app_commands.describe(
    limit="Number of entries to show (default 10)"
)
async def view_history(
    interaction: discord.Interaction,
    limit: Optional[int] = 10
):
    """View recent sale history (admin only)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can view history",
            ephemeral=True
        )
        return
    
    embed = discord.Embed(
        title="📜 Transaction History",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )
    
    if not shop_data.sale_history:
        embed.description = "No history available"
    else:
        recent_history = shop_data.sale_history[-limit:]
        
        for i, entry in enumerate(reversed(recent_history), 1):
            action = entry.get("action", "sale")
            date = entry.get("date", "Unknown")
            item = entry.get("item", "Unknown")
            display_name = shop_data.display_names.get(item, item)  # Get display name
            quantity = entry.get("quantity", 0)
            price = entry.get("price", 0)
            user = entry.get("user", "Unknown")
            
            entry_text = f"**Item:** {display_name}\n"  # Use display name
            entry_text += f"**Quantity:** {quantity}\n"
            entry_text += f"**Price:** ${price}\n"
            
            if action != "sale":
                entry_text += f"**User:** {user}\n"
                
            entry_text += f"**Date:** {date[:16]}"
            
            embed.add_field(
                name=f"{i}. {action.capitalize()}", 
                value=entry_text, 
                inline=False
            )
    
    embed.set_footer(text=f"Showing {min(limit, len(shop_data.sale_history))} of {len(shop_data.sale_history)} entries")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    
@bot.tree.command(name="sellmanual")
@app_commands.describe(
    quantity="Amount sold",
    item="Item name",
    price="Override price (optional, uses default if not set)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def manual_sell(
    interaction: discord.Interaction,
    quantity: int,
    item: str,
    price: Optional[int] = None
):
    """Manually process a sale (admin only)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can manually process sales",
            ephemeral=True
        )
        return

    normalized_item = item.lower().replace(' ', '_')
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    
    # Get current stock
    current_stock = shop_data.get_total_quantity(normalized_item)
    
    # Use default price if none provided
    if price is None:
        price = shop_data.predefined_prices.get(normalized_item)
        if price is None:
            await interaction.response.send_message(
                "❌ No price specified and no default price found",
                ephemeral=True
            )
            return

    # Process the sale
    success = await process_sale(normalized_item, quantity, price)
    
    if success:
        embed = discord.Embed(
            title="✅ Manual Sale Processed",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Sale Details",
            value=f"Item: {display_name}\n"
                  f"Quantity: {quantity}\n"
                  f"Price: ${price} each\n"
                  f"Total Value: ${quantity * price:,}"
        )
        embed.add_field(
            name="Stock Changes",
            value=f"Previous Stock: {current_stock}\n"
                  f"New Stock: {current_stock - quantity}",
            inline=False
        )
    else:
        embed = discord.Embed(
            title="❌ Sale Failed",
            description="Not enough stock or invalid item",
            color=discord.Color.red()
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)
 
 # Add this new command before the EVENT HANDLERS section:

@bot.tree.command(name="quickremove")
async def quick_remove(interaction: discord.Interaction):
    """Quick remove items using buttons"""
    await interaction.response.send_message(
        "**🗑️ Quick Stock Removal**\nSelect a category:",
        view=RemoveCategoryView(),
        ephemeral=True
    )
    
@bot.tree.command(name="userinfo")
@app_commands.describe(
    user="User to check (leave empty for self)"
)
async def user_info(
    interaction: discord.Interaction,
    user: Optional[discord.Member] = None
):
    """View user's stock and earnings (admin command)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can view user information",
            ephemeral=True
        )
        return

    target_user = str(user) if user else str(interaction.user)
    
    embed = discord.Embed(
        title=f"👤 User Information: {target_user}",
        color=COLORS['INFO'],
        timestamp=datetime.datetime.now()
    )

    # Get earnings
    earnings = shop_data.user_earnings.get(target_user, 0)
    embed.add_field(
        name="💰 Total Earnings",
        value=f"${earnings:,}",
        inline=False
    )

    # Get stock information
    stock_text = ""
    total_value = 0
    
    for category, items in shop_data.item_categories.items():
        category_items = []
        category_value = 0
        
        for item in items:
            quantity = sum(
                entry['quantity'] 
                for entry in shop_data.items.get(item, [])
                if entry['person'] == target_user
            )
            if quantity > 0:
                price = shop_data.predefined_prices.get(item, 0)
                value = quantity * price
                category_value += value
                total_value += value
                display_name = shop_data.display_names.get(item, item)
                category_items.append(f"{display_name}: {quantity:,} (${value:,})")
        
        if category_items:
            stock_text += f"\n{CATEGORY_EMOJIS.get(category, '📦')} **{category.upper()}** - ${category_value:,}\n"
            stock_text += "\n".join(f"└ {item}" for item in category_items)
            stock_text += "\n"

    if stock_text:
        embed.add_field(
            name="📊 Current Stock",
            value=stock_text,
            inline=False
        )
        embed.add_field(
            name="💎 Total Stock Value",
            value=f"${total_value:,}",
            inline=False
        )
    else:
        embed.add_field(
            name="📊 Current Stock",
            value="No items in stock",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)   
    
############### EVENT HANDLERS ###############
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Check if it's a webhook message
    if message.webhook_id and ("Captain Hook" in message.author.name or True):
        logger.info(f"📨 Webhook message from: {message.author.name}")
        
        # Get message content
        message_text = message.content
        if not message_text and message.embeds:
            embed = message.embeds[0]
            message_text = embed.description or ""
        
        try:
            if "[PURCHASE INFO]" in message_text:
                # Clean and split lines
                lines = [
                    line.strip().replace('**', '').replace('> ', '') 
                    for line in message_text.split('\n') 
                    if line.strip()
                ]
                
                # Parse required info
                item_name = None
                quantity = None
                price = None
                missing = []
                
                for line in lines:
                    if "Name:" in line:
                        item_name = line.split(':', 1)[1].strip()
                    elif "Amount:" in line:
                        try:
                            quantity = int(line.split(':', 1)[1].strip())
                        except ValueError:
                            logger.error(f"❌ Invalid quantity format in line: {line}")
                            missing.append("Valid quantity")
                    elif "Profit:" in line and '$' in line:
                        try:
                            price_text = line.split('$', 1)[1].replace(',', '').strip()
                            price = int(float(price_text))
                        except ValueError:
                            logger.error(f"❌ Invalid price format in line: {line}")
                            missing.append("Valid price")
                
                if not item_name: missing.append("Item name")
                if not quantity: missing.append("Quantity")
                if not price: missing.append("Price")
                
                if missing:
                    logger.error(f"❌ Missing information: {', '.join(missing)}")
                    await message.add_reaction("❓")
                    return
                
                # Get display name for logs
                display_name = shop_data.display_names.get(item_name, item_name)
                logger.info(f"📦 Processing webhook sale: {quantity}x {display_name} for ${price:,}")
                
                success = await process_sale(item_name, quantity, price)
                if success:
                    await message.add_reaction("✅")
                    logger.info(f"✅ Webhook sale completed: {quantity}x {display_name}")
                else:
                    await message.add_reaction("❌")
                    logger.error(f"❌ Webhook sale failed: {quantity}x {display_name}")
                    
        except Exception as e:
            logger.error(f"❌ Error processing webhook: {e}")
            logger.error(traceback.format_exc())
            await message.add_reaction("⚠️")

    await bot.process_commands(message)
    
@bot.event
async def on_ready():
    """Called when the bot is ready"""
    logger.info(f"🤖 Logged in as {bot.user}")
    try:
        # Load saved data
        shop_data.load_data()
        shop_data.load_config()
        
        # Sync commands
        synced = await bot.tree.sync()
        logger.info(f"✅ Synced {len(synced)} commands")
        
        # Update stock display
        await update_stock_message()
        logger.info("✅ Initial stock message updated")
        
    except Exception as e:
        logger.error(f"❌ Error during startup: {e}")
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    try:
        logger.info("🚀 Starting bot...")
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
    finally:
        shop_data.save_data()
        logger.info("✨ Shutdown complete")