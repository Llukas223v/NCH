# START OF FILE sonnet.py

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
from typing import Dict, List, Optional, Union, Any, Literal
import traceback
import nacl # Keep if future voice planned
import aiohttp # Keep if future direct http planned
import re
from pymongo import MongoClient
import pymongo
import schedule
from threading import Thread
import time

# Define intents first
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("discord_bot")

####################################################################################################################################

############### CONSTANTS & CONFIG ###############
COLORS = {
    'SUCCESS': 0x57F287,
    'ERROR': 0xED4245,
    'INFO': 0x3498DB,
    'WARNING': 0xFEE75C,
    'DEFAULT': 0x2F3136
}

CONFIG_FILE = "config.json"

# Load environment variables
load_dotenv('.env')
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    logger.critical("❌ BOT_TOKEN not found in environment variables!")
    raise ValueError("BOT_TOKEN environment variable is required")

STOCK_CHANNEL_ID = int(os.getenv('STOCK_CHANNEL_ID', 0))
if not STOCK_CHANNEL_ID:
    logger.warning("⚠️ STOCK_CHANNEL_ID not set or invalid. Stock updates will be disabled.")

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    logger.critical("❌ MONGO_URI not found in environment variables! Bot requires MongoDB.")
    raise ValueError("MONGO_URI environment variable is required")

APP_ENV = os.getenv("APP_ENV", "production")
DB_NAME = "NCHBot" if APP_ENV == "production" else "NCHBot_dev"

############### UI CLASSES ###############

class ItemView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=180)
        self.category = category
        items_in_category = shop_data.item_categories.get(category, [])
        for item in items_in_category:
             if shop_data.is_valid_item(item): # Ensure item is still valid
                self.add_item(ItemButton(item))

class ItemButton(discord.ui.Button):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.gray,
            custom_id=f"add_{item_name}" # Make custom_id more specific
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            # Ensure the parent view is passed correctly
            if not isinstance(self.view, ItemView):
                 logger.error(f"ItemButton callback: Parent view is not ItemView for item {self.internal_name}")
                 await interaction.response.send_message("❌ Internal UI error.", ephemeral=True)
                 return
            modal = QuantityModal(self.internal_name, self.view)
            await interaction.response.send_modal(modal)
        except Exception as e:
             logger.error(f"Error in ItemButton callback for {self.internal_name}: {e}\n{traceback.format_exc()}")
             # Attempt to notify user if interaction hasn't been responded to
             try:
                  if not interaction.response.is_done():
                       await interaction.response.send_message("❌ An error occurred opening the quantity input.", ephemeral=True)
             except Exception: pass # Ignore if sending error message fails

class RemoveItemButton(discord.ui.Button):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.red,
            custom_id=f"remove_{item_name}" # Specific custom_id
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            modal = RemoveQuantityModal(self.internal_name)
            await interaction.response.send_modal(modal)
        except Exception as e:
             logger.error(f"Error in RemoveItemButton callback for {self.internal_name}: {e}\n{traceback.format_exc()}")
             try:
                  if not interaction.response.is_done():
                       await interaction.response.send_message("❌ An error occurred opening the removal input.", ephemeral=True)
             except Exception: pass


class BulkAddModal(discord.ui.Modal, title="Bulk Add Items"):
    items_input = discord.ui.TextInput(
        label="Items (Format: item:qty or qty item, new line/comma)",
        style=discord.TextStyle.paragraph,
        placeholder="bud_sojokush: 50\n25 joint_khalifakush, whacky_bud:10",
        required=True,
        max_length=1500 # Limit input length slightly
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            items_text = self.items_input.value.strip()
            items_to_add = []
            errors = []

            for item_entry in re.split(r'[,\n]', items_text):
                item_entry = item_entry.strip()
                if not item_entry:
                    continue

                match = re.match(r'([a-z_]+)[:\s]+(\d+)', item_entry, re.IGNORECASE)
                if not match:
                    match = re.match(r'(\d+)[:\s]+([a-z_]+)', item_entry, re.IGNORECASE)
                    if match:
                        quantity_str, item_name = match.groups()
                        item_name = item_name.lower()
                    else:
                        errors.append(f"Invalid format: `{item_entry}`")
                        continue
                else:
                    item_name, quantity_str = match.groups()
                    item_name = item_name.lower()

                try:
                    quantity = int(quantity_str)
                    if quantity <= 0:
                         errors.append(f"Quantity must be positive for `{item_name}`: {quantity_str}")
                         continue
                except ValueError:
                    errors.append(f"Invalid quantity for `{item_name}`: {quantity_str}")
                    continue

                original_input_name = item_name # Keep original for error messages if needed
                if not shop_data.is_valid_item(item_name):
                    matches = [i for i in shop_data.get_all_items() if item_name in i]
                    if len(matches) == 1:
                        item_name = matches[0]
                        logger.info(f"Bulk Add: Matched input '{original_input_name}' to item '{item_name}'")
                    else:
                        errors.append(f"Unknown item or ambiguous match: `{original_input_name}`")
                        continue

                items_to_add.append((item_name, quantity))

            if not items_to_add:
                await interaction.followup.send("❌ No valid items found to add.", ephemeral=True)
                return

            user = str(interaction.user)
            total_added_count = 0
            total_value = 0
            total_quantity_added = 0

            for item_name, quantity in items_to_add:
                price = shop_data.predefined_prices.get(item_name, 0)
                value = price * quantity
                shop_data.add_item(item_name, quantity, user)
                shop_data.add_to_history("add_bulk", item_name, quantity, price, user)
                total_added_count += 1
                total_value += value
                total_quantity_added += quantity

            await update_stock_message()
            shop_data.save_data()

            confirmation = f"✅ Added {total_added_count} types of items ({total_quantity_added:,} total) worth ${total_value:,} to stock!"
            if errors:
                confirmation += "\n\n⚠️ **Errors/Warnings:**\n" + "\n".join(f"- {e}" for e in errors)

            await interaction.followup.send(confirmation, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in BulkAddModal on_submit: {e}\n{traceback.format_exc()}")
            try:
                await interaction.followup.send("❌ An unexpected error occurred during bulk add.", ephemeral=True)
            except Exception as followup_e:
                 logger.error(f"Failed to send error followup for BulkAddModal: {followup_e}")

class BulkRemoveModal(discord.ui.Modal, title="Bulk Remove Items"):
    items_input = discord.ui.TextInput(
        label="Items (Format: item:qty or qty item, new line/comma)",
        style=discord.TextStyle.paragraph,
        placeholder="bud_sojokush: 50\n25 joint_khalifakush, whacky_bud:10",
        required=True,
        max_length=1500
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            items_text = self.items_input.value.strip()
            items_to_remove = []
            errors = []
            user = str(interaction.user)

            for item_entry in re.split(r'[,\n]', items_text):
                item_entry = item_entry.strip()
                if not item_entry:
                    continue

                match = re.match(r'([a-z_]+):(\d+)', item_entry, re.IGNORECASE)
                # Allow "50 item" format too for consistency? Not really needed for remove maybe.
                # match_alt = re.match(r'(\d+)[:\s]+([a-z_]+)', item_entry, re.IGNORECASE)
                if not match:
                    #if match_alt: quantity_str, item_name = match_alt.groups() ... else:
                    errors.append(f"Invalid format: `{item_entry}` (Use item:quantity)")
                    continue

                item_name, quantity_str = match.groups()
                item_name = item_name.lower()

                try:
                    quantity = int(quantity_str)
                    if quantity <= 0:
                         errors.append(f"Quantity must be positive for `{item_name}`: {quantity_str}")
                         continue
                except ValueError:
                    errors.append(f"Invalid quantity for `{item_name}`: {quantity_str}")
                    continue

                original_input_name = item_name
                if not shop_data.is_valid_item(item_name):
                    matches = [i for i in shop_data.get_all_items() if item_name in i]
                    if len(matches) == 1:
                        item_name = matches[0]
                        logger.info(f"Bulk Remove: Matched input '{original_input_name}' to item '{item_name}'")
                    else:
                        errors.append(f"Unknown item or ambiguous match: `{original_input_name}`")
                        continue

                user_quantity = shop_data.get_user_quantity(item_name, user)
                if user_quantity < quantity:
                    display_name = shop_data.display_names.get(item_name, item_name)
                    errors.append(f"Not enough `{display_name}` (Have: {user_quantity}, Need: {quantity})")
                    continue

                items_to_remove.append((item_name, quantity))

            if not items_to_remove:
                await interaction.followup.send("❌ No valid items found to remove based on your input and current stock.", ephemeral=True)
                return

            total_removed_count = 0
            total_value = 0
            total_quantity_removed = 0
            actually_removed_items = [] # Track items successfully removed

            for item_name, quantity in items_to_remove:
                price = shop_data.predefined_prices.get(item_name, 0)
                value = price * quantity # Indicative value
                removed_successfully = shop_data.remove_item(item_name, quantity, user)

                if removed_successfully:
                    shop_data.add_to_history("remove_bulk", item_name, quantity, 0, user) # Price 0 for removal history
                    total_removed_count += 1
                    total_value += value
                    total_quantity_removed += quantity
                    actually_removed_items.append(item_name)
                else:
                    display_name = shop_data.display_names.get(item_name, item_name)
                    errors.append(f"Failed removal for {quantity}x `{display_name}` (Insufficient stock during operation?)")

            if actually_removed_items: # Only save and update if something changed
                 await update_stock_message()
                 shop_data.save_data()

            confirmation = f"✅ Removed {total_removed_count} types of items ({total_quantity_removed:,} total) worth approx. ${total_value:,} from your stock!"
            if errors:
                confirmation += "\n\n⚠️ **Errors/Warnings:**\n" + "\n".join(f"- {e}" for e in errors)

            await interaction.followup.send(confirmation, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in BulkRemoveModal on_submit: {e}\n{traceback.format_exc()}")
            try:
                await interaction.followup.send("❌ An unexpected error occurred during bulk remove.", ephemeral=True)
            except Exception as followup_e:
                 logger.error(f"Failed to send error followup for BulkRemoveModal: {followup_e}")

class BulkAddView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=300)
        self.category = category
        self.selected_items: Dict[str, int] = {}

        items_in_category = shop_data.item_categories.get(category, [])
        for item in items_in_category:
            if shop_data.is_valid_item(item):
                display_name = shop_data.display_names.get(item, item)
                button = BulkItemSelectButton(item, display_name)
                self.add_item(button)

        self.add_item(BulkConfirmButton())

class BulkItemSelectButton(discord.ui.Button):
    def __init__(self, item_name: str, display_name: str):
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.gray,
            custom_id=f"bulk_add_select_{item_name}" # Specific ID
        )
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        try:
            if not isinstance(self.view, BulkAddView):
                 logger.error("BulkItemSelectButton callback: self.view is not BulkAddView!")
                 await interaction.response.send_message("❌ Internal UI error.", ephemeral=True)
                 return
            modal = BulkQuantityModal(self.item_name, self.view)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error in BulkItemSelectButton callback for {self.item_name}: {e}\n{traceback.format_exc()}")
            try:
                 if not interaction.response.is_done():
                      await interaction.response.send_message("❌ Error opening quantity input.", ephemeral=True)
            except Exception: pass


class BulkQuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str, parent_view: BulkAddView):
        self.item_name = item_name
        self.parent_view = parent_view
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Set Quantity for {display_name}")

        current_qty = self.parent_view.selected_items.get(self.item_name, 0)

        self.quantity_input = discord.ui.TextInput(
            label="Quantity to add (0 to remove)",
            placeholder="Enter amount (e.g., 50)",
            required=True,
            min_length=1,
            max_length=6,
            default=str(current_qty) if current_qty > 0 else ""
        )
        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        # This modal interaction needs to edit the original message showing the BulkAddView
        original_message = interaction.message
        try:
            # Defer the modal submission response
            await interaction.response.defer(ephemeral=False) # Defer but allow editing the original message

            quantity = int(self.quantity_input.value)
            if quantity < 0:
                await interaction.followup.send("❌ Quantity cannot be negative. Enter 0 to remove/deselect.", ephemeral=True)
                return

            self.parent_view.selected_items[self.item_name] = quantity

            items_text_lines = []
            total_session_value = 0
            for item, qty in self.parent_view.selected_items.items():
                if qty > 0:
                    display_name = shop_data.display_names.get(item, item)
                    price = shop_data.predefined_prices.get(item, 0)
                    item_value = qty * price
                    total_session_value += item_value
                    items_text_lines.append(f"• {display_name}: {qty:,} (${item_value:,})") # Added commas

            if not items_text_lines:
                items_text = "No items selected yet."
            else:
                 items_text = "\n".join(items_text_lines)
                 items_text += f"\n\n**Total Value Selected:** ${total_session_value:,}"

            embed = discord.Embed(
                title=f"🛒 Bulk Add: {self.parent_view.category.title()}",
                description="Click items to set quantities, then click Confirm when done.",
                color=COLORS['INFO']
            )
            embed.add_field(name="Selected Items for this Session", value=items_text, inline=False)

            # Update button appearance in the parent view
            for button in self.parent_view.children:
                 if isinstance(button, BulkItemSelectButton) and button.item_name == self.item_name:
                      display_name = shop_data.display_names.get(self.item_name, self.item_name)
                      button.label = f"{display_name} ({quantity:,})" if quantity > 0 else display_name # Added commas
                      button.style = discord.ButtonStyle.success if quantity > 0 else discord.ButtonStyle.gray
                      break

            # Edit the original message showing the BulkAddView
            if original_message:
                await original_message.edit(embed=embed, view=self.parent_view)
            else:
                 logger.warning("BulkQuantityModal: Could not find original message to edit.")
                 await interaction.followup.send("Updated selection, but couldn't update the original message.", ephemeral=True)

            # Send ephemeral confirmation for the modal itself
            await interaction.followup.send(f"Set {display_name} quantity to {quantity}.", ephemeral=True)


        except ValueError:
            await interaction.followup.send("❌ Please enter a valid number (0 or positive).", ephemeral=True)
        except Exception as e:
             logger.error(f"Error in BulkQuantityModal on_submit: {e}\n{traceback.format_exc()}")
             await interaction.followup.send("❌ An unexpected error occurred setting quantity.", ephemeral=True)


class BulkConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Confirm Selection & Add Stock",
            style=discord.ButtonStyle.primary,
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        original_message = interaction.message # The message with the BulkAddView
        await interaction.response.defer(ephemeral=True)

        try:
            if not isinstance(self.view, BulkAddView):
                logger.error("BulkConfirmButton callback: self.view is not BulkAddView!")
                await interaction.followup.send("❌ Internal UI error.", ephemeral=True)
                return

            view = self.view
            selected_items = [(item, qty) for item, qty in view.selected_items.items() if qty > 0]

            if not selected_items:
                await interaction.followup.send("❌ No items with quantity > 0 selected!", ephemeral=True)
                return

            user = str(interaction.user)
            total_added_count = 0
            total_value = 0
            total_quantity_added = 0
            added_items_details = []

            for item_name, quantity in selected_items:
                price = shop_data.predefined_prices.get(item_name, 0)
                value = price * quantity
                shop_data.add_item(item_name, quantity, user)
                shop_data.add_to_history("add_bulk_visual", item_name, quantity, price, user)
                total_added_count += 1
                total_value += value
                total_quantity_added += quantity
                display_name = shop_data.display_names.get(item_name, item_name)
                added_items_details.append(f"• {display_name}: {quantity:,} (${value:,})") # Added commas

            await update_stock_message()
            shop_data.save_data()

            embed = discord.Embed(
                title="✅ Items Added to Stock (Visual Bulk Add)",
                description=f"Added {total_added_count} types ({total_quantity_added:,} total items) worth ${total_value:,} to stock!",
                color=COLORS['SUCCESS']
            )
            items_text = "\n".join(added_items_details)
            embed.add_field(name="Items Added", value=items_text, inline=False)

            # Edit the original message to show confirmation and remove buttons
            if original_message:
                await original_message.edit(embed=embed, view=None)
            else:
                 logger.warning("BulkConfirmButton: Could not find original message to edit upon confirmation.")

            await interaction.followup.send("Stock added successfully!", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in BulkConfirmButton callback: {e}\n{traceback.format_exc()}")
            try:
                 fail_embed = discord.Embed(title="❌ Error Adding Stock", description="An unexpected error occurred.", color=COLORS['ERROR'])
                 if original_message:
                      await original_message.edit(embed=fail_embed, view=None)
                 await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)
            except Exception as inner_e:
                 logger.error(f"Failed to send error message in BulkConfirmButton callback: {inner_e}")


class RemoveQuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Remove {display_name}")

        self.quantity_input = discord.ui.TextInput( # Renamed
            label=f"Amount to remove",
            placeholder="Enter amount to remove",
            required=True
        )
        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            quantity = int(self.quantity_input.value)
            if quantity <= 0:
                await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
                return

            user = str(interaction.user)
            display_name = shop_data.display_names.get(self.internal_name, self.internal_name)

            total_user_quantity = shop_data.get_user_quantity(self.internal_name, user)
            if total_user_quantity < quantity:
                await interaction.followup.send(
                    f"❌ You only have {total_user_quantity:,}x {display_name}, cannot remove {quantity:,}.",
                    ephemeral=True
                )
                return

            removed_successfully = shop_data.remove_item(self.internal_name, quantity, user)

            if removed_successfully:
                shop_data.add_to_history("remove_quick", self.internal_name, quantity, 0, user)
                shop_data.save_data()
                await update_stock_message()

                embed = discord.Embed(title="✅ Stock Removed", color=COLORS['SUCCESS'])
                remaining_total = shop_data.get_total_quantity(self.internal_name)
                remaining_user = shop_data.get_user_quantity(self.internal_name, user)
                embed.add_field(
                    name="Details",
                    value=f"```ml\nItem:      {display_name}\nRemoved:   {quantity:,}\nRemaining (Yours): {remaining_user:,}\nRemaining (Total): {remaining_total:,}```",
                    inline=False
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                 await interaction.followup.send(
                    f"❌ Failed to remove {quantity:,}x {display_name}. Stock might have changed.",
                    ephemeral=True
                )

        except ValueError:
            await interaction.followup.send("❌ Please enter a valid positive number.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in RemoveQuantityModal on_submit: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ An unexpected error occurred while removing stock.", ephemeral=True)


class RemoveCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    async def _handle_category_button(self, interaction: discord.Interaction, category: str):
        try:
             await self.show_category_items(interaction, category)
        except Exception as e:
             logger.error(f"Error handling remove category button '{category}': {e}\n{traceback.format_exc()}")
             try:
                  # Use send_message because show_category_items will respond if successful
                  if not interaction.response.is_done():
                    await interaction.response.send_message("❌ An error occurred fetching items.", ephemeral=True)
                  else:
                     # If already responded (e.g. defer), use followup
                     await interaction.followup.send("❌ An error occurred fetching items.", ephemeral=True)
             except Exception: pass # Ignore if error reporting fails

    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.red, row=0, custom_id="remove_cat_bud")
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category_button(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.red, row=0, custom_id="remove_cat_joint")
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category_button(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.red, row=1, custom_id="remove_cat_bag")
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
       await self._handle_category_button(interaction, 'bag')

    @discord.ui.button(label="🎮 Tebex", style=discord.ButtonStyle.red, row=1, custom_id="remove_cat_tebex")
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category_button(interaction, 'tebex')

    @discord.ui.button(label="🐟 Fish", style=discord.ButtonStyle.red, row=2, custom_id="remove_cat_fish")
    async def fish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category_button(interaction, 'fish')

    @discord.ui.button(label="🧩 Misc", style=discord.ButtonStyle.red, row=2, custom_id="remove_cat_misc")
    async def misc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category_button(interaction, 'misc')


    async def show_category_items(self, interaction: discord.Interaction, category: str):
        # This interaction *must* be responded to, either with items or no items message
        view = discord.ui.View(timeout=180)
        user = str(interaction.user)
        items_in_category = shop_data.item_categories.get(category, [])
        found_items = False

        for item in items_in_category:
             if shop_data.is_valid_item(item) and shop_data.get_user_quantity(item, user) > 0:
                view.add_item(RemoveItemButton(item))
                found_items = True

        if not found_items:
            await interaction.response.send_message(
                f"❌ You don't have any {category} items in stock to remove!",
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


class QuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str, view_to_return: discord.ui.View):
        self.internal_name = item_name
        self.view_to_return = view_to_return
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Add {display_name}")

        self.quantity_input = discord.ui.TextInput(
            label=f"Amount of {display_name}",
            placeholder="Enter amount to add",
            required=True
        )
        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        original_interaction_message = interaction.message
        try:
            # Defer the modal submission response
            await interaction.response.defer(ephemeral=True)

            quantity = int(self.quantity_input.value)
            if quantity <= 0:
                 await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
                 return

            # add_stock_internal needs an interaction object to potentially send messages on failure.
            # We pass the modal's interaction here. If it fails, it will use interaction.followup.
            success = await add_stock_internal(
                interaction=interaction,
                quantity=quantity,
                item=self.internal_name,
                respond=False # We handle the success response by editing the original message
            )

            if success:
                display_name = shop_data.display_names.get(self.internal_name, self.internal_name)
                price = shop_data.predefined_prices.get(self.internal_name, 0)

                updated_view = None
                try:
                     # Recreate the view that was originally shown
                     if isinstance(self.view_to_return, ItemView):
                          updated_view = ItemView(self.view_to_return.category)
                     elif isinstance(self.view_to_return, CategoryView):
                          updated_view = CategoryView()
                     # Add more view types if needed
                except Exception as view_error:
                     logger.error(f"Failed to recreate view {type(self.view_to_return)} in QuantityModal: {view_error}")

                embed = discord.Embed(
                    title="✅ Item Added",
                    description=f"Added {quantity:,} × {display_name} at ${price:,} each.",
                    color=COLORS['SUCCESS']
                )

                try:
                    # Edit the *original message* that had the ItemView/CategoryView buttons
                    if original_interaction_message:
                         await original_interaction_message.edit(embed=embed, view=updated_view)
                         await interaction.followup.send("Stock added successfully!", ephemeral=True)
                    else:
                         logger.warning("QuantityModal: Original message context lost for edit.")
                         await interaction.followup.send(embed=embed, ephemeral=True) # Fallback

                except discord.errors.NotFound:
                     logger.warning("QuantityModal: Original message not found, could not edit.")
                     await interaction.followup.send(embed=embed, ephemeral=True) # Fallback
                except Exception as edit_error:
                     logger.error(f"QuantityModal: Error editing original message: {edit_error}\n{traceback.format_exc()}")
                     await interaction.followup.send(embed=embed, ephemeral=True) # Fallback

            # If 'success' is False, add_stock_internal should have sent an error message via interaction.followup

        except ValueError:
            try:
                await interaction.followup.send("❌ Please enter a valid positive number.", ephemeral=True)
            except Exception as follow_err:
                 logger.error(f"QuantityModal: Failed to send ValueError followup: {follow_err}")
        except Exception as e:
            logger.error(f"Error in QuantityModal on_submit: {e}\n{traceback.format_exc()}")
            try:
                await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)
            except Exception as follow_err:
                 logger.error(f"QuantityModal: Failed to send general error followup: {follow_err}")


class TemplateSelectView(discord.ui.View):
    def __init__(self, user_id_str: str): # Expect string user ID
        super().__init__(timeout=180)
        self.user_id_str = user_id_str

        select = discord.ui.Select(
            placeholder="Choose a template to apply...",
            min_values=1,
            max_values=1,
            custom_id="template_select_apply"
        )

        templates = shop_data.get_user_templates(self.user_id_str)
        if not templates:
             select.add_option(label="No templates found", value="no_templates_placeholder", default=True)
             select.disabled = True
        else:
             # Sort template names alphabetically for consistency
             for name in sorted(templates.keys()):
                  select.add_option(label=name, value=name)

        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        # This interaction edits the original message (which showed the select menu)
        original_message = interaction.message
        try:
            template_name = interaction.data['values'][0]

            if template_name == "no_templates_placeholder":
                 await interaction.response.edit_message(content="No template selected.", embed=None, view=None)
                 return

            # Verify the user interacting is the one the view was intended for? (Optional if ephemeral)
            # if str(interaction.user.id) != self.user_id_str:
            #    await interaction.response.send_message("❌ You cannot use this menu.", ephemeral=True)
            #    return

            user_str = str(interaction.user) # Use the interacting user
            templates = shop_data.get_user_templates(user_str)

            if template_name not in templates:
                await interaction.response.edit_message(
                    content=f"❌ Template '{template_name}' not found.",
                    embed=None, view=None
                )
                return

            template_items = templates[template_name]
            item_details = []
            total_value = 0
            total_quantity = 0
            valid_item_count = 0

            for item, quantity in template_items.items():
                if not shop_data.is_valid_item(item) or quantity <= 0:
                     continue
                valid_item_count += 1
                display_name = shop_data.display_names.get(item, item)
                price = shop_data.predefined_prices.get(item, 0)
                value = quantity * price
                total_value += value
                total_quantity += quantity
                item_details.append(f"{display_name}: {quantity:,} (${value:,})")

            if not item_details:
                 await interaction.response.edit_message(
                     content=f"❌ Template '{template_name}' has no valid items with quantity > 0.",
                     embed=None, view=None
                 )
                 return

            embed = discord.Embed(
                title=f"📄 Apply Template: {template_name}",
                description=f"Contains **{valid_item_count}** valid item types (**{total_quantity:,}** total quantity).",
                color=COLORS['INFO']
            )

            details_str = "```ml\n" + "\n".join(sorted(item_details)) + "```" # Sort items for consistent display
            # Simple split if too long
            if len(details_str) > 1024:
                 split_point = len(item_details) // 2
                 part1 = "```ml\n" + "\n".join(sorted(item_details)[:split_point]) + "```"
                 part2 = "```ml\n" + "\n".join(sorted(item_details)[split_point:]) + "```"
                 embed.add_field(name="Items in Template (Part 1)", value=part1, inline=False)
                 embed.add_field(name="Items in Template (Part 2)", value=part2, inline=False)
            else:
                 embed.add_field(name="Items in Template", value=details_str, inline=False)

            embed.add_field(name="💰 Total Value to Add", value=f"${total_value:,}", inline=False)
            embed.set_footer(text="Click Apply to add these items to your stock.")

            confirm_view = TemplateConfirmView(template_name)
            await interaction.response.edit_message(embed=embed, view=confirm_view)

        except Exception as e:
             logger.error(f"Error in TemplateSelectView select_callback: {e}\n{traceback.format_exc()}")
             try:
                # Try editing the original message to show an error
                if original_message and not interaction.response.is_done():
                    await interaction.response.edit_message(content="❌ Error displaying template details.", embed=None, view=None)
                # If already responded (e.g. from a failed edit), try followup
                elif interaction.response.is_done():
                    await interaction.followup.send("❌ Error displaying template details.", ephemeral=True)
             except Exception: pass # Ignore errors during error reporting


class TemplateConfirmView(discord.ui.View):
    def __init__(self, template_name):
        super().__init__(timeout=180)
        self.template_name = template_name

    @discord.ui.button(label="✅ Apply Template & Add Stock", style=discord.ButtonStyle.success, custom_id="template_confirm_apply")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        original_message = interaction.message
        await interaction.response.defer(ephemeral=True)

        try:
            user = str(interaction.user)
            template = shop_data.get_user_templates(user).get(self.template_name, {})

            if not template:
                await interaction.followup.send(f"❌ Template '{self.template_name}' not found or empty.", ephemeral=True)
                if original_message: await original_message.edit(content=f"Template '{self.template_name}' not found.", embed=None, view=None)
                return

            added_items_count = 0
            total_value_added = 0
            total_quantity_added = 0
            item_details = []
            errors = []

            for item, quantity in template.items():
                if not shop_data.is_valid_item(item):
                     errors.append(f"Skipped invalid item: `{item}`")
                     continue
                if quantity <= 0:
                     continue

                price = shop_data.predefined_prices.get(item, 0)
                value = quantity * price
                shop_data.add_item(item, quantity, user)
                shop_data.add_to_history("add_template", item, quantity, price, user) # Specific action
                display_name = shop_data.display_names.get(item, item)
                item_details.append(f"{display_name}: {quantity:,} (${value:,})")
                added_items_count += 1
                total_value_added += value
                total_quantity_added += quantity

            if added_items_count > 0:
                shop_data.save_data()
                await update_stock_message()

                embed = discord.Embed(
                    title=f"✅ Applied Template: {self.template_name}",
                    description=f"Added **{added_items_count}** item types ({total_quantity_added:,} total) worth **${total_value_added:,}**.",
                    color=COLORS['SUCCESS']
                )
                details_str = "```ml\n" + "\n".join(sorted(item_details)) + "```"
                # Simple split if too long
                if len(details_str) > 1024:
                     split_point = len(item_details) // 2
                     part1 = "```ml\n" + "\n".join(sorted(item_details)[:split_point]) + "```"
                     part2 = "```ml\n" + "\n".join(sorted(item_details)[split_point:]) + "```"
                     embed.add_field(name="Items Added (Part 1)", value=part1, inline=False)
                     embed.add_field(name="Items Added (Part 2)", value=part2, inline=False)
                else:
                     embed.add_field(name="Items Added", value=details_str, inline=False)

                if errors:
                     embed.add_field(name="⚠️ Warnings", value="\n".join(errors), inline=False)

                if original_message:
                     await original_message.edit(embed=embed, view=None) # Show result in original message
                await interaction.followup.send(f"Template '{self.template_name}' applied successfully!", ephemeral=True)
            else:
                error_msg = f"❌ No valid items with quantity > 0 found in template '{self.template_name}'."
                if errors:
                     error_msg += "\n" + "\n".join(errors)
                await interaction.followup.send(error_msg, ephemeral=True)
                if original_message: await original_message.edit(content=error_msg, embed=None, view=None)

        except Exception as e:
            logger.error(f"Error in TemplateConfirmView confirm_button: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ An unexpected error occurred while applying the template.", ephemeral=True)
            try: # Try to update original message too
                 if original_message: await original_message.edit(content="❌ Error applying template.", embed=None, view=None)
            except: pass

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="template_cancel_apply")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Edit the original message where the confirmation was shown
            await interaction.response.edit_message(
                content="Template application cancelled.",
                embed=None,
                view=None
            )
        except Exception as e:
            logger.error(f"Error cancelling template apply: {e}")
            # Fallback if edit fails
            await interaction.followup.send("Cancelled.", ephemeral=True)


class TemplateNameModal(discord.ui.Modal):
    def __init__(self, existing_name: Optional[str] = None, is_edit: bool = False):
        self.existing_name = existing_name
        self.is_edit = is_edit
        title = f"Edit Template Name: {existing_name}" if is_edit else "Create New Template"
        super().__init__(title=title)

        self.template_name_input = discord.ui.TextInput( # Renamed
            label="Template Name",
            placeholder="My Daily Restock",
            required=True,
            max_length=40, # Slightly longer max length
            min_length=3,
            default=existing_name if existing_name else ""
        )
        self.add_item(self.template_name_input)

    async def on_submit(self, interaction: discord.Interaction):
        # This modal starts the visual editor flow
        try:
            template_name = self.template_name_input.value.strip()
            if not template_name:
                 await interaction.response.send_message("❌ Template name cannot be empty.", ephemeral=True)
                 return

            user = str(interaction.user)

            # Check for name collision only when creating or renaming to a different name
            if template_name != self.existing_name and template_name in shop_data.get_user_templates(user):
                 await interaction.response.send_message(f"❌ A template named '{template_name}' already exists.", ephemeral=True)
                 return

            # If editing, update the name in the data store first (if changed)
            if self.is_edit and self.existing_name and template_name != self.existing_name:
                 if user in shop_data.user_templates and self.existing_name in shop_data.user_templates[user]:
                      shop_data.user_templates[user][template_name] = shop_data.user_templates[user].pop(self.existing_name)
                      shop_data.save_data() # Save name change
                 else:
                      await interaction.response.send_message(f"❌ Error renaming: Original template '{self.existing_name}' not found.", ephemeral=True)
                      return

            # Initialize the visual category view
            template_view = TemplateVisualCategoryView(template_name)
            template_view.user_id_str = user # Pass user ID

            # If editing, load existing items
            if self.is_edit or template_name in shop_data.user_templates.get(user, {}):
                 template_items = shop_data.user_templates.get(user, {}).get(template_name, {})
                 template_view.selected_items = template_items.copy() # Load existing

            # Create initial embed for the editor
            embed = template_view.create_current_selection_embed() # Use helper to build embed
            action_word = "Editing" if self.is_edit else "Creating"
            embed.title = f"📋 {action_word} Template: {template_name}"
            embed.description = "Select categories to add or modify items."

            embed.add_field(
                name="Instructions",
                value="1. Click a category button.\n"
                      "2. Click items to set quantities (0 to remove).\n"
                      "3. Use 'Back' to return here.\n"
                      "4. Click 'Finish & Save' when done.",
                inline=False
            )

            # Send the editor interface as the response to the modal
            await interaction.response.send_message(
                content=f"{action_word} template: **{template_name}**",
                embed=embed,
                view=template_view,
                ephemeral=True
            )

        except Exception as e:
             logger.error(f"Error in TemplateNameModal on_submit: {e}\n{traceback.format_exc()}")
             # Attempt to respond if possible
             try:
                  if not interaction.response.is_done():
                       await interaction.response.send_message("❌ An unexpected error occurred starting the template editor.", ephemeral=True)
             except Exception: pass


class TemplateItemButton(discord.ui.Button):
    def __init__(self, template_name: str, item_name: str):
        # This class seems unused now with the visual editor? Keep for potential future use or remove?
        # Let's assume it might be used elsewhere or was part of an older flow.
        self.template_name = template_name
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.gray,
            custom_id=f"template_add_{item_name}" # Specific ID
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            modal = TemplateItemQuantityModal(self.template_name, self.internal_name)
            await interaction.response.send_modal(modal)
        except Exception as e:
             logger.error(f"Error in TemplateItemButton callback for {self.internal_name}: {e}\n{traceback.format_exc()}")
             try:
                  if not interaction.response.is_done():
                       await interaction.response.send_message("❌ Error opening quantity input.", ephemeral=True)
             except Exception: pass

class TemplateItemQuantityModal(discord.ui.Modal):
    # This class seems unused now with the visual editor? Keep for potential future use or remove?
    # Let's assume it might be used elsewhere or was part of an older flow.
    def __init__(self, template_name: str, item_name: str):
        self.template_name = template_name
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Add {display_name} to Template")

        self.quantity_input = discord.ui.TextInput( # Renamed
            label=f"Amount of {display_name}",
            placeholder="Enter amount to add",
            required=True
        )
        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True) # Defer modal submission

            quantity = int(self.quantity_input.value)
            if quantity <= 0:
                 await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
                 return

            user = str(interaction.user)

            if user not in shop_data.user_templates:
                shop_data.user_templates[user] = {}
            if self.template_name not in shop_data.user_templates[user]:
                shop_data.user_templates[user][self.template_name] = {}

            shop_data.user_templates[user][self.template_name][self.internal_name] = quantity
            shop_data.save_data()

            display_name = shop_data.display_names.get(self.internal_name, self.internal_name)
            price = shop_data.predefined_prices.get(self.internal_name, 0)
            value = quantity * price

            embed = discord.Embed(
                title="✅ Item Added/Updated in Template",
                color=COLORS['SUCCESS']
            )
            embed.add_field(
                name="Details",
                value=f"```ml\nTemplate: {self.template_name}\nItem:     {display_name}\nQuantity: {quantity:,}\nValue:    ${value:,}```",
                inline=False
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError:
            await interaction.followup.send("❌ Please enter a valid positive number.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in TemplateItemQuantityModal on_submit: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

class TemplateCategoryView(discord.ui.View):
    # This class seems unused now with the visual editor? Keep for potential future use or remove?
    # Let's assume it might be used elsewhere or was part of an older flow.
    def __init__(self, template_name):
        super().__init__(timeout=180)
        self.template_name = template_name

    async def _show_items(self, interaction: discord.Interaction, category: str):
         try:
              view = discord.ui.View(timeout=180)
              items_in_category = shop_data.item_categories.get(category, [])
              found = False
              for item in items_in_category:
                   if shop_data.is_valid_item(item):
                        view.add_item(TemplateItemButton(self.template_name, item))
                        found = True

              if not found:
                   await interaction.response.send_message(f"No valid items found in category '{category}'.", ephemeral=True)
                   return

              await interaction.response.send_message(
                   f"Select items from **{category.title()}** to add to template '{self.template_name}':",
                   view=view,
                   ephemeral=True
              )
         except Exception as e:
              logger.error(f"Error showing template category items: {e}\n{traceback.format_exc()}")
              try:
                   if not interaction.response.is_done():
                        await interaction.response.send_message("❌ Error loading items.", ephemeral=True)
              except: pass


    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green, custom_id="tpl_cat_bud")
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_items(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple, custom_id="tpl_cat_joint")
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_items(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray, custom_id="tpl_cat_bag")
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_items(interaction, 'bag')

    @discord.ui.button(label="💎 Tebex", style=discord.ButtonStyle.primary, row=1, custom_id="tpl_cat_tebex")
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_items(interaction, 'tebex')
    
    @discord.ui.button(label="🐟 fish", style=discord.ButtonStyle.primary, row=1, custom_id="tpl_cat_fish")
    async def fish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_items(interaction, 'fish')
            
    @discord.ui.button(label="🧩 Misc", style=discord.ButtonStyle.primary, row=1, custom_id="tpl_cat_misc")
    async def misc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_items(interaction, 'misc')
        


class TemplateItemView(discord.ui.View):
    # This class seems unused now with the visual editor? Keep for potential future use or remove?
    def __init__(self, template_name: str, category: str):
        super().__init__(timeout=180)
        self.template_name = template_name
        self.category = category

        items_in_category = shop_data.item_categories.get(category, [])
        for item in items_in_category:
            if shop_data.is_valid_item(item):
                button = TemplateItemButton(self.template_name, item)
                self.add_item(button)


class TemplateDeleteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.select = discord.ui.Select(
            placeholder="Choose a template to delete...",
            min_values=1,
            max_values=1,
            custom_id="template_select_delete"
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def setup_for_user(self, user_id_str: str):
        self.select.options = [] # Clear existing options
        templates = shop_data.get_user_templates(user_id_str)
        if not templates:
             self.select.add_option(label="No templates to delete", value="no_templates_placeholder", default=True)
             self.select.disabled = True
        else:
             self.select.disabled = False
             for name in sorted(templates.keys()):
                  self.select.add_option(label=name, value=name)
        return self

    async def select_callback(self, interaction: discord.Interaction):
        # This interaction edits the original message (which showed the select menu)
        original_message = interaction.message
        try:
            template_name = interaction.data['values'][0]
            if template_name == "no_templates_placeholder":
                 await interaction.response.edit_message(content="No template selected.", embed=None, view=None)
                 return

            user = str(interaction.user)

            if user in shop_data.user_templates and template_name in shop_data.user_templates[user]:
                del shop_data.user_templates[user][template_name]
                shop_data.save_data()
                logger.info(f"User '{user}' deleted template '{template_name}'")
                await interaction.response.edit_message(
                    content=f"✅ Template **{template_name}** deleted successfully.",
                    embed=None, view=None
                )
            else:
                await interaction.response.edit_message(
                    content=f"❌ Template '{template_name}' not found.",
                    embed=None, view=None
                )
        except Exception as e:
            logger.error(f"Error in TemplateDeleteView select_callback: {e}\n{traceback.format_exc()}")
            try:
                 if original_message and not interaction.response.is_done():
                      await interaction.response.edit_message(content="❌ Error deleting template.", embed=None, view=None)
                 elif interaction.response.is_done():
                      await interaction.followup.send("❌ Error deleting template.", ephemeral=True)
            except: pass


class TemplateVisualCategoryView(discord.ui.View):
    def __init__(self, template_name):
        super().__init__(timeout=300) # Longer timeout for editor
        self.template_name = template_name
        self.selected_items: Dict[str, int] = {} # Stores items selected *in this editing session*
        self.user_id_str: Optional[str] = None # Store user ID for permission checks?

    # Helper for category buttons
    async def _handle_category(self, interaction: discord.Interaction, category: str):
         try:
              await self.show_category_items(interaction, category)
         except Exception as e:
              logger.error(f"Error showing template visual category '{category}': {e}\n{traceback.format_exc()}")
              # Try to edit the current message if possible
              try:
                   await interaction.response.edit_message(content=f"Error loading {category} items.", embed=None, view=self) # Keep current view on error?
              except Exception: pass

    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green, row=0, custom_id="tpl_vis_cat_bud")
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple, row=0, custom_id="tpl_vis_cat_joint")
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray, row=1, custom_id="tpl_vis_cat_bag")
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'bag')

    @discord.ui.button(label="💎 Tebex", style=discord.ButtonStyle.primary, row=1, custom_id="tpl_vis_cat_tebex")
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'tebex')
        
    @discord.ui.button(label="🐟 Fish", style=discord.ButtonStyle.primary, row=2, custom_id="tpl_vis_cat_fish")
    async def fish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'fish')   
        
    @discord.ui.button(label="🧩 Misc", style=discord.ButtonStyle.primary, row=2, custom_id="tpl_vis_cat_misc")
    async def misc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'misc')      

    @discord.ui.button(label="✅ Finish & Save Template", style=discord.ButtonStyle.success, row=2, custom_id="tpl_vis_finish")
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.save_template(interaction)

    @discord.ui.button(label="✏️ Rename", style=discord.ButtonStyle.secondary, row=2, custom_id="tpl_vis_rename")
    async def rename_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
             # Show modal to rename the current template
             rename_modal = TemplateNameModal(existing_name=self.template_name, is_edit=True)
             await interaction.response.send_modal(rename_modal)
             # Note: The modal's on_submit now handles updating the name and relaunching the editor view.
             # We might lose the current `selected_items` state if the modal relaunches from scratch.
             # A more robust rename would update the view's template_name attribute directly after modal success.
        except Exception as e:
            logger.error(f"Error opening rename modal: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ Error opening rename modal.", ephemeral=True)


    async def show_category_items(self, interaction: discord.Interaction, category: str):
        # This interaction needs to edit the current message (showing the category view)
        embed = self.create_current_selection_embed() # Get updated embed first
        item_view = TemplateVisualItemView(self.template_name, category, self.selected_items, self.user_id_str) # Pass user_id

        await interaction.response.edit_message(
            content=f"Select items from **{category.title()}** to add/modify in template '{self.template_name}':",
            embed=embed,
            view=item_view
        )

    def create_current_selection_embed(self):
        embed = discord.Embed(color=COLORS['INFO']) # Title set later
        if not self.user_id_str:
             logger.warning("create_current_selection_embed called without user_id_str set!")
             embed.description = "Error: User context lost."
             return embed

        # Get the items currently stored in the *data* for this template
        user_templates = shop_data.user_templates.get(self.user_id_str, {})
        # Use self.selected_items which holds the state of *this editing session*
        current_selection = self.selected_items

        embed.title = f"📋 Template Editor: {self.template_name}"

        if not current_selection:
            embed.description = "No items selected for this template yet."
            return embed

        by_category: Dict[str, List[str]] = {}
        total_value = 0
        total_items = 0
        valid_item_count = 0

        # Group current selection by category
        for item, qty in current_selection.items():
            if qty <= 0: # Skip items marked for removal in this session
                continue

            category = shop_data.get_category_for_item(item)
            if not category: continue # Skip if item somehow lost its category

            valid_item_count +=1
            total_items += qty

            if category not in by_category:
                by_category[category] = []

            price = shop_data.predefined_prices.get(item, 0)
            value = qty * price
            total_value += value
            display_name = shop_data.display_names.get(item, item)
            by_category[category].append(f"{display_name}: {qty:,} (${value:,})")

        if valid_item_count == 0:
             embed.description = "No items with quantity > 0 selected."
             return embed

        embed.description = f"Currently editing **{valid_item_count}** item types with **{total_items:,}** total quantity."

        # Sort categories for consistent display
        sorted_categories = sorted(by_category.keys())

        for category in sorted_categories:
            items = by_category[category]
            if not items: continue

            # Use emojis from config/shop_data
            emoji = shop_data.category_emojis.get(category, '📦')
            # Sort items within category for consistent display
            field_value = "\n".join(sorted(items))
            # Handle potential field value limit
            if len(field_value) > 1020: field_value = field_value[:1020] + "..."
            embed.add_field(
                name=f"{emoji} {category.title()}",
                value=field_value,
                inline=False
            )

        embed.add_field(name="💰 Total Template Value", value=f"${total_value:,}", inline=False)
        return embed

    async def save_template(self, interaction: discord.Interaction):
        # This interaction edits the original message (which showed the editor)
        original_message = interaction.message
        await interaction.response.defer(ephemeral=True) # Defer the button click

        try:
            if not self.user_id_str: # Ensure user context is available
                 logger.error("Cannot save template: user_id_str not set in view.")
                 await interaction.followup.send("❌ Error: User context lost. Cannot save.", ephemeral=True)
                 return

            user = self.user_id_str # Use the stored user ID

            # Get the state of the template *before* this editing session started
            original_items = shop_data.user_templates.get(user, {}).get(self.template_name, {}).copy()

            # Prepare the new template data from the current selection state
            new_template_data = {item: qty for item, qty in self.selected_items.items() if qty > 0}

            # Save the updated or new template
            if user not in shop_data.user_templates:
                shop_data.user_templates[user] = {}
            shop_data.user_templates[user][self.template_name] = new_template_data
            shop_data.save_data()

            logger.info(f"User '{user}' saved template '{self.template_name}'")

            # Create confirmation embed using the final state
            embed = self.create_current_selection_embed() # Regenerate embed with final data
            embed.color = COLORS['SUCCESS'] # Make it green

            # Determine if it was an edit or creation based on original_items
            is_edit = bool(original_items)
            action_word = "Updated" if is_edit else "Saved"
            embed.title = f"✅ Template {action_word}: {self.template_name}"


            # --- Optional: Show detailed changes (can make embed long) ---
            if is_edit:
                 added_items, removed_items, modified_items = [], [], []
                 current_items_set = set(new_template_data.keys())
                 original_items_set = set(original_items.keys())

                 for item in current_items_set - original_items_set:
                      display_name = shop_data.display_names.get(item, item)
                      added_items.append(f"+ {display_name}: {new_template_data[item]:,}")
                 for item in original_items_set - current_items_set:
                      display_name = shop_data.display_names.get(item, item)
                      removed_items.append(f"- {display_name}: {original_items[item]:,}")
                 for item in current_items_set.intersection(original_items_set):
                      if new_template_data[item] != original_items[item]:
                           display_name = shop_data.display_names.get(item, item)
                           modified_items.append(f"~ {display_name}: {original_items[item]:,} → {new_template_data[item]:,}")

                 change_summary = ""
                 if added_items: change_summary += "**Added:**\n" + "\n".join(sorted(added_items)) + "\n"
                 if modified_items: change_summary += "**Modified:**\n" + "\n".join(sorted(modified_items)) + "\n"
                 if removed_items: change_summary += "**Removed:**\n" + "\n".join(sorted(removed_items)) + "\n"

                 if change_summary:
                      if len(change_summary) > 1020: change_summary = change_summary[:1020] + "..."
                      embed.add_field(name="Changes Made", value=change_summary, inline=False)
                 else:
                      embed.add_field(name="Changes Made", value="No changes detected.", inline=False)
            # --- End Optional Changes Summary ---


            # Edit the original message to show the final saved state and remove buttons
            if original_message:
                await original_message.edit(content=f"Template **{self.template_name}** saved successfully!", embed=embed, view=None)
            else:
                 logger.warning("Could not find original message to edit after saving template.")

            await interaction.followup.send(f"Template '{self.template_name}' saved!", ephemeral=True)

        except Exception as e:
            logger.error(f"Error saving template '{self.template_name}': {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ An unexpected error occurred while saving the template.", ephemeral=True)
            try: # Try to update original message too
                 if original_message: await original_message.edit(content="❌ Error saving template.", embed=None, view=None)
            except: pass


class TemplateVisualItemView(discord.ui.View):
    def __init__(self, template_name, category, selected_items, user_id_str):
        super().__init__(timeout=300) # Match parent view timeout?
        self.template_name = template_name
        self.category = category
        self.selected_items = selected_items
        self.user_id_str = user_id_str # Pass user ID

        items_in_category = shop_data.item_categories.get(category, [])
        item_count = 0
        for item in items_in_category:
            if shop_data.is_valid_item(item):
                button = TemplateVisualItemButton(item, self.selected_items.get(item, 0))
                self.add_item(button)
                item_count += 1

        # Add Back button only if there were items
        if item_count > 0:
             self.add_item(self.create_back_button())

    def create_back_button(self):
        back_button = discord.ui.Button(
            label="↩️ Back to Categories",
            style=discord.ButtonStyle.secondary,
            row=4, # Place consistently
            custom_id="tpl_vis_back"
        )

        async def back_callback(interaction: discord.Interaction):
            # This interaction needs to edit the current message (showing the item view)
            original_message = interaction.message
            try:
                # Create the main category view again, preserving state
                category_view = TemplateVisualCategoryView(self.template_name)
                category_view.selected_items = self.selected_items # Pass current selections back
                category_view.user_id_str = self.user_id_str # Pass user ID back

                embed = category_view.create_current_selection_embed() # Regenerate embed
                embed.title = f"📋 Editing Template: {self.template_name}" # Reset title
                embed.description = "Select categories to add or modify items." # Reset description
                embed.add_field(
                    name="Instructions",
                    value="1. Click a category button.\n"
                          "2. Click items to set quantities (0 to remove).\n"
                          "3. Use 'Back' to return here.\n"
                          "4. Click 'Finish & Save' when done.",
                    inline=False
                )


                await interaction.response.edit_message(
                    content=f"Editing template: **{self.template_name}**",
                    embed=embed,
                    view=category_view
                )
            except Exception as e:
                 logger.error(f"Error in template visual back button: {e}\n{traceback.format_exc()}")
                 try:
                      if original_message and not interaction.response.is_done():
                           await interaction.response.edit_message(content="❌ Error going back.", embed=None, view=None)
                      elif interaction.response.is_done():
                           await interaction.followup.send("❌ Error going back.", ephemeral=True)

                 except: pass

        back_button.callback = back_callback
        return back_button


class TemplateVisualItemButton(discord.ui.Button):
    def __init__(self, item_name, current_qty=0):
        self.item_name = item_name
        self.current_qty = current_qty
        display_name = shop_data.display_names.get(item_name, item_name)

        label = display_name
        style = discord.ButtonStyle.gray
        if current_qty > 0:
            label = f"{display_name} ({current_qty:,})" # Added comma
            style = discord.ButtonStyle.success # Use success style if selected

        super().__init__(
            label=label,
            style=style,
            custom_id=f"tpl_vis_item_{item_name}" # Specific ID
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            # Ensure the parent view is the ItemView
            if not isinstance(self.view, TemplateVisualItemView):
                 logger.error("TemplateVisualItemButton callback: self.view is not TemplateVisualItemView!")
                 await interaction.response.send_message("❌ Internal UI error.", ephemeral=True)
                 return

            modal = TemplateVisualQuantityModal(self.item_name, self.view)
            await interaction.response.send_modal(modal)
        except Exception as e:
             logger.error(f"Error in TemplateVisualItemButton callback for {self.item_name}: {e}\n{traceback.format_exc()}")
             try:
                  if not interaction.response.is_done():
                       await interaction.response.send_message("❌ Error opening quantity input.", ephemeral=True)
             except Exception: pass


class TemplateVisualQuantityModal(discord.ui.Modal):
    def __init__(self, item_name, parent_view: TemplateVisualItemView): # Type hint parent
        self.item_name = item_name
        self.parent_view = parent_view
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Set Qty for {display_name}")

        current_qty = parent_view.selected_items.get(item_name, 0)

        self.quantity_input = discord.ui.TextInput( # Renamed
            label=f"Quantity (0 to remove)",
            placeholder="Enter amount",
            required=True,
            default=str(current_qty) if current_qty > 0 else ""
        )
        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        # This interaction edits the message showing the TemplateVisualItemView
        original_message = interaction.message
        try:
            # Defer modal submission but allow editing original message
            await interaction.response.defer(ephemeral=False)

            quantity = int(self.quantity_input.value)
            if quantity < 0:
                await interaction.followup.send("❌ Quantity cannot be negative.", ephemeral=True)
                return

            # Update the parent view's selection state
            self.parent_view.selected_items[self.item_name] = quantity

            # --- Recreate the Item View with updated buttons ---
            # This is necessary because we need to change button labels/styles
            new_item_view = TemplateVisualItemView(
                self.parent_view.template_name,
                self.parent_view.category,
                self.parent_view.selected_items,
                self.parent_view.user_id_str # Pass user ID along
            )
            # --- ---

            # Create embed confirming the change
            display_name = shop_data.display_names.get(self.item_name, self.item_name)
            embed = discord.Embed(color=COLORS['SUCCESS'])
            if quantity > 0:
                 embed.title = f"✅ Set {display_name} to {quantity:,}"
            else:
                 embed.title = f"ℹ️ Removed {display_name} from template"

            # Add summary of current selections in this category
            current_category_items = []
            category_value = 0
            for item, qty in self.parent_view.selected_items.items():
                if qty > 0 and shop_data.get_category_for_item(item) == self.parent_view.category:
                    d_name = shop_data.display_names.get(item, item)
                    price = shop_data.predefined_prices.get(item, 0) * qty
                    category_value += price
                    current_category_items.append(f"{d_name}: {qty:,} (${price:,})")

            if current_category_items:
                field_value = "\n".join(sorted(current_category_items))
                if len(field_value) > 1020: field_value = field_value[:1020] + "..."
                embed.add_field(
                    name=f"Current {self.parent_view.category.title()} Selections (${category_value:,})",
                    value=field_value,
                    inline=False
                )

            # Edit the message showing the item list
            if original_message:
                await original_message.edit(
                    content=f"Select items from **{self.parent_view.category.title()}** for template '{self.parent_view.template_name}':",
                    embed=embed,
                    view=new_item_view # Show the view with updated buttons
                )
            else:
                 logger.warning("TemplateVisualQuantityModal: Could not find original message to edit.")

            # Send ephemeral followup for the modal itself
            await interaction.followup.send(f"Updated {display_name}.", ephemeral=True)

        except ValueError:
            await interaction.followup.send("❌ Please enter a valid number (0 or positive).", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in TemplateVisualQuantityModal on_submit: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)


class CategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    async def _handle_category(self, interaction: discord.Interaction, category: str, title: str, color: int):
        try:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=title,
                    description="Select an item to add to your stock:",
                    color=color
                ),
                view=ItemView(category), # ItemView handles showing items
                ephemeral=True
            )
        except Exception as e:
             logger.error(f"Error handling category button '{category}': {e}\n{traceback.format_exc()}")
             try:
                  # Check if already responded
                  if not interaction.response.is_done():
                       await interaction.response.send_message("❌ Error loading items.", ephemeral=True)
                  else: # If somehow already responded (e.g. defer?)
                       await interaction.followup.send("❌ Error loading items.", ephemeral=True)
             except Exception: pass # Ignore errors during error reporting


    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green, row=0, custom_id="quickadd_cat_bud")
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'bud', "🥦 Add Buds", COLORS['SUCCESS'])

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple, row=0, custom_id="quickadd_cat_joint")
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'joint', "🚬 Add Joints", COLORS['INFO'])

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray, row=1, custom_id="quickadd_cat_bag")
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'bag', "🛍️ Add Bags", COLORS['DEFAULT'])

    @discord.ui.button(label="💎 Tebex", style=discord.ButtonStyle.primary, row=1, custom_id="quickadd_cat_tebex")
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_category(interaction, 'tebex', "💎 Add Tebex Items", COLORS['INFO'])


class StockView(discord.ui.View):
    # This view is sent ephemerally, so timeout is less critical but keep it reasonable
    def __init__(self):
        super().__init__(timeout=300)

    async def _show_category(self, interaction: discord.Interaction, category_filter: Optional[str] = None):
        # This interaction must respond or edit the original response
        original_message = interaction.message
        try:
            user = str(interaction.user)
            # Load preference *within* the function to get the latest
            compact_mode = shop_data.get_user_preference(user, "compact_view", False)

            embed = discord.Embed(
                title="📊 Current Shop Stock",
                color=COLORS['INFO'],
                timestamp=datetime.datetime.now()
            )

            total_value = 0
            any_stock = False

            # Determine categories to display
            categories_to_show = []
            if category_filter and category_filter != 'all':
                 if category_filter in shop_data.item_categories:
                      categories_to_show = [(category_filter, shop_data.item_categories[category_filter])]
                 else:
                      # Handle invalid category filter? Show error or default to all? Default to all.
                      categories_to_show = sorted(shop_data.item_categories.items())
            else:
                 categories_to_show = sorted(shop_data.item_categories.items()) # Sort for consistent order

            for cat, category_items in categories_to_show:
                content = []
                category_value = 0
                category_has_stock = False

                # Sort items within category
                sorted_items = sorted(category_items, key=lambda x: shop_data.display_names.get(x, x))

                for item_name in sorted_items:
                    qty = shop_data.get_total_quantity(item_name)
                    if qty > 0:
                        any_stock = True
                        category_has_stock = True
                        price = shop_data.predefined_prices.get(item_name, 0)
                        value = qty * price
                        category_value += value
                        display_name = shop_data.display_names.get(item_name, item_name)
                        low_threshold = shop_data.low_stock_thresholds.get(cat, 0) # Get from shop_data

                        if compact_mode:
                            status = "⚠️" if low_threshold > 0 and qty <= low_threshold else ""
                            content.append(f"{display_name}: {qty:,} (${value:,}) {status}".strip())
                        else:
                            status = ""
                            if low_threshold > 0: # Only show status if threshold is set
                                if qty <= low_threshold: status = "⚠️ LOW"
                                elif qty >= low_threshold * 3: status = "📈 HIGH"
                                # else: status = "✅ OK" # Reduce clutter, only show warnings/highs
                            # Format for alignment
                            formatted_price = f"${price:,}" if price else "N/A"
                            formatted_value = f"${value:,}" if price else "N/A"
                            content.append(f"`{display_name[:15]:<15} {qty:>5,} @ {formatted_price:>8} = {formatted_value:>10} {status}`")

                if category_has_stock:
                    total_value += category_value
                    category_title = f"{shop_data.category_emojis.get(cat, '📦')} {cat.upper()}" # Get emoji from shop_data
                    name = f"{category_title}: ${category_value:,}" if compact_mode else f"{category_title} (${category_value:,})"
                    value_str = "\n".join(content)
                    if len(value_str) > 1020: value_str = value_str[:1020] + "..." # Truncate if needed
                    embed.add_field(name=name, value=value_str, inline=False)
                elif category_filter and category_filter != 'all': # Only show "no stock" if filtering specifically
                     embed.add_field(name=f"{shop_data.category_emojis.get(cat, '📦')} {cat.upper()}", value="No stock in this category.", inline=False)


            if any_stock:
                embed.description = f"💰 **Total Value:** ${total_value:,}"
            else:
                embed.description = "No items currently in stock across all categories."

            # Add toggle button view
            toggle_view = StockViewToggle(compact_mode)

            embed.set_footer(text=f"{'Compact' if compact_mode else 'Standard'} View • /quickadd, /add, /template")

            # Decide how to respond: send new or edit existing
            if interaction.type == discord.InteractionType.component: # If button was clicked
                 await interaction.response.edit_message(embed=embed, view=toggle_view)
            else: # If initial command /stock
                 await interaction.response.send_message(embed=embed, view=toggle_view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in StockView._show_category: {e}\n{traceback.format_exc()}")
            error_msg = "❌ An error occurred while displaying stock."
            try:
                 if interaction.type == discord.InteractionType.component and original_message:
                      await interaction.response.edit_message(content=error_msg, embed=None, view=None)
                 elif not interaction.response.is_done():
                      await interaction.response.send_message(error_msg, ephemeral=True)
                 else: # Fallback if response already sent/deferred
                      await interaction.followup.send(error_msg, ephemeral=True)
            except Exception: pass # Ignore errors during error reporting


    # Buttons call the helper method
    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green, custom_id="stock_view_bud")
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple, custom_id="stock_view_joint")
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray, custom_id="stock_view_bag")
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, 'bag')

    @discord.ui.button(label="💎 Tebex", style=discord.ButtonStyle.primary, row=1, custom_id="stock_view_tebex")
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, 'tebex')
        
    @discord.ui.button(label="🐟 Fish", style=discord.ButtonStyle.primary, row=2, custom_id="stock_view_fish")
    async def fish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, 'fish')
    
    @discord.ui.button(label="🧩 Misc", style=discord.ButtonStyle.primary, row=2, custom_id="stock_view_misc")
    async def misc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, 'misc')    

    @discord.ui.button(label="📊 All Stock", style=discord.ButtonStyle.secondary, row=1, custom_id="stock_view_all") # Changed style
    async def all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, 'all')


class StockViewToggle(discord.ui.View):
    def __init__(self, current_compact_mode: bool):
        super().__init__(timeout=300) # Match parent view timeout
        self.compact_mode = current_compact_mode
        # Update button label based on current mode
        self.toggle_button.label = "Switch to Standard View" if self.compact_mode else "Switch to Compact View"

    # Define button directly in init or define it and add it
    @discord.ui.button(label="Toggle View Mode", style=discord.ButtonStyle.secondary, custom_id="stock_toggle_view") # Initial label is placeholder
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            user = str(interaction.user)
            new_mode = not self.compact_mode
            shop_data.set_user_preference(user, "compact_view", new_mode) # Saves automatically

            # Re-show the stock using the main StockView's helper
            # We need an instance of StockView to call its method
            stock_display_view = StockView()
            # Pass 'all' to show all categories after toggling
            await stock_display_view._show_category(interaction, 'all')

        except Exception as e:
            logger.error(f"Error toggling stock view mode: {e}\n{traceback.format_exc()}")
            # Attempt to respond to the interaction if possible
            try:
                 await interaction.response.edit_message(content="❌ Error changing view mode.", embed=None, view=None)
            except Exception: pass # Ignore further errors


class TemplateEditSelectView(discord.ui.View):
    def __init__(self, user_id_str: str):
        super().__init__(timeout=180)
        self.user_id_str = user_id_str

        select = discord.ui.Select(
            placeholder="Choose a template to edit...",
            min_values=1,
            max_values=1,
            custom_id="template_select_edit"
        )

        templates = shop_data.get_user_templates(self.user_id_str)
        if not templates:
             select.add_option(label="No templates found to edit", value="no_templates_placeholder", default=True)
             select.disabled = True
        else:
             for name in sorted(templates.keys()):
                  select.add_option(label=name, value=name)

        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        # This interaction edits the original message (showing the select) to show the editor
        original_message = interaction.message
        try:
            template_name = interaction.data['values'][0]

            if template_name == "no_templates_placeholder":
                 await interaction.response.edit_message(content="No template selected.", embed=None, view=None)
                 return

            # Verify interacting user? (Optional)
            # if str(interaction.user.id) != self.user_id_str:
            #    await interaction.response.send_message("❌ You cannot use this menu.", ephemeral=True)
            #    return

            user_str = str(interaction.user)
            templates = shop_data.get_user_templates(user_str)

            if template_name not in templates:
                await interaction.response.edit_message(
                    content=f"❌ Template '{template_name}' not found.",
                    embed=None, view=None
                )
                return

            # Initialize the visual category view for editing
            template_view = TemplateVisualCategoryView(template_name)
            template_view.user_id_str = user_str

            # Load the existing template items into the view's selection state
            template_items = templates.get(template_name, {})
            template_view.selected_items = template_items.copy() # Start editor state with saved data

            embed = template_view.create_current_selection_embed() # Build initial embed
            embed.add_field(
                name="Instructions",
                value="1. Click a category button.\n"
                      "2. Click items to set quantities (0 to remove).\n"
                      "3. Use 'Back' to return here.\n"
                      "4. Click 'Finish & Save' when done.",
                inline=False
            )

            await interaction.response.edit_message(
                content=f"Editing template: **{template_name}**",
                embed=embed,
                view=template_view
            )

        except Exception as e:
             logger.error(f"Error in TemplateEditSelectView select_callback: {e}\n{traceback.format_exc()}")
             try:
                  if original_message and not interaction.response.is_done():
                       await interaction.response.edit_message(content="❌ Error opening template editor.", embed=None, view=None)
                  elif interaction.response.is_done():
                       await interaction.followup.send("❌ Error opening template editor.", ephemeral=True)
             except Exception: pass

# --- END OF PASTED UI CLASSES ---


############### DATA CLASS ###############
class ShopData:
    def __init__(self):
        self.items: Dict[str, List[Dict[str, Any]]] = {}
        self.user_earnings: Dict[str, int] = {}
        self.sale_history: List[Dict[str, Any]] = []
        self.stock_message_ids: List[int] = []
        self.user_templates: Dict[str, Dict[str, Dict[str, int]]] = {} # user_id_str: {template_name: {item: qty}}
        self.user_preferences: Dict[str, Dict[str, Any]] = {} # user_id_str: {pref_name: value}
        self.low_stock_thresholds: Dict[str, int] = {} # category: threshold
        self.category_emojis: Dict[str, str] = {} # category: emoji

        # Default values (will be loaded/overwritten from config)
        self._default_thresholds = {'bud': 30, 'joint': 100, 'bag': 100, 'tebex': 10, 'fish': 10, 'misc': 10}
        self._default_emojis = {'bud': '🥦', 'joint': '🚬', 'bag': '🛍️', 'tebex': '💎', 'fish': '🐟', 'misc': '🧩'}

        logger.info(f"🌍 Running in {APP_ENV.upper()} environment")
        logger.info(f"🗄️ Using database: {DB_NAME}")
        logger.info(f"MongoDB URI check: ...@{MONGO_URI.split('@')[-1].split('/')[0]}")

        try:
            logger.info("🔌 Connecting to MongoDB...")
            self.mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
            self.mongo_client.admin.command('ping') # More reliable connection test
            self.db = self.mongo_client[DB_NAME]
            self.using_mongodb = True
            logger.info(f"✅ Connected to MongoDB successfully (Database: {DB_NAME})")
        except pymongo.errors.ConnectionFailure as e:
            logger.critical(f"❌ MongoDB connection failed: {e}")
            logger.critical("💾 Bot requires MongoDB connection to operate.")
            raise RuntimeError(f"MongoDB connection failed: {e}")
        except Exception as e:
            logger.critical(f"❌ MongoDB setup error: {e}\n{traceback.format_exc()}")
            logger.critical("💾 Cannot continue without MongoDB connection.")
            raise RuntimeError(f"MongoDB setup error: {e}")

        # Load display names, prices, categories (these seem relatively static)
        self._load_static_data()

        # Load dynamic data from DB and config
        self.load_data() # Load from MongoDB first
        self.load_config() # Load from JSON, potentially overwriting thresholds/emojis

        self.item_list = list(self.predefined_prices.keys())


    def _load_static_data(self):
         # These could also be moved to a separate JSON/YAML file if they change occasionally
        self.display_names = {
            'bud_sojokush': 'Bizarre Bud', 'bud_khalifakush': 'Strange Bud', 'bud_pineappleexpress': 'Smelly Bud',
            'bud_sourdiesel': 'Sour Diesel Bud', 'bud_whitewidow': 'Whacky Bud', 'bud_ogkush': 'Old Bud',
            'joint_sojokush': 'Bizarre Joint', 'joint_khalifakush': 'Strange Joint', 'joint_pineappleexpress': 'Smelly Joint',
            'joint_sourdiesel': 'Sour Diesel Joint', 'joint_whitewidow': 'Whacky Joint', 'joint_ogkush': 'Old Joint',
            'bagof_sojokush': 'Bizarre Bag', 'bagof_khalifakush': 'Strange Bag', 'bagof_pineappleexpress': 'Smelly Bag',
            'bagof_sourdiesel': 'Sour Diesel Bag', 'bagof_whitewidow': 'Whacky Bag', 'bagof_ogkush': 'Old Bag',
            'tebex_vinplate': 'Stolen Plate', 'tebex_talentreset': 'Talent Reset', 'tebex_deep_pockets': 'Deep Pockets',
            'licenseplate': 'Custom Plate', 'tebex_carwax': 'Car Wax', 'tebex_xpbooster': 'XP Booster',
            'cookedmackerel': 'Cooked Mackerel', 'cookedbass': 'Cooked Bass', 'cookedsalmon': 'Cooked Salmon', 'cookedgrouper': 'Cooked Grouper',
            'makeshiftarmour': 'Makeshift Armour', 'rollingpaper': 'Rolling Paper'
        }
        self.predefined_prices = {
            'bud_sojokush': 5450, 'bud_khalifakush': 1200, 'bud_pineappleexpress': 750, 'bud_sourdiesel': 650,
            'bud_whitewidow': 630, 'bud_ogkush': 795, 'joint_ogkush': 30, 'joint_whitewidow': 30, 'joint_sourdiesel': 35,
            'joint_pineappleexpress': 35, 'joint_khalifakush': 60, 'joint_sojokush': 125, 'bagof_ogkush': 37,
            'bagof_whitewidow': 40, 'bagof_sourdiesel': 43, 'bagof_pineappleexpress': 45, 'bagof_khalifakush': 75,
            'bagof_sojokush': 325, 'tebex_vinplate': 400000, 'tebex_talentreset': 550000, 'tebex_deep_pockets': 1000000,
            'licenseplate': 535000, 'tebex_carwax': 595000, 'tebex_xpbooster': 1450000, 'cookedmackerel': 500, 'cookedbass': 500, 
            'cookedgrouper': 500,
            'cookedsalmon': 600, 'makeshiftarmour': 2750, 'rollingpaper': 20
        }
        self.item_categories = {
            'bud': ['bud_ogkush', 'bud_whitewidow', 'bud_sourdiesel', 'bud_pineappleexpress', 'bud_khalifakush', 'bud_sojokush'],
            'joint': ['joint_ogkush', 'joint_whitewidow', 'joint_sourdiesel', 'joint_pineappleexpress', 'joint_khalifakush', 'joint_sojokush'],
            'bag': ['bagof_ogkush', 'bagof_whitewidow', 'bagof_sourdiesel', 'bagof_pineappleexpress', 'bagof_khalifakush', 'bagof_sojokush'],
            'tebex': ['tebex_vinplate', 'tebex_talentreset', 'tebex_deep_pockets', 'licenseplate', 'tebex_carwax', 'tebex_xpbooster'],
            'fish': ['cookedmackerel', 'cookedbass', 'cookedsalmon', 'cookedgrouper'],
            'misc': ['makeshiftarmour', 'rollingpaper']
        }

    def save_data(self) -> None:
        try:
            # --- Save to MongoDB ---
            # Save items (consider batching updates if performance becomes an issue)
            for item_name, entries in self.items.items():
                # Filter out entries with quantity 0 before saving? Optional.
                valid_entries = [e for e in entries if e.get('quantity', 0) > 0]
                if valid_entries:
                    self.db.items.update_one(
                        {"_id": item_name},
                        {"$set": {"entries": valid_entries}},
                        upsert=True
                    )
                else:
                    # If no valid entries left, remove the item document
                    self.db.items.delete_one({"_id": item_name})

            # Save main settings/collections to the 'settings' collection in MongoDB
            settings_to_save = {
                "user_earnings": self.user_earnings,
                "user_templates": self.user_templates,
                "user_preferences": self.user_preferences,
                # Prices are static here, but if they become dynamic:
                # "predefined_prices": self.predefined_prices
            }
            for key, data in settings_to_save.items():
                 self.db.settings.update_one({"_id": key}, {"$set": {"data": data}}, upsert=True)

            # Save limited sale history (limit size to prevent unbounded growth)
            recent_history = self.sale_history[-1000:] # Keep last 1000 entries
            self.db.settings.update_one(
                {"_id": "sale_history"},
                {"$set": {"data": recent_history}},
                upsert=True
            )
            self.sale_history = recent_history # Update in-memory list to match saved state

            logger.info("💾 Data saved to MongoDB")
        except Exception as e:
            logger.error(f"❌ MongoDB save error: {e}\n{traceback.format_exc()}")
            # In critical failure, maybe attempt a local JSON dump as emergency fallback?
            # self._emergency_local_save()
            raise # Re-raise to indicate failure

    def load_data(self) -> None:
        try:
            # --- Load from MongoDB ---
            # Load items
            self.items = {} # Clear existing memory first
            for item_doc in self.db.items.find():
                item_id = item_doc.get("_id")
                entries = item_doc.get("entries")
                # Basic validation
                if isinstance(item_id, str) and isinstance(entries, list):
                     # Further validation of entries if needed
                     self.items[item_id] = entries

            # Load settings from the 'settings' collection
            settings_keys = ["user_earnings", "user_templates", "user_preferences", "sale_history"]
            for key in settings_keys:
                 doc = self.db.settings.find_one({"_id": key})
                 if doc and "data" in doc:
                      # Load into the correct attribute
                      if key == "user_earnings": self.user_earnings = doc["data"]
                      elif key == "user_templates": self.user_templates = doc["data"]
                      elif key == "user_preferences": self.user_preferences = doc["data"]
                      elif key == "sale_history": self.sale_history = doc["data"]
                      # Add prices here if they become dynamic and stored in DB

            logger.info("📂 Data loaded from MongoDB")

        except Exception as e:
            logger.error(f"❌ MongoDB load error: {e}\n{traceback.format_exc()}")
            # Consider loading from a local emergency backup if DB load fails?
            # self._try_load_emergency_local()
            raise # Re-raise error if critical data cannot be loaded


    def load_config(self) -> None:
        """Load configuration from config.json"""
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                self.stock_message_ids = config.get("stock_message_ids", [])
                # Load thresholds and emojis, falling back to defaults if missing/invalid
                loaded_thresholds = config.get("low_stock_thresholds", self._default_thresholds)
                self.low_stock_thresholds = loaded_thresholds if isinstance(loaded_thresholds, dict) else self._default_thresholds

                loaded_emojis = config.get("category_emojis", self._default_emojis)
                self.category_emojis = loaded_emojis if isinstance(loaded_emojis, dict) else self._default_emojis

                logger.info(f"📂 Config loaded: stock_msg_ids={self.stock_message_ids}, thresholds/emojis loaded.")
        except FileNotFoundError:
            logger.warning(f"📝 Config file '{CONFIG_FILE}' not found. Using defaults and creating file on next save.")
            self.stock_message_ids = None
            self.low_stock_thresholds = self._default_thresholds.copy()
            self.category_emojis = self._default_emojis.copy()
        except json.JSONDecodeError:
             logger.error(f"❌ Error decoding '{CONFIG_FILE}'. Please check its format. Using defaults.")
             self.stock_message_ids = None
             self.low_stock_thresholds = self._default_thresholds.copy()
             self.category_emojis = self._default_emojis.copy()
        except Exception as e:
            logger.error(f"❌ Error loading config '{CONFIG_FILE}': {e}\n{traceback.format_exc()}")
            # Fallback to defaults in case of other errors
            self.stock_message_ids = None
            self.low_stock_thresholds = self._default_thresholds.copy()
            self.category_emojis = self._default_emojis.copy()

    def save_config(self) -> None:
        """Save configuration to config.json"""
        try:
            config_data = {
                "stock_message_ids": self.stock_message_ids,
                "low_stock_thresholds": self.low_stock_thresholds,
                "category_emojis": self.category_emojis
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(config_data, f, indent=2)
            logger.info(f"💾 Config saved to {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"❌ Error saving config '{CONFIG_FILE}': {e}\n{traceback.format_exc()}")

    def get_total_quantity(self, item_name: str) -> int:
        if item_name not in self.items:
            return 0
        # Ensure entries are valid dicts with 'quantity' key
        return sum(entry.get('quantity', 0) for entry in self.items[item_name] if isinstance(entry, dict))

    def get_user_quantity(self, item_name: str, user: str) -> int:
        if item_name not in self.items:
            return 0
        # Ensure entries are valid dicts with 'quantity' and 'person' keys
        return sum(entry.get('quantity', 0) for entry in self.items[item_name]
                   if isinstance(entry, dict) and entry.get('person') == user)

    def get_all_items(self) -> List[str]:
        return self.item_list

    def add_item(self, item_name: str, quantity: int, user: str) -> bool:
        # Assumes item_name is valid and quantity > 0 (checked by callers)
        price = self.predefined_prices.get(item_name, 0)
        date_str = str(datetime.date.today()) # Use consistent date format

        if item_name not in self.items:
            self.items[item_name] = []

        # Append new stock entry
        self.items[item_name].append({
            "person": user,
            "quantity": quantity,
            "date": date_str,
            "price": price # Store the price at time of adding
        })
        # Note: save_data() is called by the command handler after potentially multiple adds
        return True

    def remove_item(self, item_name: str, quantity_to_remove: int, user: str) -> bool:
        """Removes a specific quantity of an item from a user's stock."""
        if item_name not in self.items or quantity_to_remove <= 0:
            return False

        user_entries = [
            (index, entry) for index, entry in enumerate(self.items[item_name])
            if isinstance(entry, dict) and entry.get('person') == user and entry.get('quantity', 0) > 0
        ]

        # Check total available *before* sorting/removing
        total_available = sum(entry['quantity'] for _, entry in user_entries)
        if total_available < quantity_to_remove:
            logger.warning(f"User '{user}' has only {total_available} of {item_name}, tried to remove {quantity_to_remove}")
            return False # Not enough stock

        # Sort user's entries by date (oldest first) to ensure FIFO removal for the user
        user_entries.sort(key=lambda x: x[1].get('date', '9999-99-99'))

        removed_count = 0
        indices_to_update = [] # Store (original_index, new_quantity)

        for original_index, entry in user_entries:
            if removed_count >= quantity_to_remove:
                break

            can_remove_from_this = entry['quantity']
            remove_amount = min(can_remove_from_this, quantity_to_remove - removed_count)

            new_quantity = entry['quantity'] - remove_amount
            indices_to_update.append((original_index, new_quantity))
            removed_count += remove_amount

        if removed_count != quantity_to_remove:
            # This suggests an internal logic error or race condition if total check passed
            logger.error(f"remove_item internal mismatch: Expected {quantity_to_remove}, removed {removed_count} for {item_name} / {user}")
            # Should we revert? For now, proceed but log error.
            # return False # Option to fail the operation entirely

        # Apply the updates to the main items list
        for index, new_qty in indices_to_update:
             self.items[item_name][index]['quantity'] = new_qty

        # Clean up entries with zero quantity (optional, can be done in save_data too)
        # self.items[item_name] = [entry for entry in self.items[item_name] if entry.get('quantity', 0) > 0]
        # Note: save_data() is called by the command handler

        return True # Indicate successful removal attempt


    def is_valid_item(self, item_name: str) -> bool:
        return item_name in self.predefined_prices

    def add_to_history(self, action: str, item: str, quantity: int, price: int, user: str) -> None:
        """Adds an event to the sale/action history."""
        try:
            # Use UTC time for consistency
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            history_entry = {
                "timestamp": timestamp,
                "action": action, # e.g., "add", "remove", "sale", "payout", "set", "clear", "price_change"
                "item": item, # Can be item name, "earnings", "all", etc.
                "quantity": quantity,
                "price": price, # Price per item, or total amount for payout/earnings
                "user": user # Can be user ID string, "customer", "all", etc.
            }
            self.sale_history.append(history_entry)
            # Limit history size in memory immediately after adding
            if len(self.sale_history) > 1100: # Keep slightly more than save limit
                 self.sale_history = self.sale_history[-1000:]
        except Exception as e:
            logger.error(f"Failed to add entry to history: {e}")


    def get_category_for_item(self, item_name: str) -> Optional[str]:
        for category, items in self.item_categories.items():
            if item_name in items:
                return category
        return None

    def is_low_stock(self, item_name: str, quantity: int) -> bool:
        category = self.get_category_for_item(item_name)
        if not category: return False
        threshold = self.low_stock_thresholds.get(category, 0)
        return threshold > 0 and quantity <= threshold # Only trigger if threshold is positive

    def save_template(self, user: str, template_name: str, items: Dict[str, int]) -> bool:
        # This method seems redundant if template saving is handled directly in commands/views
        # Let's keep it for now, assuming it might be used internally
        if user not in self.user_templates:
            self.user_templates[user] = {}
        # Filter out 0 quantity items before saving?
        self.user_templates[user][template_name] = {k: v for k, v in items.items() if v > 0}
        self.save_data() # Should this save all data? Maybe just templates?
        return True

    def get_user_templates(self, user: str) -> Dict[str, Dict[str, int]]:
        return self.user_templates.get(user, {})

    def get_user_preference(self, user: str, preference: str, default: Any = None) -> Any:
        return self.user_preferences.get(user, {}).get(preference, default)

    def set_user_preference(self, user: str, preference: str, value: Any) -> None:
        if user not in self.user_preferences:
            self.user_preferences[user] = {}
        self.user_preferences[user][preference] = value
        self.save_data() # Save immediately when preferences change


# Instantiate ShopData AFTER the class is defined
shop_data = ShopData()

# Instantiate Bot AFTER ShopData might be needed by decorators/UI elements
# (Though typically decorators are evaluated later, it's safer this way)
bot = commands.Bot(command_prefix="!", intents=intents)


############### HELPER FUNCTIONS ###############

async def is_admin(interaction: discord.Interaction) -> bool:
    """Checks if the interaction user has administrator permissions."""
    if not isinstance(interaction.user, discord.Member): # Check in DMs or user left?
        logger.warning(f"is_admin check failed: interaction.user is not a Member object for {interaction.user}")
        return False
    # Use guild_permissions which is reliable
    has_admin = interaction.user.guild_permissions.administrator
    # logger.info(f"Admin check for {interaction.user} in guild {interaction.guild.id}: {has_admin}") # Debug logging
    return has_admin

async def update_stock_message() -> None:
    """Updates the persistent stock message in the designated channel."""
    if not STOCK_CHANNEL_ID:
        # logger.error("Stock channel ID not configured, cannot update message.") # Reduce log noise
        return

    channel = bot.get_channel(STOCK_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        logger.error(f"❌ Cannot find stock channel or invalid channel type: {STOCK_CHANNEL_ID}")
        shop_data.stock_message_id = None # Reset ID if channel invalid
        shop_data.save_config()
        return

    # Check bot permissions in the channel
    perms = channel.permissions_for(channel.guild.me)
    if not perms.send_messages or not perms.read_message_history or not perms.manage_messages:
         logger.error(f"❌ Bot lacks Send Messages, Read History, or Manage Messages permission in channel {STOCK_CHANNEL_ID}")
         return


    messages_content = []
    timestamp = int(datetime.datetime.now().timestamp())
    char_limit = 1950 # Safety margin below 2000

    # Calculate total value before building messages
    total_value = 0
    for item_name in shop_data.get_all_items():
        total_quantity = shop_data.get_total_quantity(item_name)
        if total_quantity > 0:
            price = shop_data.predefined_prices.get(item_name, 0)
            item_value = total_quantity * price
            total_value += item_value
    logger.debug(f"Calculated total_value: {total_value}")

    # Start the message with the stock value at the top
    current_message = f"**📊 Current Shop Stock** (Updated: <t:{timestamp}:R>)\n"
    current_message += f"💰 **Total Stock Value:** ${total_value:,}\n\n"

    sorted_categories = sorted(shop_data.item_categories.items())

    for category, category_items in sorted_categories:
        category_header = f"{shop_data.category_emojis.get(category, '📦')} **{category.upper()}**\n"
        category_block = "```ml\n"
        # Adjust column widths if needed
        category_block += f"{'Item':<18} {'Stock':>7} {'Price':>9} {'Value':>11} Status\n"
        category_block += "─" * (18+7+9+11+8) + "\n" # Adjust separator length

        has_items = False
        category_value = 0
        item_lines = []

        def get_item_order(item_name):
            # Define custom ordering within each category
            category = shop_data.get_category_for_item(item_name)
    
            # Custom order for joints
            if category == 'joint':
                order_map = {
                    'joint_ogkush': 1,      # Old Joint
                    'joint_whitewidow': 2,  # Whacky Joint
                    'joint_sourdiesel': 3,  # Sour Diesel Joint
                    'joint_pineappleexpress': 4, # Smelly Joint
                    'joint_khalifakush': 5, # Strange Joint
                    'joint_sojokush': 6,    # Bizarre Joint
                }
                return order_map.get(item_name, 999)  # Default to end for unknown items
    
            # Custom order for buds
            elif category == 'bud':
                order_map = {
                    'bud_ogkush': 1,      # Old Bud
                    'bud_whitewidow': 2,  # Whacky Bud
                    'bud_sourdiesel': 3,  # Sour Diesel Bud
                    'bud_pineappleexpress': 4, # Smelly Bud
                    'bud_khalifakush': 5, # Strange Bud
                    'bud_sojokush': 6,    # Bizarre Bud
                }
                return order_map.get(item_name, 999)
    
            # Custom order for bags
            elif category == 'bag':
                order_map = {
                    'bagof_ogkush': 1,      # Old Bag
                    'bagof_whitewidow': 2,  # Whacky Bag
                    'bagof_sourdiesel': 3,  # Sour Diesel Bag
                    'bagof_pineappleexpress': 4, # Smelly Bag
                    'bagof_khalifakush': 5, # Strange Bag
                    'bagof_sojokush': 6,    # Bizarre Bag
                }
                return order_map.get(item_name, 999)
    
            # Default to alphabetical sorting by display name for other categories
            else:
                return shop_data.display_names.get(item_name, item_name)

        # Use the custom sort function
        sorted_items = sorted(category_items, key=get_item_order)

        for item_name in sorted_items:
            # No need to check shop_data.items - get_total_quantity handles it
            total_quantity = shop_data.get_total_quantity(item_name)
            if total_quantity > 0:
                has_items = True
                price = shop_data.predefined_prices.get(item_name, 0) # Use 0 if price somehow missing
                item_value = total_quantity * price
                category_value += item_value
                display_name = shop_data.display_names.get(item_name, item_name)
                low_threshold = shop_data.low_stock_thresholds.get(category, 0)

                warning = ""
                if low_threshold > 0:
                    if total_quantity <= low_threshold: warning = "⚠️ LOW"
                    elif total_quantity >= low_threshold * 3: warning = "📈 HIGH"

                formatted_price = f"${price:,}" if price else "N/A"
                formatted_value = f"${item_value:,}" if price else "N/A"
                # Ensure alignment with potentially shorter/longer names
                item_line = f"{display_name[:18]:<18} {total_quantity:>7,} {formatted_price:>9} {formatted_value:>11} {warning}\n"
                item_lines.append(item_line)
            logger.debug(f"Added {item_name} value: {item_value}, category_value now: {category_value}")

        if not has_items:
            category_block += "-- No stock in this category --\n"
        else:
            category_block += "".join(item_lines)

        category_block += "```\n"
        total_value += category_value
        logger.debug(f"Added category {category} value: {category_value}, total_value now: {total_value}")

        # Check if adding this whole category block exceeds limit
        if len(current_message) + len(category_header) + len(category_block) > char_limit:
            # Finish the current message and start a new one
            messages_content.append(current_message)
            current_message = category_header + category_block
        else:
            # Add to the current message
            current_message += category_header + category_block

    messages_content.append(current_message) # Add the last message

    try:
        new_message_ids = []
        # Fetch existing messages with better handling for multiple messages
        old_message_ids = shop_data.stock_message_ids.copy()  # Work with a copy
        old_messages = {}
    
        # Fetch all existing messages
        if old_message_ids:
            for idx, msg_id in enumerate(old_message_ids):
                try:
                    old_message = await channel.fetch_message(msg_id)
                    old_messages[idx] = old_message
                except discord.NotFound:
                    logger.warning(f"Stock message {msg_id} (part {idx+1}) not found.")
                except Exception as e:
                    logger.error(f"Error fetching stock message {msg_id}: {e}")
    
        # Send/edit messages using the cached messages
        for i, content in enumerate(messages_content):
            if i in old_messages:  # Try to update existing message
                try:
                    await old_messages[i].edit(content=content)
                    new_message_ids.append(old_messages[i].id)
                    logger.info(f"Updated stock message part {i+1}/{len(messages_content)}")
                except Exception as e:
                    logger.error(f"Failed to edit stock message part {i+1}: {e}")
                    try:
                        msg = await channel.send(content)
                        new_message_ids.append(msg.id)
                    except Exception:
                        logger.error(f"Also failed to send new message for part {i+1}")
            else:  # Send new message
                try:
                    # Add rate limit handling
                    if i > 0:
                        await asyncio.sleep(1.1)
                    msg = await channel.send(content)
                    new_message_ids.append(msg.id)
                    logger.info(f"Sent new stock message part {i+1}/{len(messages_content)}")
                except Exception as e:
                    logger.error(f"Failed to send stock message part {i+1}: {e}")
    
        # Clean up any extra old messages
        for i in range(len(messages_content), len(old_message_ids)):
            if i in old_messages:
                try:
                    await old_messages[i].delete()
                    logger.info(f"Deleted extra stock message part {i+1}")
                except Exception:
                    logger.warning(f"Failed to delete extra message {old_message_ids[i]}")
    
        # Save the updated message IDs
        if shop_data.stock_message_ids != new_message_ids:
            shop_data.stock_message_ids = new_message_ids
            shop_data.save_config()
            logger.info(f"📝 Updated stock message IDs: {new_message_ids}")
            try:
                old_message = await channel.fetch_message(old_message_id)
            except discord.NotFound:
                logger.warning(f"Stock message {old_message_id} not found. Will send new.")
                shop_data.stock_message_id = None # Clear invalid ID
            except discord.Forbidden:
                logger.error(f"No permission to fetch stock message {old_message_id}.")
                shop_data.stock_message_id = None
            except Exception as fetch_err:
                logger.error(f"Error fetching old stock message {old_message_id}: {fetch_err}")
                shop_data.stock_message_id = None
    except Exception as e:
        logger.error(f"Error updating stock message: {e}")
        return  # Return early on error


async def process_sale(item_name: str, quantity_sold: int, sale_price_per_item: int) -> bool:
    """Processes a sale, removing stock FIFO globally and crediting users."""
    # Ensure item_name is normalized? Assumes caller normalized it.
    display_name = shop_data.display_names.get(item_name, item_name)
    logger.info(f"🛒 PROCESSING SALE: {quantity_sold}x {display_name} @ ${sale_price_per_item:,} each")

    if not shop_data.is_valid_item(item_name):
        logger.error(f"❌ Sale failed: Invalid item '{item_name}'")
        return False

    # Use the ShopData method to get total quantity
    total_stock = shop_data.get_total_quantity(item_name)

    if total_stock < quantity_sold:
        logger.error(f"❌ Sale failed: Not enough stock for {display_name} (Need: {quantity_sold}, Have: {total_stock})")
        return False

    # Get all stock entries for this item, ready for processing
    # Ensure we have a mutable list of dictionaries
    stock_entries = [dict(entry) for entry in shop_data.items.get(item_name, []) if isinstance(entry, dict) and entry.get('quantity', 0) > 0]

    if not stock_entries:
        logger.error(f"❌ Sale failed: No valid stock entries found for {display_name} despite total_stock > 0 (data inconsistency?)")
        return False

    # Sort entries globally by date (oldest first) for FIFO
    stock_entries.sort(key=lambda x: x.get('date', '9999-99-99'))

    remaining_to_sell = quantity_sold
    processed_indices = set() # Track original indices to update/remove later
    earnings_updates: Dict[str, int] = {} # Track earnings per user for this sale

    # Find original indices (needed because we're working on a sorted copy)
    original_indices = {}
    for i, entry in enumerate(shop_data.items.get(item_name, [])):
        # Use consistent attributes to make a key
        if isinstance(entry, dict):
            key = (entry.get('person'), entry.get('date'), entry.get('price'), entry.get('quantity'))
            original_indices[key] = i

    for entry in stock_entries:
        if remaining_to_sell <= 0:
            break

        key = (entry.get('person'), entry.get('date'), entry.get('price'), entry.get('quantity'))
        original_index = original_indices.get(key)
        if original_index is None:
             logger.error(f"Could not find original index for entry: {entry}. Skipping.")
             continue # Should not happen if logic is correct

        sell_amount = min(entry['quantity'], remaining_to_sell)
        user = entry.get('person')
        original_price = entry.get('price', 0) # Price the user stocked it at

        if not user:
             logger.warning(f"Stock entry missing 'person' field: {entry}. Skipping.")
             continue

        logger.info(f"📦 Taking {sell_amount} of {display_name} from {user}'s stock (added {entry.get('date', 'N/A')})")

        # Update quantity in the original list using the index
        shop_data.items[item_name][original_index]['quantity'] -= sell_amount
        processed_indices.add(original_index)

        # Calculate earnings based on the price *they stocked it at*
        sale_value = sell_amount * original_price
        earnings_updates[user] = earnings_updates.get(user, 0) + sale_value
        logger.info(f"💰 Crediting ${sale_value:,} to {user} (Item cost: ${original_price:,})")

        remaining_to_sell -= sell_amount

    # Update global earnings and clean up zero-quantity items
    if remaining_to_sell == 0:
        for user, amount in earnings_updates.items():
            shop_data.user_earnings[user] = shop_data.user_earnings.get(user, 0) + amount

        # Clean up zero quantity entries *after* processing all deductions for this sale
        shop_data.items[item_name] = [
            entry for i, entry in enumerate(shop_data.items.get(item_name, []))
            if entry.get('quantity', 0) > 0
        ]

        # Record the sale in history (using the actual sale price per item)
        shop_data.add_to_history("sale", item_name, quantity_sold, sale_price_per_item, "customer")

        shop_data.save_data() # Save all changes
        await update_stock_message() # Update stock display
        logger.info(f"✅ Sale completed: {quantity_sold}x {display_name}")
        return True
    else:
        # This indicates a logic error if the initial stock check passed
        logger.error(f"❌ Sale logic error: Could not fulfill sale of {quantity_sold}x {display_name}. Remaining needed: {remaining_to_sell}. Rolling back changes for this item is complex, state might be inconsistent.")
        # For simplicity, we don't automatically roll back here, but log the error.
        # A more robust system might store changes temporarily and commit/rollback.
        return False


async def item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for item names based on display name."""
    choices = []
    current_lower = current.lower()
    for item_id in shop_data.item_list:
        display_name = shop_data.display_names.get(item_id, item_id)
        if current_lower in display_name.lower() or current_lower in item_id.lower():
            choices.append(app_commands.Choice(name=display_name, value=item_id))
        if len(choices) >= 25:
            break
    return choices


async def add_stock_internal(
    interaction: discord.Interaction, # Pass interaction for potential error messages
    quantity: int,
    item: str, # Expects internal item_name
    price: Optional[int] = None,
    respond: bool = True # Controls if this function sends the final response
    ) -> bool:
    """Internal function to add stock. Returns success status."""

    # Assumes item is already normalized/validated by caller if needed
    # Assumes quantity > 0 is checked by caller

    # Get price or use default
    if price is None:
        price = shop_data.predefined_prices.get(item)
        if price is None: # Should not happen if is_valid_item passed
             error_msg = f"❌ Internal Error: No price found for valid item '{item}'."
             logger.error(error_msg)
             # Send error via the interaction passed to this function
             await interaction.followup.send(error_msg, ephemeral=True)
             return False

    # Add using ShopData method
    user = str(interaction.user)
    shop_data.add_item(item, quantity, user)
    shop_data.add_to_history("add", item, quantity, price, user)

    # Save and update message are typically handled by the caller *after* all operations
    # shop_data.save_data() # Caller saves
    # await update_stock_message() # Caller updates

    # Send response only if requested (e.g., not called from a modal that edits message)
    if respond:
        display_name = shop_data.display_names.get(item, item)
        embed = discord.Embed(
            title="✅ Stock Added",
            description=f"Added {quantity:,} × {display_name} at ${price:,} each.",
            color=COLORS['SUCCESS']
        )
        # Use followup because the command interaction might have been deferred
        await interaction.followup.send(embed=embed, ephemeral=True)

    return True


# (add_large_quantity function seems less relevant now with bulk add, can be removed or kept)
# Keeping it for now as `/add` still uses it directly
async def add_large_quantity(
    interaction: discord.Interaction,
    quantity: int,
    normalized_item: str,
    price: int
    ) -> bool:
    """Handles confirmation and addition for large quantities."""
    # This function assumes it's called *after* initial checks in the main command.
    # It needs to respond to the interaction.

    # Confirmation View (Simple Yes/No)
    class ConfirmLargeAdd(discord.ui.View):
        def __init__(self, timeout=60):
            super().__init__(timeout=timeout)
            self.confirmed = False

        @discord.ui.button(label="Confirm Add", style=discord.ButtonStyle.danger)
        async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            # Ensure the user clicking confirm is the original command user
            if btn_interaction.user.id != interaction.user.id:
                 await btn_interaction.response.send_message("You cannot confirm this action.", ephemeral=True)
                 return

            self.confirmed = True
            self.stop()
            # Disable buttons after click
            for item in self.children: item.disabled = True
            await btn_interaction.response.edit_message(content="Adding stock...", view=self)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
             if btn_interaction.user.id != interaction.user.id:
                 await btn_interaction.response.send_message("You cannot cancel this action.", ephemeral=True)
                 return
             self.confirmed = False
             self.stop()
             for item in self.children: item.disabled = True
             await btn_interaction.response.edit_message(content="Large quantity addition cancelled.", embed=None, view=self)


    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    embed = discord.Embed(
        title="⚠️ Large Quantity Confirmation",
        description=f"You are about to add **{quantity:,}** × **{display_name}**.\n"
                    f"Total value: ${quantity * price:,}.\n\n"
                    f"**Are you sure?**",
        color=COLORS['WARNING']
    )
    view = ConfirmLargeAdd()

    # Send the confirmation message (use followup as main command might defer)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    await view.wait() # Wait for the user to click a button

    if not view.confirmed:
        # User cancelled, message already updated by the cancel button callback
        return False

    # --- User confirmed ---
    # Add the stock using the main method
    add_success = shop_data.add_item(normalized_item, quantity, str(interaction.user))

    if add_success:
        shop_data.add_to_history("add_large", normalized_item, quantity, price, str(interaction.user)) # Specific action
        shop_data.save_data()
        await update_stock_message()

        confirm_embed = discord.Embed(
            title="✅ Stock Added (Large Quantity)",
            description=f"Added {quantity:,} × {display_name} at ${price:,} each.",
            color=COLORS['SUCCESS']
        )

        # Check for high stock warning after adding
        total_quantity = shop_data.get_total_quantity(normalized_item)
        category = shop_data.get_category_for_item(normalized_item)
        if category:
             threshold = shop_data.low_stock_thresholds.get(category, 0)
             if threshold > 0 and total_quantity >= threshold * 5: # Use higher multiplier for large add warning
                 confirm_embed.add_field(
                     name="⚠️ High Stock Level",
                     value=f"Total stock for {display_name} is now {total_quantity:,}!",
                     inline=False
                 )
        # Update the original confirmation message (which is now showing "Adding stock...")
        # Can't edit the followup directly easily, maybe send a new followup?
        await interaction.followup.send(embed=confirm_embed, ephemeral=True) # Send new message
        return True
    else:
         # Should not happen if add_item works, but handle case
         await interaction.followup.send(f"❌ Failed to add large quantity of {display_name}.", ephemeral=True)
         return False


############### COMMANDS ###############

@bot.tree.command(name="help")
async def help_cmd(interaction: discord.Interaction):
    """Shows available commands and bot information."""
    try:
        is_admin_user = await is_admin(interaction)

        embed = discord.Embed(
            title="🏪 Shop Bot Commands",
            description="Manage your shop inventory and earnings with these commands:",
            color=COLORS['INFO']
        )

        stock_commands = [
            "`/quickadd` - Add items using category buttons",
            "`/add` - Add a specific quantity of an item",
            "`/bulkadd` - Add multiple items via text input",
            "`/bulkadd_visual` - Add multiple items visually by category",
            "`/quickremove` - Remove items using category buttons",
            "`/remove` - Remove a specific quantity of an item",
            "`/bulkremove` - Remove multiple items via text input",
            "`/stock` - View current inventory (total shop stock)"
        ]
        embed.add_field(name="📦 Stock Management", value="\n".join(stock_commands), inline=False)

        template_commands = [
            "`/template create` - Create a new restock template visually",
            "`/template use` - Apply a saved template to add items quickly",
            "`/template list` - View your saved templates",
            "`/template edit` - Edit an existing template visually",
            "`/template delete` - Delete one of your templates"
        ]
        embed.add_field(name="📋 Templates", value="\n".join(template_commands), inline=False)

        finance_commands = [
            "`/earnings` - Check your current earnings balance",
            "`/payout` - Request to cash out your earnings"
        ]
        embed.add_field(name="💰 Financial", value="\n".join(finance_commands), inline=False)

        if is_admin_user:
            admin_commands = [
                "`/setstock` - Set exact stock quantity for a user/item",
                "`/clearstock` - Clear stock for item(s) / user(s)",
                "`/sellmanual` - Manually process a sale from shop stock",
                "`/price` - Change the default price of an item",
                "`/userinfo` - View detailed stock/earnings for any user",
                "`/history` - View recent transaction history",
                "`/analytics` - View basic shop analytics",
                "`/backup` - Create a manual backup to local JSON file",
                "`/dmbackup` - Create a backup and send it to your Discord DMs"
            ]
            embed.add_field(name="⚙️ Admin Commands", value="\n".join(admin_commands), inline=False)

        embed.add_field(
            name="💡 Tips",
            value="• Use tab completion for item names\n"
                  "• Templates are great for regular restocking\n"
                  "• Automatic MongoDB backups run every 4 hours\n"
                  "• `/stock` shows low inventory warnings",
            inline=False
        )
        
        embed.set_footer(text="Bot by NCH • MongoDB Integration • Daily Automatic Backups")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in help command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.response.send_message("❌ Error displaying help.", ephemeral=True)
        except Exception: pass


@bot.tree.command(name="stock")
async def stock_cmd(interaction: discord.Interaction):
    """View current total shop stock levels by category."""
    try:
        # The StockView itself handles the display logic
        await interaction.response.send_message(
            "**📊 Stock Viewer**\nSelect a category or view all:",
            view=StockView(),
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in stock command: {e}\n{traceback.format_exc()}")
        try:
             # Check if already responded
             if not interaction.response.is_done():
                  await interaction.response.send_message("❌ Error displaying stock view.", ephemeral=True)
             # else: await interaction.followup.send("❌ Error displaying stock view.", ephemeral=True) # Followup if deferred? Unlikely here.
        except Exception: pass


@bot.tree.command(name="earnings")
async def check_earnings(interaction: discord.Interaction):
    """Check your current earnings balance."""
    try:
        user = str(interaction.user)
        earnings = shop_data.user_earnings.get(user, 0)

        embed = discord.Embed(title="💰 Your Earnings", color=COLORS['WARNING']) # Gold/Yellow color
        if earnings > 0:
            embed.description = f"Available earnings to `/payout`: **${earnings:,}**"
        else:
            embed.description = "You have no earnings available."
            embed.add_field(
                name="How to Earn?",
                value="Add items using `/add`, `/quickadd`, etc. When items you stocked are sold (automatically via webhook or manually by admin), you earn based on the price you added them at.",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in earnings command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.response.send_message("❌ Error fetching earnings.", ephemeral=True)
        except Exception: pass


@bot.tree.command(name="add")
@app_commands.describe(
    quantity="Amount to add (positive number)",
    item="Item name (use autocomplete or internal name)",
    price="Override default price per item (optional, admin only?)",
    user="Add stock for another user (admin only)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def add_stock(
    interaction: discord.Interaction,
    quantity: int,
    item: str, # item here is the internal name from autocomplete
    price: Optional[int] = None,
    user: Optional[discord.Member] = None
):
    """Add items to stock (yours or others if admin)."""
    # Defer early as it involves DB writes and stock update
    await interaction.response.defer(ephemeral=True)
    try:
        target_user_obj = user if user else interaction.user
        target_user_str = str(target_user_obj)
        is_admin_user = await is_admin(interaction)

        # Permission checks
        if user and not is_admin_user:
            await interaction.followup.send("❌ Only administrators can add stock for other users.", ephemeral=True)
            return
        if price is not None and not is_admin_user:
             await interaction.followup.send("❌ Only administrators can set a custom price.", ephemeral=True)
             return # Or maybe just ignore the price for non-admins? Silently ignoring is bad UX.

        # Validate item (autocomplete provides internal name)
        if not shop_data.is_valid_item(item):
            # This should ideally not happen if autocomplete is used correctly
            await interaction.followup.send(f"❌ Invalid item specified: `{item}`.", ephemeral=True)
            return

        # Validate quantity
        if quantity <= 0:
            await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
            return

        # Handle large quantity confirmation
        # Define threshold based on item type? For now, a general threshold.
        large_qty_threshold = 500 # Example threshold
        if quantity >= large_qty_threshold and not is_admin_user: # Admins can skip confirmation? Or add flag?
            add_success = await add_large_quantity(interaction, quantity, item, price if price is not None else shop_data.predefined_prices.get(item, 0))
            # add_large_quantity handles its own response/followup
            if not add_success:
                 # Cancellation message already sent by add_large_quantity
                 pass
            # Success message sent by add_large_quantity
            return # Exit after handling large quantity


        # --- Regular quantity or admin adding large qty ---
        final_price = price if price is not None else shop_data.predefined_prices.get(item, 0)

        add_success = shop_data.add_item(item, quantity, target_user_str)

        if add_success:
            shop_data.add_to_history("add", item, quantity, final_price, target_user_str)
            shop_data.save_data()
            await update_stock_message()

            display_name = shop_data.display_names.get(item, item)
            embed = discord.Embed(title="✅ Stock Added", color=COLORS['SUCCESS'])
            value = quantity * final_price
            embed.add_field(
                name="Details",
                value=f"```ml\nItem:     {display_name}\nQuantity: {quantity:,}\nPrice:    ${final_price:,}\nValue:    ${value:,}\nUser:     {target_user_obj.display_name} ({target_user_str})```",
                inline=False
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
             # Should not happen if add_item is simple append
             await interaction.followup.send(f"❌ Failed to add stock for {item}.", ephemeral=True)


    except Exception as e:
        logger.error(f"Error in add command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred while adding stock.", ephemeral=True)
        except Exception: pass


@bot.tree.command(name="remove")
@app_commands.describe(
    quantity="Amount to remove from your stock",
    item="Item name (use autocomplete or internal name)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def remove_stock(interaction: discord.Interaction, quantity: int, item: str):
    """Remove items from your personal stock contribution."""
    await interaction.response.defer(ephemeral=True)
    try:
        user = str(interaction.user)
        display_name = shop_data.display_names.get(item, item)

        if not shop_data.is_valid_item(item):
            await interaction.followup.send(f"❌ Invalid item specified: `{item}`.", ephemeral=True)
            return

        if quantity <= 0:
            await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
            return

        # Check if user has enough before attempting removal
        total_user_quantity = shop_data.get_user_quantity(item, user)
        if total_user_quantity < quantity:
            await interaction.followup.send(
                f"❌ You only have {total_user_quantity:,}x {display_name} in stock, cannot remove {quantity:,}.",
                ephemeral=True
            )
            return

        # Use the ShopData method which handles checks and FIFO for the user
        removed_successfully = shop_data.remove_item(item, quantity, user)

        if removed_successfully:
            shop_data.add_to_history("remove", item, quantity, 0, user)
            shop_data.save_data()
            await update_stock_message()

            embed = discord.Embed(title="✅ Stock Removed", color=COLORS['SUCCESS'])
            remaining_total = shop_data.get_total_quantity(item)
            remaining_user = shop_data.get_user_quantity(item, user)
            embed.add_field(
                name="Details",
                value=f"```ml\nItem:      {display_name}\nRemoved:   {quantity:,}\nRemaining (Yours): {remaining_user:,}\nRemaining (Total): {remaining_total:,}```",
                inline=False
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
             # This might happen if stock changed between check and removal (race condition)
             await interaction.followup.send(
                f"❌ Failed to remove {quantity:,}x {display_name}. Stock might have changed.",
                ephemeral=True
            )

    except Exception as e:
        logger.error(f"Error in remove command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred while removing stock.", ephemeral=True)
        except Exception: pass


@bot.tree.command(name="setstock")
@app_commands.checks.has_permissions(administrator=True) # Use built-in check
@app_commands.describe(
    quantity="New total quantity for this user's item",
    item="Item name (use autocomplete or internal name)",
    user="User to set stock for",
    price="Price per item (optional, uses default)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def set_stock(
    interaction: discord.Interaction,
    quantity: int,
    item: str,
    user: discord.Member,
    price: Optional[int] = None
):
    """ADMIN: Set a user's stock for an item, overwriting previous entries."""
    await interaction.response.defer(ephemeral=True)
    try:
        target_user_str = str(user)

        if not shop_data.is_valid_item(item):
            await interaction.followup.send(f"❌ Invalid item specified: `{item}`.", ephemeral=True)
            return

        if quantity < 0:
            await interaction.followup.send("❌ Quantity cannot be negative (use 0 to clear).", ephemeral=True)
            return

        # Determine price
        final_price = price if price is not None else shop_data.predefined_prices.get(item, 0)
        if final_price is None and quantity > 0: # Check price only if adding stock
             await interaction.followup.send(f"❌ Cannot set stock: No price specified and no default found for {item}.", ephemeral=True)
             return

        # Get previous quantity for logging/display
        previous_quantity = shop_data.get_user_quantity(item, target_user_str)

        # Remove existing entries for this specific user and item
        if item in shop_data.items:
            shop_data.items[item] = [
                entry for entry in shop_data.items[item]
                if entry.get('person') != target_user_str
            ]

        # Add the new single entry if quantity > 0
        if quantity > 0:
            if item not in shop_data.items: # Ensure item key exists if list was empty
                 shop_data.items[item] = []
            shop_data.items[item].append({
                "person": target_user_str,
                "quantity": quantity,
                "date": str(datetime.date.today()),
                "price": final_price
            })

        shop_data.add_to_history("set", item, quantity, final_price, target_user_str)
        shop_data.save_data()
        await update_stock_message()

        display_name = shop_data.display_names.get(item, item)
        embed = discord.Embed(
            title="⚙️ Stock Set (Admin)",
            description=f"Set **{display_name}** stock for **{user.display_name}** to **{quantity:,}**.",
            color=COLORS['INFO']
        )
        embed.add_field(name="User", value=f"{user.mention} ({target_user_str})", inline=True)
        embed.add_field(name="Change", value=f"{previous_quantity:,} → {quantity:,}", inline=True)
        if quantity > 0:
             embed.add_field(name="Price Per Item", value=f"${final_price:,}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in setstock command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred while setting stock.", ephemeral=True)
        except Exception: pass

@set_stock.error # Catch permission errors specifically for setstock
async def set_stock_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
     else:
          logger.error(f"Unhandled error in setstock command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="clearstock")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    item="Item to clear (leave empty for all items)",
    user="User to clear stock for (leave empty for all users)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def clear_stock(
    interaction: discord.Interaction,
    item: Optional[str] = None,
    user: Optional[discord.Member] = None
):
    """ADMIN: Clear stock entries (use with caution!)."""
    await interaction.response.defer(ephemeral=True)
    try:
        target_user_str = str(user) if user else None
        cleared_items = []
        cleared_users = "all users" if not target_user_str else f"user {user.display_name}"

        embed = discord.Embed(title="🗑️ Stock Cleared (Admin)", color=COLORS['ERROR']) # Red for destructive action

        if item:
            # Clear specific item
            if not shop_data.is_valid_item(item):
                 await interaction.followup.send(f"❌ Invalid item specified: `{item}`.", ephemeral=True)
                 return
            display_name = shop_data.display_names.get(item, item)

            if item in shop_data.items:
                original_entries = shop_data.items[item]
                if target_user_str:
                    shop_data.items[item] = [e for e in original_entries if e.get('person') != target_user_str]
                else:
                    shop_data.items[item] = [] # Clear all for this item

                # Remove item key entirely if list becomes empty
                if not shop_data.items[item]:
                     del shop_data.items[item]

                cleared_items.append(display_name)
                embed.description = f"Cleared **{display_name}** stock for **{cleared_users}**."
                shop_data.add_to_history("clear", item, 0, 0, target_user_str if target_user_str else "all")
            else:
                embed.description = f"Item **{display_name}** already had no stock entries."
                embed.color = COLORS['WARNING']
        else:
            # Clear all items for specified user(s)
            items_to_remove_keys = []
            for item_key, entries in shop_data.items.items():
                if target_user_str:
                    original_count = len(entries)
                    shop_data.items[item_key] = [e for e in entries if e.get('person') != target_user_str]
                    if len(shop_data.items[item_key]) < original_count:
                         cleared_items.append(shop_data.display_names.get(item_key, item_key))
                    if not shop_data.items[item_key]:
                         items_to_remove_keys.append(item_key) # Mark for deletion if empty
                else:
                    # Clearing all for everyone
                    cleared_items.append(shop_data.display_names.get(item_key, item_key))
                    items_to_remove_keys.append(item_key) # Mark all for deletion

            # Perform deletions
            for key_to_del in items_to_remove_keys:
                 if key_to_del in shop_data.items:
                      del shop_data.items[key_to_del]

            if not cleared_items:
                 embed.description = f"No stock found for **{cleared_users}** to clear."
                 embed.color = COLORS['WARNING']
            elif target_user_str:
                 embed.description = f"Cleared all stock entries ({len(cleared_items)} types) for user **{user.display_name}**."
            else:
                 embed.description = f"⚠️ Cleared **ALL** stock entries ({len(cleared_items)} types) for **ALL** users!"

            shop_data.add_to_history("clear", "all_items", 0, 0, target_user_str if target_user_str else "all")


        # Save and update only if changes were made
        if cleared_items:
            shop_data.save_data()
            await update_stock_message()

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in clearstock command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred while clearing stock.", ephemeral=True)
        except Exception: pass

@clear_stock.error # Catch permission errors
async def clear_stock_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
     else:
          logger.error(f"Unhandled error in clearstock command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="quickadd")
async def quick_add(interaction: discord.Interaction):
    """Add items to your stock using category buttons."""
    try:
        # The CategoryView handles the button logic and showing ItemView
        await interaction.response.send_message(
            "**📦 Quick Stock Addition**\nSelect a category:",
            view=CategoryView(),
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in quickadd command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error opening quick add menu.", ephemeral=True)
        except Exception: pass


@bot.tree.command(name="history")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(limit="Number of entries to show (max 50)")
async def view_history(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 15):
    """ADMIN: View recent transaction/action history."""
    await interaction.response.defer(ephemeral=True)
    try:
        embed = discord.Embed(
            title=f"📜 Transaction History (Last {limit})",
            color=COLORS['INFO'],
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        if not shop_data.sale_history:
            embed.description = "No history recorded yet."
        else:
            # Get the most recent 'limit' entries
            recent_history = shop_data.sale_history[-limit:]
            description_lines = []

            for entry in reversed(recent_history): # Show newest first
                try: # Add try-except for individual entry processing
                    ts_str = entry.get("timestamp", "Unknown Time")
                    # Attempt to parse timestamp for relative time
                    try:
                        ts_dt = datetime.datetime.fromisoformat(ts_str)
                        # Convert to Unix timestamp for Discord relative time
                        ts_unix = int(ts_dt.timestamp())
                        time_display = f"<t:{ts_unix}:R>" # Relative time
                    except (ValueError, TypeError):
                        time_display = ts_str[:16] # Fallback to short string

                    action = entry.get("action", "unknown").replace('_', ' ').title()
                    item = entry.get("item", "?")
                    display_item = shop_data.display_names.get(item, item) if item not in ["all_items", "earnings", "all"] else item
                    quantity = entry.get("quantity", 0)
                    price = entry.get("price", 0)
                    user = entry.get("user", "?")
                    user_display = f"<@{user}>" if user.isdigit() else user # Attempt to mention if ID

                    line = f"**{action}** [{time_display}]"
                    details = []
                    if item != "all_items": details.append(f"Item: *{display_item}*")
                    if quantity != 0: details.append(f"Qty: {quantity:,}")
                    if price != 0: details.append(f"Price/Val: ${price:,}")
                    if user not in ["customer", "all"]: details.append(f"User: {user_display}")

                    line += " - " + " | ".join(details)
                    description_lines.append(line)

                except Exception as entry_e:
                     logger.error(f"Error processing history entry {entry}: {entry_e}")
                     description_lines.append(f"Error processing entry: {entry.get('timestamp', 'N/A')}")


            embed.description = "\n".join(description_lines)
            if not description_lines: # If all entries failed processing
                 embed.description = "Error processing history entries."

        embed.set_footer(text=f"Total Entries: {len(shop_data.sale_history)}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in history command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred while fetching history.", ephemeral=True)
        except Exception: pass

@view_history.error # Catch permission errors
async def view_history_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          # Check if response already sent (e.g. by defer)
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
          else:
               await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)

     else:
          logger.error(f"Unhandled error in history command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="sellmanual")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    quantity="Amount sold (positive number)",
    item="Item name (use autocomplete or internal name)",
    price="Sale price per item (uses default if not set)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def manual_sell(
    interaction: discord.Interaction,
    quantity: int,
    item: str,
    price: Optional[int] = None
):
    """ADMIN: Manually process a sale, deducting stock and crediting users."""
    await interaction.response.defer(ephemeral=True)
    try:
        if not shop_data.is_valid_item(item):
            await interaction.followup.send(f"❌ Invalid item specified: `{item}`.", ephemeral=True)
            return

        if quantity <= 0:
            await interaction.followup.send("❌ Quantity must be positive.", ephemeral=True)
            return

        display_name = shop_data.display_names.get(item, item)
        current_stock = shop_data.get_total_quantity(item) # Get stock before sale

        # Use default price if none provided, ensure it exists
        sale_price = price
        if sale_price is None:
            sale_price = shop_data.predefined_prices.get(item)
            if sale_price is None:
                await interaction.followup.send(f"❌ Cannot sell: No price specified and no default found for {display_name}.", ephemeral=True)
                return

        if sale_price < 0: # Price should likely be non-negative
             await interaction.followup.send(f"❌ Sale price cannot be negative.", ephemeral=True)
             return


        # Check stock *before* calling process_sale (process_sale checks again, but good practice)
        if current_stock < quantity:
             await interaction.followup.send(f"❌ Not enough stock for {display_name}. Need {quantity:,}, Have {current_stock:,}.", ephemeral=True)
             return

        # Call the sale processing logic
        success = await process_sale(item, quantity, sale_price)

        if success:
            new_stock = shop_data.get_total_quantity(item) # Get stock after sale
            total_sale_value = quantity * sale_price
            embed = discord.Embed(title="✅ Manual Sale Processed (Admin)", color=COLORS['SUCCESS'])
            embed.add_field(
                name="Sale Details",
                value=f"```ml\nItem:     {display_name}\nQuantity: {quantity:,}\nPrice Ea: ${sale_price:,}\nTotal:    ${total_sale_value:,}```",
                inline=False
            )
            embed.add_field(name="Stock Change", value=f"{current_stock:,} → {new_stock:,}", inline=False)
            embed.set_footer(text=f"Stock removed FIFO, user earnings credited based on their add price.")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # process_sale should have logged details, provide generic error here
            await interaction.followup.send(
                f"❌ Failed to process manual sale for {display_name}. Check logs for details (likely insufficient stock).",
                color=COLORS['ERROR'],
                ephemeral=True
            )

    except Exception as e:
        logger.error(f"Error in sellmanual command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred during manual sale.", ephemeral=True)
        except Exception: pass

@manual_sell.error # Catch permission errors
async def manual_sell_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
          else:
               await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
     else:
          logger.error(f"Unhandled error in sellmanual command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="quickremove")
async def quick_remove(interaction: discord.Interaction):
    """Remove items from your stock using category buttons."""
    try:
        # The RemoveCategoryView handles the logic
        await interaction.response.send_message(
            "**🗑️ Quick Stock Removal**\nSelect a category:",
            view=RemoveCategoryView(),
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in quickremove command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error opening quick remove menu.", ephemeral=True)
        except Exception: pass


@bot.tree.command(name="userinfo")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(user="User to view information for")
async def user_info(interaction: discord.Interaction, user: discord.Member):
    """ADMIN: View a user's stock contributions and earnings."""
    await interaction.response.defer(ephemeral=True)
    try:
        target_user_str = str(user)

        embed = discord.Embed(
            title=f"👤 User Info: {user.display_name}",
            color=COLORS['INFO'],
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user.mention} ({target_user_str})", inline=False)

        # Get earnings
        earnings = shop_data.user_earnings.get(target_user_str, 0)
        embed.add_field(name="💰 Available Earnings", value=f"${earnings:,}", inline=True)

        # Get stock information
        stock_details = []
        total_stock_value = 0
        total_item_count = 0

        # Iterate through all known items to check user's quantity
        for item_name in shop_data.item_list:
             user_qty = shop_data.get_user_quantity(item_name, target_user_str)
             if user_qty > 0:
                  price = shop_data.predefined_prices.get(item_name, 0) # Use current price for value estimate
                  value = user_qty * price
                  total_stock_value += value
                  total_item_count += user_qty
                  display_name = shop_data.display_names.get(item_name, item_name)
                  stock_details.append(f"{display_name}: {user_qty:,} (~${value:,})") # Indicate value is estimate

        embed.add_field(name="📊 Total Stock Value (Est.)", value=f"${total_stock_value:,}", inline=True)
        embed.add_field(name="📦 Total Items Stocked", value=f"{total_item_count:,}", inline=True)


        if stock_details:
            stock_str = "```\n" + "\n".join(sorted(stock_details)) + "```"
            # Handle splitting if too long
            if len(stock_str) > 1024:
                 split_point = len(stock_details) // 2
                 part1 = "```\n" + "\n".join(sorted(stock_details)[:split_point]) + "```"
                 part2 = "```\n" + "\n".join(sorted(stock_details)[split_point:]) + "```"
                 embed.add_field(name="📈 Stock Contributions (Part 1)", value=part1, inline=False)
                 embed.add_field(name="📈 Stock Contributions (Part 2)", value=part2, inline=False)
            else:
                 embed.add_field(name="📈 Stock Contributions", value=stock_str, inline=False)
        else:
            embed.add_field(name="📈 Stock Contributions", value="No items currently stocked by this user.", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in userinfo command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred fetching user info.", ephemeral=True)
        except Exception: pass

@user_info.error # Catch permission errors
async def user_info_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
          else:
               await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
     else:
          logger.error(f"Unhandled error in userinfo command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="payout")
@app_commands.describe(amount="Amount to cash out (e.g., 50000 or 'all')")
async def payout(interaction: discord.Interaction, amount: str):
    """Cash out your available earnings."""
    await interaction.response.defer(ephemeral=True)
    try:
        user = str(interaction.user)
        current_balance = shop_data.user_earnings.get(user, 0)

        if current_balance <= 0:
            embed = discord.Embed(title="ℹ️ No Earnings", description="You have no earnings available to cash out.", color=COLORS['INFO'])
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            if amount.lower() == 'all':
                payout_amount = current_balance
            else:
                # Remove commas, allow decimals? For now, assume integer currency.
                payout_amount = int(re.sub(r'[,\s]', '', amount))

            if payout_amount <= 0:
                raise ValueError("Amount must be positive")

            if payout_amount > current_balance:
                embed = discord.Embed(
                    title="⚠️ Insufficient Balance",
                    description=f"You only have **${current_balance:,}** available.\nCannot cash out ${payout_amount:,}.",
                    color=COLORS['WARNING']
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Process payout
            shop_data.user_earnings[user] = current_balance - payout_amount
            shop_data.add_to_history("payout", "earnings", payout_amount, 0, user) # Store amount paid out
            shop_data.save_data()

            embed = discord.Embed(title="💸 Payout Processed", color=COLORS['SUCCESS'])
            embed.add_field(
                name="Details",
                value=f"```ml\nAmount Cashed Out: ${payout_amount:,}\nRemaining Balance: ${shop_data.user_earnings[user]:,}```",
                inline=False
            )
            embed.set_footer(text="Payout recorded. Ensure you receive the funds through appropriate channels.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"💰 Payout processed for {user}: ${payout_amount:,}")

            # Notify admins for large payouts? Threshold needs consideration.
            large_payout_threshold = 1000000 # Example
            if payout_amount >= large_payout_threshold:
                 # Send notification to admins (implement helper function if needed)
                 logger.info(f"Large payout alert: {user} cashed out ${payout_amount:,}")
                 # await notify_admins(f"💰 Large Payout: {interaction.user.mention} cashed out ${payout_amount:,}")


        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Amount",
                description="Please enter a valid positive number or 'all'.",
                color=COLORS['ERROR']
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in payout command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred during payout.", ephemeral=True)
        except Exception: pass


# Group for template commands
template_group = app_commands.Group(name="template", description="Manage your restock templates")

@template_group.command(name="create")
async def template_create(interaction: discord.Interaction):
    """Create a new restock template using the visual editor."""
    try:
        # Show modal to get the template name first
        await interaction.response.send_modal(TemplateNameModal(is_edit=False))
        # The modal's on_submit will then launch the TemplateVisualCategoryView
    except Exception as e:
        logger.error(f"Error in template create command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error starting template creation.", ephemeral=True)
        except Exception: pass

@template_group.command(name="use")
async def template_use(interaction: discord.Interaction):
    """Apply a saved template to quickly add items to your stock."""
    try:
        user_str = str(interaction.user)
        templates = shop_data.get_user_templates(user_str)

        if not templates:
            await interaction.response.send_message(
                "❌ You have no templates. Use `/template create` first.",
                ephemeral=True
            )
            return

        # Pass user ID string to the view
        await interaction.response.send_message(
            "Select a template to apply:",
            view=TemplateSelectView(user_str),
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in template use command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error loading templates.", ephemeral=True)
        except Exception: pass

@template_group.command(name="list")
async def template_list(interaction: discord.Interaction):
    """View your saved restock templates."""
    try:
        user_str = str(interaction.user)
        templates = shop_data.get_user_templates(user_str)

        if not templates:
            await interaction.response.send_message(
                "❌ You have no templates. Use `/template create` first.",
                ephemeral=True
            )
            return

        embed = discord.Embed(title="📋 Your Templates", color=COLORS['INFO'])
        template_details = []

        for name, items in sorted(templates.items()): # Sort by name
            total_quantity = 0
            template_value = 0
            valid_item_count = 0
            for item, qty in items.items():
                 if shop_data.is_valid_item(item) and qty > 0:
                      valid_item_count += 1
                      total_quantity += qty
                      template_value += qty * shop_data.predefined_prices.get(item, 0)

            if valid_item_count > 0: # Only list templates with valid items
                 template_details.append(
                     f"**{name}**: {valid_item_count} items ({total_quantity:,} total) - Value: ${template_value:,}"
                 )
            else:
                 template_details.append(f"**{name}**: (Empty or contains only invalid/zero quantity items)")


        if not template_details:
             embed.description = "You have templates saved, but they appear to be empty or invalid."
        else:
             embed.description = "\n".join(template_details)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in template list command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.response.send_message("❌ Error listing templates.", ephemeral=True)
        except Exception: pass

@template_group.command(name="delete")
async def template_delete(interaction: discord.Interaction):
    """Delete one of your saved templates."""
    try:
        user_str = str(interaction.user)
        templates = shop_data.get_user_templates(user_str)

        if not templates:
            await interaction.response.send_message(
                "❌ You have no templates to delete.",
                ephemeral=True
            )
            return

        view = TemplateDeleteView()
        await view.setup_for_user(user_str) # Populate select options

        await interaction.response.send_message(
            "Select a template to **permanently delete**:",
            view=view,
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in template delete command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error preparing template deletion.", ephemeral=True)
        except Exception: pass

@template_group.command(name="edit")
async def template_edit(interaction: discord.Interaction):
    """Edit an existing restock template using the visual editor."""
    try:
        user_str = str(interaction.user)
        templates = shop_data.get_user_templates(user_str)

        if not templates:
            await interaction.response.send_message(
                "❌ You have no templates to edit. Use `/template create` first.",
                ephemeral=True
            )
            return

        # Show template selection view
        view = TemplateEditSelectView(user_str)
        await interaction.response.send_message(
            "Select a template to edit:",
            view=view,
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in template edit command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error loading templates for editing.", ephemeral=True)
        except Exception: pass

# Add the group to the bot's command tree
bot.tree.add_command(template_group)


@bot.tree.command(name="price")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    item="Item name (use autocomplete or internal name)",
    new_price="New default price for the item (must be positive)",
    update_existing="Update price for items already in stock? (Default: No)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def change_price(
    interaction: discord.Interaction,
    item: str,
    new_price: app_commands.Range[int, 1, None], # Ensure positive price
    update_existing: bool = False
):
    """ADMIN: Change the default price of an item."""
    await interaction.response.defer(ephemeral=True)
    try:
        if not shop_data.is_valid_item(item):
            await interaction.followup.send(f"❌ Invalid item specified: `{item}`.", ephemeral=True)
            return

        display_name = shop_data.display_names.get(item, item)
        old_price = shop_data.predefined_prices.get(item, "N/A")

        # Update the predefined price dictionary
        shop_data.predefined_prices[item] = new_price

        updated_stock_count = 0
        if update_existing and item in shop_data.items:
            for entry in shop_data.items[item]:
                if isinstance(entry, dict): # Basic type check
                    entry["price"] = new_price # Update the stored price
                    updated_stock_count += entry.get("quantity", 0)

        # Save potentially changed prices and updated stock entries
        shop_data.save_data() # Save everything for simplicity, includes price dict if it were dynamic
        # Prices are static for now, so save_data mainly saves stock changes if update_existing=True
        # Need to decide if predefined_prices should be saved to DB/Config if they change via command

        await update_stock_message()

        embed = discord.Embed(title="⚙️ Price Updated (Admin)", color=COLORS['SUCCESS'])
        embed.add_field(name="Item", value=display_name, inline=True)
        embed.add_field(name="Price Change", value=f"${old_price:,} → ${new_price:,}", inline=True)

        if update_existing:
            embed.add_field(name="Existing Stock", value=f"Updated price for {updated_stock_count:,} existing items in stock.", inline=False)
        else:
            embed.add_field(name="Existing Stock", value="Price for existing items in stock remains unchanged.", inline=False)
        embed.set_footer(text="Future stock additions will use the new default price.")

        shop_data.add_to_history("price_change", item, new_price, 0, str(interaction.user)) # Store new price in 'quantity' field for history

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in price command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred while changing the price.", ephemeral=True)
        except Exception: pass

@change_price.error # Catch permission errors
async def change_price_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
          else:
               await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
     # Catch Range errors too
     elif isinstance(error, app_commands.errors.RangeError):
          await interaction.followup.send(f"❌ Invalid price: {error}", ephemeral=True)
     else:
          logger.error(f"Unhandled error in price command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="analytics")
@app_commands.checks.has_permissions(administrator=True)
async def analytics(interaction: discord.Interaction):
    """ADMIN: View basic shop analytics."""
    await interaction.response.defer(ephemeral=True)
    try:
        embed = discord.Embed(title="📊 Shop Analytics", color=COLORS['INFO'])

        # --- Inventory Stats ---
        total_value = 0
        total_items_count = 0
        item_counts_stock = {}
        for item, entries in shop_data.items.items():
            qty = sum(entry.get('quantity', 0) for entry in entries if isinstance(entry, dict))
            if qty > 0:
                 total_items_count += qty
                 price = shop_data.predefined_prices.get(item, 0)
                 total_value += qty * price
                 item_counts_stock[item] = qty

        embed.add_field(name="Total Inventory Value", value=f"${total_value:,}", inline=True)
        embed.add_field(name="Total Items in Stock", value=f"{total_items_count:,}", inline=True)
        embed.add_field(name="Unique Item Types", value=f"{len(item_counts_stock)}", inline=True)


        # --- Sales Data (from history) ---
        sales_history = [e for e in shop_data.sale_history if e.get("action") == "sale"]
        # Analyze recent period, e.g., last 30 days or last 100 sales
        recent_sales = sales_history[-100:] # Analyze last 100 sales
        sales_volume = 0
        revenue = 0
        item_counts_sold = {}
        start_date = None
        end_date = datetime.datetime.now(datetime.timezone.utc)

        if recent_sales:
            try: # Get timeframe
                 start_ts_str = recent_sales[0].get("timestamp")
                 start_date = datetime.datetime.fromisoformat(start_ts_str) if start_ts_str else None
            except: pass

            for entry in recent_sales:
                 qty = entry.get("quantity", 0)
                 price = entry.get("price", 0) # Sale price per item
                 item = entry.get("item")
                 sales_volume += qty
                 revenue += qty * price
                 if item:
                      item_counts_sold[item] = item_counts_sold.get(item, 0) + qty


        timeframe_str = f"Last {len(recent_sales)} sales"
        if start_date:
             timeframe_str += f" (Since <t:{int(start_date.timestamp())}:D>)"

        embed.add_field(name=f"Sales Volume ({timeframe_str})", value=f"{sales_volume:,} items", inline=True)
        embed.add_field(name=f"Revenue ({timeframe_str})", value=f"${revenue:,}", inline=True)
        # Add Profit calculation here if entry['price'] stored cost price?

        # Top Selling Items
        top_items_sold = sorted(item_counts_sold.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_items_sold:
            embed.add_field(
                name="Top Selling Items (Recent)",
                value="```\n" + "\n".join(f"{shop_data.display_names.get(item, item)}: {count:,}" for item, count in top_items_sold) + "```",
                inline=False
            )

        # Top Stocked Items
        top_items_stock = sorted(item_counts_stock.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_items_stock:
             embed.add_field(
                 name="Top Stocked Items (Current)",
                 value="```\n" + "\n".join(f"{shop_data.display_names.get(item, item)}: {count:,}" for item, count in top_items_stock) + "```",
                 inline=False
             )

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in analytics command: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send("❌ An unexpected error occurred generating analytics.", ephemeral=True)
        except Exception: pass

@analytics.error # Catch permission errors
async def analytics_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
          else:
               await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
     else:
          logger.error(f"Unhandled error in analytics command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="backup")
@app_commands.checks.has_permissions(administrator=True)
async def backup_data(interaction: discord.Interaction):
    """ADMIN: Create a manual backup of shop data to a local JSON file."""
    await interaction.response.defer(ephemeral=True)
    try:
        backup_data_content = {}
        # Backup items
        backup_data_content["items"] = {}
        for item_doc in shop_data.db.items.find():
            if "_id" in item_doc and "entries" in item_doc:
                backup_data_content["items"][item_doc["_id"]] = item_doc["entries"]

        # Backup settings collection
        backup_data_content["settings"] = {}
        for setting_doc in shop_data.db.settings.find():
            if "_id" in setting_doc and "data" in setting_doc:
                backup_data_content["settings"][setting_doc["_id"]] = setting_doc["data"]

        # Backup config file content too
        try:
             with open(CONFIG_FILE, "r") as f:
                  backup_data_content["config_file"] = json.load(f)
        except Exception as conf_e:
             logger.warning(f"Could not read config file for backup: {conf_e}")
             backup_data_content["config_file"] = {"error": f"Could not read {CONFIG_FILE}"}

        # Create backup filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Save backups to a dedicated 'backups' subfolder?
        backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
        os.makedirs(backup_dir, exist_ok=True) # Create folder if it doesn't exist
        backup_filename = os.path.join(backup_dir, f"manual_backup_{DB_NAME}_{timestamp}.json")

        # Write backup file
        with open(backup_filename, "w") as dest:
            json.dump(backup_data_content, dest, indent=2)

        logger.info(f"Manual backup created successfully: {backup_filename}")
        await interaction.followup.send(
            f"✅ Manual backup created: `{os.path.basename(backup_filename)}`\n"
            f"(Check the 'backups' folder next to the bot script)",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Manual backup failed: {e}\n{traceback.format_exc()}")
        try:
            await interaction.followup.send(f"❌ Backup failed: {e}", ephemeral=True)
        except Exception: pass

@backup_data.error # Catch permission errors
async def backup_data_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
          else:
               await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
     else:
          logger.error(f"Unhandled error in backup command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


@bot.tree.command(name="bulkremove", description="Remove multiple items from your stock via text input.")
@app_commands.guild_only()
async def bulk_remove_cmd(interaction: discord.Interaction): # Renamed function
    """Remove multiple items from your stock contribution via text input."""
    try:
        # Show the modal defined earlier
        await interaction.response.send_modal(BulkRemoveModal())
    except Exception as e:
        logger.error(f"Error in bulkremove command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                 await interaction.response.send_message("❌ Error opening bulk remove form.", ephemeral=True)
        except Exception: pass


# Renamed command from bulkadd2 for clarity
@bot.tree.command(name="bulkadd_visual", description="Add multiple items to stock visually (by category).")
@app_commands.guild_only()
@app_commands.describe(category="Category of items to add")
# Use choices based on item_categories keys
@app_commands.choices(category=[
    app_commands.Choice(name=cat.title(), value=cat) for cat in ShopData().item_categories.keys() # Temp instance OK here
])
async def bulk_add_visual(interaction: discord.Interaction, category: app_commands.Choice[str]):
    """Add multiple items to your stock contribution using visual selection."""
    try:
        # Show the BulkAddView defined earlier
        view = BulkAddView(category.value) # Pass category value
        await interaction.response.send_message(
            f"Select items from **{category.name}** to add to stock:", # Use choice name
            view=view,
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in bulkadd_visual command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                 await interaction.response.send_message(f"❌ Error opening bulk add menu for {category.name}.", ephemeral=True)
        except Exception: pass


@bot.tree.command(name="bulkadd", description="Add multiple items to stock via text input.")
@app_commands.guild_only()
async def bulk_add_text(interaction: discord.Interaction):
     """Add multiple items to your stock contribution via text input."""
     try:
        # Show the modal defined earlier
        await interaction.response.send_modal(BulkAddModal())
     except Exception as e:
        logger.error(f"Error in bulkadd command: {e}\n{traceback.format_exc()}")
        try:
            if not interaction.response.is_done():
                 await interaction.response.send_message("❌ Error opening bulk add form.", ephemeral=True)
        except Exception: pass

# Add this command function alongside your other @bot.tree.command definitions

@bot.tree.command(name="dmbackup")
@app_commands.checks.has_permissions(administrator=True)
async def dm_backup(interaction: discord.Interaction):
    """ADMIN: Creates a DB backup and sends it to your DMs."""
    await interaction.response.defer(ephemeral=True)

    # Ensure backup directory exists
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create backup directory '{backup_dir}': {e}")
        await interaction.followup.send(f"❌ Failed to create backup directory. Check bot permissions.", ephemeral=True)
        return

    # Generate timestamp for unique filename
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Define path within the backup directory
    backup_filename_base = f"dm_backup_{DB_NAME}_{timestamp}.json"
    temp_backup_path = os.path.join(backup_dir, backup_filename_base)

    logger.info(f"Creating DM backup: {temp_backup_path}")

    try:
        # Export all collections to a structured backup
        backup_data = {}
        backup_data["items"] = {}
        for item_doc in shop_data.db.items.find():
            if "_id" in item_doc and "entries" in item_doc:
                backup_data["items"][item_doc["_id"]] = item_doc["entries"]

        backup_data["settings"] = {}
        for setting_doc in shop_data.db.settings.find():
            if "_id" in setting_doc and "data" in setting_doc:
                backup_data["settings"][setting_doc["_id"]] = setting_doc["data"]

        # Include config file content in DM backup too? Optional but maybe useful.
        try:
             with open(CONFIG_FILE, "r") as f:
                  backup_data["config_file"] = json.load(f)
        except Exception as conf_e:
             logger.warning(f"Could not read config file for DM backup: {conf_e}")
             backup_data["config_file"] = {"error": f"Could not read {CONFIG_FILE}"}

        # Write backup to temporary file
        with open(temp_backup_path, "w") as dest:
            json.dump(backup_data, dest, indent=2)

        # Check file size before attempting to send
        try:
            file_size = os.path.getsize(temp_backup_path)
            # Discord's limit is 25 MiB (25 * 1024 * 1024 bytes)
            if file_size > 25 * 1024 * 1024:
                logger.warning(f"Backup file {temp_backup_path} is too large ({file_size / (1024*1024):.2f} MiB) to send via DM.")
                await interaction.followup.send(
                    f"⚠️ Backup created locally (`{backup_filename_base}` in backups folder), but it's too large ({file_size / (1024*1024):.2f} MiB) to send via DM.",
                    ephemeral=True
                )
                # Don't delete the file if it was too large to send
                return # Stop here, don't try to send or delete

        except OSError as e:
            logger.error(f"Could not get size of backup file {temp_backup_path}: {e}")
            await interaction.followup.send("❌ Error checking backup file size.", ephemeral=True)
            # Clean up the file we created but couldn't check
            try: os.remove(temp_backup_path)
            except OSError: pass
            return

        # Send the file to the user via DM
        try:
            with open(temp_backup_path, "rb") as file:
                await interaction.user.send(
                    f"📦 Requested database backup - {timestamp}",
                    file=discord.File(file, filename=backup_filename_base) # Use base name for upload
                )
            logger.info(f"DM Backup sent to {interaction.user} ({interaction.user.id})")
            await interaction.followup.send("✅ Backup sent to your DM!", ephemeral=True)

        except discord.Forbidden:
             logger.warning(f"Failed to send DM backup to {interaction.user} ({interaction.user.id}): DMs might be blocked.")
             await interaction.followup.send("❌ Could not send backup to your DM. Please check your privacy settings to allow DMs from server members or this bot.", ephemeral=True)
        except Exception as send_e:
             logger.error(f"Failed to send DM backup file {temp_backup_path}: {send_e}\n{traceback.format_exc()}")
             await interaction.followup.send(f"❌ Failed to send backup file via DM: {send_e}", ephemeral=True)

    except Exception as e:
        # Catch errors during data gathering or initial file write
        error_details = traceback.format_exc()
        logger.error(f"DM backup creation failed: {e}\n{error_details}")
        await interaction.followup.send(f"❌ Failed to create backup: {e}", ephemeral=True)

    finally:
        # Clean up temporary file if it exists and wasn't kept due to size
        if os.path.exists(temp_backup_path):
            # Re-check size condition if needed, but safer to just try removing if send was attempted/failed
             try:
                 # Only remove if file size was okay OR send failed for other reasons
                 if 'file_size' not in locals() or file_size <= 25 * 1024 * 1024:
                      os.remove(temp_backup_path)
                      # logger.info(f"Cleaned up temporary backup file: {temp_backup_path}") # Optional log
             except OSError as e:
                 # Log if cleanup fails, but don't bother the user
                 logger.warning(f"Could not remove temporary backup file {temp_backup_path}: {e}")


# Add the error handler for the command
@dm_backup.error
async def dm_backup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     if isinstance(error, app_commands.MissingPermissions):
          # Need to check is_done() because defer happens first
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
          else:
              await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
     else:
          logger.error(f"Unhandled error in dmbackup command: {error}\n{traceback.format_exc()}")
          if not interaction.response.is_done():
               await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
          else:
              try: await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)
              except Exception: pass # Ignore if followup fails

# --- End of dmbackup command code ---

############### EVENT HANDLERS ###############
@bot.event
async def on_message(message: discord.Message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    # --- Webhook Processing ---
    # Be specific about which webhook(s) to process
    # Example: Check webhook ID or author name if possible/consistent
    # if message.webhook_id == EXPECTED_WEBHOOK_ID:
    # Or: if message.webhook_id and "Specific Webhook Name" in message.author.name:
    if message.webhook_id: # General check for any webhook for now
        logger.info(f"📨 Received webhook message (ID: {message.webhook_id}) from '{message.author.name}' in #{message.channel.name}")

        # Get message content (prefer embed description if available)
        message_text = ""
        if message.embeds:
            embed = message.embeds[0]
            if embed.description:
                 message_text = embed.description
            # Optionally check other fields like title or fields if format varies
            # elif embed.title and "Purchase Info" in embed.title: ...
            else: # Fallback to content if embed has no description
                 message_text = message.content
        else:
            message_text = message.content

        # Keyword check (make this configurable?)
        if "[PURCHASE INFO]" not in message_text:
             # If not the expected purchase format, process regular commands if any
             await bot.process_commands(message)
             return # Stop webhook processing

        logger.info("Processing potential purchase webhook...")
        
        logger.info(f"📨 Webhook raw message content: {message_text[:200]}...")

        try:
            # Use regex to extract info - more robust than splitting
            name_pattern = r"(?:Name|Item|Product):\s*\*?\*?(?P<name>[a-zA-Z0-9_]+)\*?\*?"
            amount_pattern = r"(?:Amount|Quantity|Qty|Count):\s*\*?\*?(?P<amount>\d+)\*?\*?"
            profit_pattern = r"(?:Profit|Price|Total|Cost):\s*\*?\*?\$?(?P<profit>[\d,]+(?:\.\d+)?)\*?\*?"

            name_match = re.search(name_pattern, message_text, re.IGNORECASE)
            amount_match = re.search(amount_pattern, message_text, re.IGNORECASE)
            profit_match = re.search(profit_pattern, message_text, re.IGNORECASE)

            item_name_internal = None
            quantity = None
            sale_price = None # Sale price per item

            if name_match:
                 item_name_internal = name_match.group("name").lower().strip()
                 # Further validation if needed (e.g., ensure it's a known item type)
                 if not shop_data.is_valid_item(item_name_internal):
                      logger.warning(f"Webhook item name '{item_name_internal}' is not a valid known item.")
                      # Decide: react error, ignore, or attempt fuzzy match? For now, let process_sale handle it.
                      pass # Let process_sale fail if invalid

            if amount_match:
                 try: quantity = int(amount_match.group("amount"))
                 except ValueError: logger.error("Webhook parsing: Invalid amount format.")
            if profit_match:
                 try:
                      profit_str = profit_match.group("profit").replace(',', '')
                      total_profit = float(profit_str)
                      # Calculate price per item if quantity is known
                      if quantity and quantity > 0:
                           sale_price = int(total_profit / quantity)
                      else: # Cannot determine price per item
                           logger.warning("Webhook parsing: Cannot determine sale price per item (Amount missing or zero).")
                 except ValueError: logger.error("Webhook parsing: Invalid profit format.")


            # Check if all essential info was parsed
            if item_name_internal and quantity and quantity > 0 and sale_price is not None:
                display_name = shop_data.display_names.get(item_name_internal, item_name_internal)
                logger.info(f"📦 Parsed webhook sale: {quantity}x {display_name} ({item_name_internal}) for total profit ${total_profit:,.2f} (~${sale_price:,} each)")

                success = await process_sale(item_name_internal, quantity, sale_price)

                if success:
                    await message.add_reaction("✅") # Success reaction
                else:
                    await message.add_reaction("❌") # Failure reaction (e.g., insufficient stock)
            else:
                # Log specific missing fields
                missing = []
                if not item_name_internal: missing.append("Item Name")
                if not quantity or quantity <= 0: missing.append("Valid Amount")
                if sale_price is None: missing.append("Price (derived from Profit/Amount)")
                logger.error(f"❌ Webhook parsing failed. Missing fields: {', '.join(missing)}. Raw text snippet:\n{message_text[:300]}...")
                await message.add_reaction("❓") # Parsing error reaction

        except Exception as e:
            logger.error(f"❌ Error processing webhook message: {e}\n{traceback.format_exc()}")
            await message.add_reaction("⚠️") # General error reaction

    else:
        # If it's not a webhook message, process potential bot commands
        await bot.process_commands(message)


@bot.event
async def on_ready():
    """Called when the bot is ready and connected."""
    logger.info(f"--- Bot Ready ---")
    logger.info(f"User: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guilds.")
    # Listing guilds can be noisy, remove if not needed for debug
    # for guild in bot.guilds: logger.info(f" - {guild.name} (ID: {guild.id})")
    logger.info(f"-----------------")

    try:
        # Sync commands globally (or specify guilds if needed)
        # Syncing should generally only be needed once or when commands change,
        # but doing it on ready ensures they are available after restarts.
        try:
            synced = await bot.tree.sync()
            logger.info(f"✅ Synced {len(synced)} application commands.")
        except discord.errors.Forbidden:
             logger.error("❌ Failed to sync commands: Bot lacks 'application.commands' scope or permissions.")
        except Exception as e:
            logger.error(f"❌ Failed to sync commands: {e}\n{traceback.format_exc()}")

        # Update stock display after syncing and connecting
        try:
            await update_stock_message()
        except Exception as e:
            logger.error(f"❌ Failed initial stock message update on ready: {e}\n{traceback.format_exc()}")

        logger.info("✅ Bot startup complete.")

    except Exception as e:
        logger.error(f"❌ Error during on_ready tasks: {e}\n{traceback.format_exc()}")


############### AUTO BACKUP ###############

def create_automatic_backup():
    """Creates a timestamped backup of MongoDB data locally and stores a copy in DB."""
    logger.info("Attempting automatic backup...")
    try:
        backup_data_content = {}
        # Backup items
        backup_data_content["items"] = {}
        for item_doc in shop_data.db.items.find():
             # Basic validation
             item_id = item_doc.get("_id")
             entries = item_doc.get("entries")
             if isinstance(item_id, str) and isinstance(entries, list):
                  backup_data_content["items"][item_id] = entries

        # Backup settings collection
        backup_data_content["settings"] = {}
        for setting_doc in shop_data.db.settings.find():
             setting_id = setting_doc.get("_id")
             data = setting_doc.get("data")
             if isinstance(setting_id, str) and data is not None: # Allow various data types
                  backup_data_content["settings"][setting_id] = data

        # Backup config file content
        try:
             with open(CONFIG_FILE, "r") as f:
                  backup_data_content["config_file"] = json.load(f)
        except Exception as conf_e:
             logger.warning(f"Could not read config file for auto backup: {conf_e}")
             backup_data_content["config_file"] = {"error": f"Could not read {CONFIG_FILE}"}


        # Create local backup filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_filename_local = os.path.join(backup_dir, f"auto_backup_{DB_NAME}_{timestamp}.json")

        # Write local backup file
        with open(backup_filename_local, "w") as dest:
            json.dump(backup_data_content, dest, indent=2)
        logger.info(f"🔄 Automatic local backup created: {backup_filename_local}")

        # --- Store backup in MongoDB ---
        try:
             # Use BSON compatible datetime
             backup_timestamp_utc = datetime.datetime.now(datetime.timezone.utc)
             db_backup_entry = {
                 "backup_type": "automatic",
                 "timestamp_utc": backup_timestamp_utc,
                 "database_name": DB_NAME,
                 "local_filename": os.path.basename(backup_filename_local), # Store only filename
                 "data": backup_data_content # Store the actual data
             }
             # Limit size? MongoDB has document size limits (16MB). Check size if data can be huge.
             # import bson
             # data_size = len(bson.encode(db_backup_entry))
             # if data_size > 15 * 1024 * 1024: # Example: limit near 15MB
             #     logger.error("Backup data exceeds MongoDB document size limit. Skipping DB backup.")
             # else:
             shop_data.db.backups.insert_one(db_backup_entry)
             logger.info(f"🔄 Stored automatic backup copy in MongoDB collection 'backups'.")

             # --- Optional: Prune old backups in MongoDB ---
             retention_days = 7 # Keep 7 days of auto backups in DB
             cutoff_date = backup_timestamp_utc - datetime.timedelta(days=retention_days)
             delete_result = shop_data.db.backups.delete_many({
                 "backup_type": "automatic",
                 "timestamp_utc": {"$lt": cutoff_date}
             })
             if delete_result.deleted_count > 0:
                  logger.info(f"Pruned {delete_result.deleted_count} old automatic backups from MongoDB.")

        except Exception as db_backup_e:
             logger.error(f"❌ Failed to store automatic backup in MongoDB: {db_backup_e}\n{traceback.format_exc()}")

        # --- Optional: Prune old local backup files ---
        try:
             local_retention_days = 14 # Keep 14 days locally
             now = time.time()
             cutoff_time = now - (local_retention_days * 86400)
             deleted_count = 0
             for filename in os.listdir(backup_dir):
                  if filename.startswith(f"auto_backup_{DB_NAME}_") and filename.endswith(".json"):
                       file_path = os.path.join(backup_dir, filename)
                       try:
                            file_mod_time = os.path.getmtime(file_path)
                            if file_mod_time < cutoff_time:
                                 os.remove(file_path)
                                 deleted_count += 1
                                 logger.info(f"Deleted old local backup: {filename}")
                       except OSError as rm_err:
                            logger.warning(f"Could not delete old local backup {filename}: {rm_err}")
             if deleted_count > 0:
                  logger.info(f"Pruned {deleted_count} old local backup files.")
        except Exception as prune_e:
             logger.error(f"Error pruning local backup files: {prune_e}")


    except Exception as e:
        logger.error(f"❌ Automatic backup process failed: {e}\n{traceback.format_exc()}")

# Scheduler function
def run_scheduler():
    logger.info("Scheduler thread started.")
    # Schedule daily backup at a specific time (e.g., 3:00 AM local time)
    schedule.every().day.at("03:00").do(create_automatic_backup)
    # Schedule more frequent backups (e.g., every 4 hours)
    schedule.every(4).hours.do(create_automatic_backup)
    logger.info(f"Scheduled daily backup at 03:00 and every 4 hours.")

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
             logger.error(f"Exception in scheduler loop: {e}\n{traceback.format_exc()}")
        # Sleep for a minute before checking again
        time.sleep(60)


############### MAIN EXECUTION ###############
async def main():
    try:
        # Instantiate ShopData early - loads data & connects to DB
        # global shop_data # Not needed if shop_data is defined at module level
        # shop_data = ShopData() # Already instantiated globally

        # Start the scheduler in a background thread
        # Ensure the thread is daemon so it exits when the main program exits
        scheduler_thread = Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        logger.info("🔄 Automatic backup scheduler thread started.")

        # Create an initial backup at startup after data loaded
        logger.info("Performing initial startup backup...")
        create_automatic_backup()

        logger.info("Starting bot connection...")
        await bot.start(TOKEN)

    except KeyboardInterrupt:
        logger.info("Shutdown requested via KeyboardInterrupt.")
    except asyncio.CancelledError:
        logger.info("Shutdown requested via task cancellation.")
    except discord.LoginFailure:
        logger.critical("❌ Invalid token! Please check your BOT_TOKEN in .env")
    except RuntimeError as e: # Catch specific runtime errors like DB connection failure
         logger.critical(f"❌ Runtime Error during startup: {e}")
    except Exception as e:
        logger.critical(f"❌ Unhandled exception during bot execution: {e}\n{traceback.format_exc()}")
    finally:
        logger.info("Initiating bot shutdown sequence...")
        if bot and not bot.is_closed():
            await bot.close()
            logger.info("Discord bot connection closed.")
        # Wait briefly for scheduler thread to potentially finish current task? Not strictly necessary if daemon.
        # scheduler_thread.join(timeout=5) # Optional wait
        logger.info("Bot shutdown complete.")

if __name__ == "__main__":
    logger.info("--- Script Starting ---")
    # Ensure ShopData and bot are instantiated before main runs
    if 'shop_data' not in globals():
         logger.error("ShopData not instantiated globally before main!")
         exit()
    if 'bot' not in globals():
         logger.error("Bot not instantiated globally before main!")
         exit()

    asyncio.run(main())


# **Key Changes Made:**

#1.  **Error Handling (`try...except Exception`)**: Added around the main logic of *all* `@bot.tree.command` functions and relevant UI callbacks/modal submits. Includes logging with `traceback` and sending ephemeral error messages.
#2.  **Webhook Parsing (`on_message`)**: Replaced string splitting with `re.search` using named groups (`?P<name>`) for clarity and robustness. Handles optional `$` and commas in profit. Includes specific logging for parsing success/failure.
#3.  **Config Management (`ShopData`, `load_config`, `save_config`)**: `low_stock_thresholds` and `category_emojis` are now loaded from `config.json` in `ShopData.__init__` (via `load_config`) and saved back using `save_config`. Default values are used if the file or keys are missing.
#4.  **Config Usage**: All parts of the code that previously used the hardcoded `LOW_STOCK_THRESHOLDS` or `CATEGORY_EMOJIS` constants now access them via `shop_data.low_stock_thresholds` and `shop_data.category_emojis`.
#5.  **Backup Improvements**: Added creation of a `backups` subfolder. Added pruning of old *local* backup files. Added storage of backups *within* MongoDB itself (in a `backups` collection) with automatic pruning there too. Included `config.json` content in the backup data.
#6.  **Readability**: Removed many comments like `# Add item`, `# Check quantity`, `# Save data` where the code itself is clear. Kept comments explaining *why* something is done or clarifying complex logic. Added more commas to f-string formatting (`{value:,}`).
#7.  **Minor Fixes/Improvements**: Added `ephemeral=True` consistently to error messages. Improved some embed formatting. Added sorting to template lists and history/stock displays for consistency. Added explicit `isinstance` checks in some UI callbacks. Refined logging messages. Added `Range` check for `/price` command. Added `has_permissions` checks decorator where applicable. Added `.error` handlers for commands with permission checks. Improved connection check in `ShopData`.

# Remember to update your `config.json` as mentioned above to include the `low_stock_thresholds` and `category_emojis` keys. Test the webhook parsing thoroughly with your actual webhook message format.